"""Per-case scoring (R12): coverage and omissions via an LLM judge; grounding and efficiency
deterministic. Infrastructure errors are kept apart from quality failures."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

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
    run_id: str | None = None
    expert_failures: dict[str, str] = Field(default_factory=dict)
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
    decisions: list[dict] = Field(
        default_factory=list,
        description="Router decisions with a ground-truth relevance label (Jev calibration).",
    )
    trace_id: str | None = None
    trace_url: str | None = None


def infra_error(run: RunResult) -> str | None:
    """Return a reason when the run failed for infrastructure reasons (excluded from aggregates)."""
    kinds = {f.error_kind for f in run.failures}
    if run.synthesis_error and any(f"[{k}]" in run.synthesis_error for k in INFRA_ERRORS):
        return f"synthesis failed with an infrastructure error: {run.synthesis_error}"
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
    """Exactly one verdict per id: duplicates would skew the fractions."""
    got_e = sorted(v.expected_id for v in out.expected)
    got_o = sorted(v.omission_id for v in out.omissions)
    return got_e == sorted(e.expected_id for e in case.expected_impacts) and got_o == sorted(
        o.omission_id for o in case.important_omissions
    )


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
        run_id=run.run_id,
        expert_failures={f.agent: f.error_kind for f in run.failures},
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

    labeled = label_decisions(run, out)
    cov = sum(v.covered for v in out.expected) / len(out.expected)
    om = sum(v.addressed for v in out.omissions) / len(out.omissions) if out.omissions else None
    update = {"coverage": cov, "omissions_addressed": om, "judge": out, "decisions": labeled}
    return base.model_copy(update=update), usage


def label_decisions(run: RunResult, judge: JudgeOutput) -> list[dict]:
    """Ground truth for router relevance: an expert was relevant when at least one of its
    supported findings sits in a dossier impact the judge matched to an expected impact."""
    if not run.dossier:
        return []
    covering = {v.impact_id for v in judge.expected if v.covered and v.impact_id}
    useful_agents = {
        f.agent for i in run.dossier.impacts if i.impact_id in covering for f in i.findings
    }
    return [
        {"subject": d.subject, "decider": d.decider, "probability": d.probability,
         "decision": d.decision, "relevant": d.subject in useful_agents}
        for d in run.decisions if d.decision_point == "router.relevance"
    ]  # fmt: skip


def calibration(scores: list[CaseScore], bins: int = 5) -> dict | None:
    """Brier score and reliability bins over labeled decisions that carry a probability."""
    pts = [
        (d["probability"], 1.0 if d["relevant"] else 0.0)
        for s in scores if s.outcome == "scored" for d in s.decisions
        if d.get("probability") is not None
    ]  # fmt: skip
    if not pts:
        return None
    table = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        in_bin = [(p, y) for p, y in pts if lo <= p < hi or (b == bins - 1 and p == 1.0)]
        if in_bin:
            table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(in_bin),
                          "mean_p": sum(p for p, _ in in_bin) / len(in_bin),
                          "observed": sum(y for _, y in in_bin) / len(in_bin)})  # fmt: skip
    return {
        "n": len(pts),
        "brier": sum((p - y) ** 2 for p, y in pts) / len(pts),
        "base_rate": sum(y for _, y in pts) / len(pts),
        "bins": table,
    }


def noise(scores: list[CaseScore]) -> dict[str, dict]:
    """Run-to-run spread per case and metric over repetitions (R34); errored runs excluded."""
    import statistics

    out: dict[str, dict] = {}
    for case_id in dict.fromkeys(s.case_id for s in scores):
        runs = [s for s in scores if s.case_id == case_id and s.outcome == "scored"]
        per_metric = {}
        for metric in ("coverage", "omissions_addressed", "grounding"):
            vals = [getattr(s, metric) for s in runs if getattr(s, metric) is not None]
            per_metric[metric] = {
                "n": len(vals),
                "mean": statistics.fmean(vals) if vals else None,
                "stdev": statistics.stdev(vals) if len(vals) > 1 else None,
                "min": min(vals) if vals else None,
                "max": max(vals) if vals else None,
            }
        out[case_id] = per_metric
    return out


# Loose v0 defaults; v1 planning sets real thresholds from R34 noise data.
FAILURE_THRESHOLDS = {"coverage": 0.6, "omissions_addressed": 0.5, "grounding": 0.9}


def failure_records(scores: list[CaseScore]) -> list[dict]:
    """R14b: what went wrong per scored case, for Failure Memory. Errored (infrastructure) cases
    are not quality failures and are skipped."""
    records = []
    for s in scores:
        if s.outcome != "scored":
            continue
        base = {"case_id": s.case_id, "scenario_id": s.scenario_id, "run_id": s.run_id}
        for metric, floor in FAILURE_THRESHOLDS.items():
            value = getattr(s, metric)
            if value is not None and value < floor:
                records.append(base | {"category": f"low_{metric}", "agent": None,
                                       "detail": {"value": value, "threshold": floor}})  # fmt: skip
        if s.judge_error:
            records.append(base | {"category": "judge_error", "agent": None,
                                   "detail": {"error": s.judge_error[:500]}})  # fmt: skip
        if s.judge:
            missed = [v.expected_id for v in s.judge.expected if not v.covered]
            if missed:
                records.append(base | {"category": "missed_expected_impacts", "agent": None,
                                       "detail": {"expected_ids": missed}})  # fmt: skip
        for agent, kind in s.expert_failures.items():
            records.append(base | {"category": f"expert_{kind}", "agent": agent, "detail": {}})
    return records


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
