"""Cross-family judge re-score of a gate comparison (plan 2026-10-15-001, U2; R28).

The runs a gate comparison used are re-judged, not re-run: only judge calls, with the coverage
judge of another model family (by default ``system_versions/v1.0-unscoped.yaml``, claude_code,
the same prompt file). The result is report-only: it never changes the gate's decision. It is
labelled ``cross-judge-sensitive`` when the re-scored coverage delta has the opposite sign to the
gate's, or when the gate promoted and the re-scored delta is at or below 0.

Holdout side, like ``womm.evolve.promotion``: it reads the sealed store and writes only the
holdout database's ``rescore_audit`` (aggregates only, never a case id), never the main database
or Failure Memory, and the Improvement Planner never imports it.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import Field

from womm.eval import holdout
from womm.eval.evaluators import CaseScore, judge_version, score_case
from womm.eval.holdout import MIN_PROPOSALS, MetricDelta
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel
from womm.models.run import RunResult
from womm.models.system_version import SystemVersion

Label = Literal["agrees", "cross-judge-sensitive", "inconclusive"]


class RescoreRefused(RuntimeError):
    """A precondition failed; no judge call was made."""


class RescoreStopped(RuntimeError):
    """A rate limit stopped the re-score; finished results are saved, rerun to resume."""


class ArmScore(StrictModel):
    mean: float | None = Field(description="Mean over cases of the re-scored coverage.")
    scored: int
    errored: int = Field(description="Runs without a re-scored coverage (judge error or a run "
                         "that was never scored).")  # fmt: skip


class RescoreResult(StrictModel):
    """Aggregates only, by construction: no case ids, no per-case values."""

    gate_id: str
    rescore_judge_version: str
    judge: str = Field(description="backend:model of the re-scoring judge.")
    gate_judge_version: str | None
    repetitions: int
    failure_policy: str
    arms: dict[str, ArmScore]
    delta: MetricDelta
    dropped_cases: int = Field(description="Cases without a re-scored value on some side.")
    gate_delta: float | None
    gate_decision: str
    sign_agrees: bool | None
    label: Label


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)


def _rate_limited(score: CaseScore) -> bool:
    return any("[rate_limit]" in (t or "") for t in (score.error, score.judge_error))


def label_for(rescored: float | None, gate: float | None, decision: str) -> Label:
    if rescored is None or gate is None:
        return "inconclusive"
    if rescored * gate < 0 or (decision == "promoted" and rescored <= 0):
        return "cross-judge-sensitive"
    return "agrees"


async def rescore(
    gate_id: str,
    *,
    store: Any,
    judge_sv: SystemVersion,
    backends: Mapping[str, LLMBackend],
    replace: bool = False,
) -> RescoreResult:
    """Re-judge the stored runs of gate ``gate_id`` with ``judge_sv``'s coverage judge."""
    stored = await store.stored_runs(gate_id)
    if stored is None:
        raise RescoreRefused(f"no comparison recorded for gate {gate_id}")
    audit, rows = stored["audit"], stored["rows"]
    result = audit["result"]
    if result.get("aborted"):
        raise RescoreRefused(f"the comparison of gate {gate_id} aborted: nothing to re-score")
    if not audit.get("decision"):
        raise RescoreRefused(f"gate {gate_id} has no decision recorded")
    if not rows or audit.get("judge_version") is None:
        raise RescoreRefused(f"gate {gate_id} has no recorded runs (decided before holdout "
                             "migration 004, or unchecked): it cannot be re-scored")  # fmt: skip
    if any(r["score"] is not None and r["run"] is None for r in rows):
        raise RescoreRefused(f"gate {gate_id} used runs saved before runs were stored (holdout "
                             "migration 004): it cannot be re-scored")  # fmt: skip
    cases = await holdout.sealed_by_key(store)
    if missing := {r["case_key"] for r in rows} - cases.keys():
        raise RescoreRefused(f"{len(missing)} case(s) of gate {gate_id} are no longer in the "
                             "sealed store")  # fmt: skip
    rjv = judge_version(judge_sv)
    if await store.current_rescore(gate_id) is not None:
        if not replace:
            raise RescoreRefused(f"gate {gate_id} is already re-scored: pass --replace to "
                                 "supersede it")  # fmt: skip
        await store.clear_rescore_progress(gate_id, rjv)
    role = judge_sv.spec.judge
    backend, prompt = backends[role.backend], judge_sv.prompt_text(role)
    done = await store.load_rescore_progress(gate_id, rjv)

    arm_of = {audit["candidate_version"]: "candidate", audit["baseline_version"]: "baseline"}
    values: dict[str, dict[str, list[float]]] = {"candidate": {}, "baseline": {}}
    errored = {"candidate": 0, "baseline": 0}
    with holdout.tracing_context(enabled=False):  # holdout material never reaches LangSmith
        for row in rows:
            arm, key, rep = arm_of[row["version_id"]], row["case_key"], row["repetition"]
            if row["score"] is None:
                errored[arm] += 1
                continue
            score = done.get((row["version_id"], key, rep))
            if score is None:
                run = RunResult.model_validate(row["run"])
                score, _ = await score_case(cases[key], run, backend, role, prompt)
                score = score.model_copy(update={"case_id": key})
                if _rate_limited(score):
                    raise RescoreStopped(f"rate limited while re-scoring gate {gate_id}: rerun "
                                         "the same command to resume")  # fmt: skip
                if score.outcome == "scored" and score.coverage is not None:
                    await store.save_rescore_progress(gate_id, rjv, row["version_id"], key, rep,
                                                      score)  # fmt: skip
            if score.outcome == "scored" and score.coverage is not None:
                values[arm].setdefault(key, []).append(score.coverage)
            else:
                errored[arm] += 1

    means = {arm: {k: statistics.fmean(v) for k, v in vals.items()} for arm, vals in values.items()}
    all_keys = sorted({r["case_key"] for r in rows})
    lacking = [k for k in all_keys if k not in means["candidate"] or k not in means["baseline"]]
    policy = result.get("failure_policy", "abort")
    cluster_of = {k: cases[k].fixture for k in all_keys}
    boot = result.get("bootstrap") or {}
    point = lo = hi = se = None
    if not lacking or policy == "score_zero":
        diffs = {k: means["candidate"].get(k, 0.0) - means["baseline"].get(k, 0.0)
                 for k in all_keys}  # fmt: skip
        point, lo, hi, se = holdout._bootstrap(diffs, cluster_of, boot.get("n_boot", 2000),
                                               boot.get("seed", 0))  # fmt: skip
        if len(set(cluster_of.values())) < MIN_PROPOSALS:
            lo = hi = se = None
        n_cases = len(diffs)
    else:
        n_cases = 0
    gate_delta = ((result.get("deltas") or {}).get("coverage") or {}).get("mean_delta")
    decision = audit["decision"].get("decision", "")
    out = RescoreResult(
        gate_id=gate_id, rescore_judge_version=rjv, judge=f"{role.backend}:{role.model}",
        gate_judge_version=audit.get("judge_version"), repetitions=audit["repetitions"],
        failure_policy=policy,
        arms={arm: ArmScore(mean=statistics.fmean(m.values()) if m else None,
                            scored=sum(len(v) for v in values[arm].values()),
                            errored=errored[arm]) for arm, m in means.items()},
        delta=MetricDelta(mean_delta=point, ci95_low=lo, ci95_high=hi, se=se, n_cases=n_cases),
        dropped_cases=len(lacking), gate_delta=gate_delta, gate_decision=decision,
        sign_agrees=None if point is None or gate_delta is None
        else _sign(point) == _sign(gate_delta),
        label=label_for(point, gate_delta, decision),
    )  # fmt: skip
    await store.record_rescore(gate_id, rjv, out.model_dump(mode="json"), replace=replace)
    return out


def format_rescore(r: RescoreResult | Mapping[str, Any]) -> str:
    d = r.model_dump(mode="json") if isinstance(r, RescoreResult) else dict(r)
    delta = d["delta"]
    mean = "n/a" if delta["mean_delta"] is None else f"{delta['mean_delta']:+.3f}"
    ci = ("CI null" if delta["ci95_low"] is None
          else f"CI95 [{delta['ci95_low']:+.3f}, {delta['ci95_high']:+.3f}]")  # fmt: skip
    gate = "n/a" if d["gate_delta"] is None else f"{d['gate_delta']:+.3f}"
    return (
        f"cross-judge check ({d['judge']}): {d['label']}; re-scored coverage delta {mean} "
        f"({ci}, {delta['n_cases']} cases), gate's {gate} ({d['gate_decision']}); "
        f"errored runs {d['arms']['candidate']['errored']}+{d['arms']['baseline']['errored']}, "
        f"dropped cases {d['dropped_cases']}; report-only"
    )
