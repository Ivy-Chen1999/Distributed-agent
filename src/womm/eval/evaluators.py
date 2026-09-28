"""Per-case scoring (R12): coverage and omissions via an LLM judge; grounding and efficiency
deterministic. Infrastructure errors are kept apart from quality failures."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from womm.eval.golden import GoldenCase
from womm.llm.base import LLMBackend, LLMError
from womm.models.base import StrictModel
from womm.models.run import CallUsage, RunResult, RunStatus
from womm.models.system_version import RoleConfig

INFRA_ERRORS = {"auth", "rate_limit", "timeout"}


class ExpectedVerdict(StrictModel):
    expected_id: str
    covered: bool
    impact_id: str | None
    justification: str


class OmissionVerdict(StrictModel):
    omission_id: str
    addressed: bool
    impact_id: str | None
    justification: str


class JudgeOutput(StrictModel):
    expected: list[ExpectedVerdict]
    omissions: list[OmissionVerdict]


class CaseScore(BaseModel):
    case_id: str
    scenario_id: str
    outcome: Literal["scored", "errored"]
    error: str | None = None
    run_status: str | None = None
    coverage: float | None = None
    omissions_addressed: float | None = None
    grounding: float | None = None
    latency_s: float = 0.0
    tokens: int = 0
    cost_usd: float = 0.0
    judge: JudgeOutput | None = None
    judge_error: str | None = None


def infra_error(run: RunResult) -> str | None:
    """Return a reason when the run failed for infrastructure reasons (excluded from aggregates)."""
    kinds = {f.error_kind for f in run.failures}
    # Match LLMError's '[kind]' tag: bare substrings like 'auth' also occur in 'authority'.
    if (
        run.status == RunStatus.failed
        and run.error
        and any(f"[{k}]" in run.error for k in INFRA_ERRORS)
    ):
        return run.error
    if run.status == RunStatus.failed and kinds and kinds <= INFRA_ERRORS:
        return f"all experts failed with infrastructure errors: {sorted(kinds)}"
    return None


def judge_input(case: GoldenCase, run: RunResult) -> str:
    dossier = run.dossier
    impacts = [
        {
            "impact_id": i.impact_id,
            "summary": i.summary,
            "findings": [
                {"affected_actor": f.affected_actor, "mechanism": f.mechanism, "impact": f.impact}
                for f in i.findings
            ],
        }
        for i in (dossier.impacts if dossier else [])
    ]
    expected = [
        {"expected_id": e.expected_id, "affected_actor": e.affected_actor,
         "mechanism": e.mechanism, "impact": e.impact}
        for e in case.expected_impacts
    ]  # fmt: skip
    omissions = [
        {"omission_id": o.omission_id, "description": o.description}
        for o in case.important_omissions
    ]
    return (
        "Dossier impacts:\n" + json.dumps(impacts, indent=1, ensure_ascii=False)
        + "\n\nExpected impacts:\n" + json.dumps(expected, indent=1, ensure_ascii=False)
        + "\n\nImportant omissions:\n" + json.dumps(omissions, indent=1, ensure_ascii=False)
    )  # fmt: skip


def _complete(case: GoldenCase, out: JudgeOutput) -> bool:
    want_e = {e.expected_id for e in case.expected_impacts}
    want_o = {o.omission_id for o in case.important_omissions}
    return {v.expected_id for v in out.expected} == want_e and {
        v.omission_id for v in out.omissions
    } == want_o


async def score_case(
    case: GoldenCase,
    run: RunResult,
    judge_backend: LLMBackend,
    judge_role: RoleConfig,
    judge_prompt: str,
) -> tuple[CaseScore, CallUsage | None]:
    base = CaseScore(
        case_id=case.case_id,
        scenario_id=case.scenario_id,
        outcome="scored",
        run_status=run.status.value,
        latency_s=sum(u.latency_s for u in run.usage),
        tokens=sum(u.input_tokens + u.output_tokens for u in run.usage),
        cost_usd=sum(u.cost_usd or 0 for u in run.usage),
        grounding=run.grounding.rate,
    )
    if reason := infra_error(run):
        return base.model_copy(update={"outcome": "errored", "error": reason}), None
    if run.status == RunStatus.failed or not run.dossier or not run.dossier.impacts:
        # quality failure: nothing to cover
        return base.model_copy(update={"coverage": 0.0, "omissions_addressed": 0.0}), None

    try:
        out, usage = await judge_backend.call(
            "judge", judge_prompt, judge_input(case, run), JudgeOutput, judge_role
        )
    except LLMError as exc:
        if exc.error_kind in INFRA_ERRORS:
            return base.model_copy(update={"outcome": "errored", "error": str(exc)}), exc.usage
        return base.model_copy(update={"judge_error": str(exc)}), exc.usage
    if not _complete(case, out):
        return base.model_copy(update={"judge": out, "judge_error": "judge skipped ids"}), usage

    cov = sum(v.covered for v in out.expected) / len(out.expected)
    om = sum(v.addressed for v in out.omissions) / len(out.omissions) if out.omissions else None
    return base.model_copy(update={"coverage": cov, "omissions_addressed": om, "judge": out}), usage


def aggregate(scores: list[CaseScore]) -> dict:
    """Means over scored cases only; None metrics (judge failures) are skipped, not zeroed."""
    scored = [s for s in scores if s.outcome == "scored"]

    def mean(attr: str) -> float | None:
        vals = [getattr(s, attr) for s in scored if getattr(s, attr) is not None]
        return sum(vals) / len(vals) if vals else None

    return {
        "cases": len(scores),
        "scored": len(scored),
        "errored": len(scores) - len(scored),
        "partial": len(scored) < len(scores) or any(s.coverage is None for s in scored),
        "coverage": mean("coverage"),
        "omissions_addressed": mean("omissions_addressed"),
        "grounding": mean("grounding"),
        "latency_s": mean("latency_s"),
        "tokens": mean("tokens"),
        "cost_usd": mean("cost_usd"),
    }
