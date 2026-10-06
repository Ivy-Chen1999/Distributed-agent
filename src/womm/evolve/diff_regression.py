"""R37 diff regression check (U8): every SystemVersion scored on the provision-level demo diff.

The R2 demo scenario ``demo_penalties_amended`` (proposal -> adopted Regulation) has no impact
assessment, so it is scored against hand-written reference answers in
``evals/diff_regression/demo_penalties_amended.yaml`` (``GoldenCase`` shape, split
``diff_check``; anchors are provision quotes, not IA quotes). One person writes and decides
every item, as origin R37 requires: no LLM writes them and no item is auto-accepted. Until a
person has written them the file is a template and the check reports ``not_available``.

- **Monitoring only.** The score is recorded per version in the archive (``sv_metrics`` split
  ``diff_check``) and copied into each promotion record next to the incumbent's score. A
  regression is shown and never changes a decision.
- **Not Planner input.** ``PlannerView`` reads train/val metrics only, and no module that
  builds the Improvement Planner's input imports this one (import-graph test in
  ``tests/evolve/test_planner_boundary.py``), so the Planner cannot optimise the monitor.
- The check runs through the resumable replay store (split ``diff_check``), with the judge
  pinned to the seed's, like train/val replays. It never feeds Failure Memory.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import ValidationError

from womm.config import REPO_ROOT
from womm.data.fixtures import FixtureError
from womm.eval.golden import GoldenCase, load_case_fixture
from womm.evolve.archive import Archive
from womm.models.base import StrictModel

REFERENCE_PATH = REPO_ROOT / "evals" / "diff_regression" / "demo_penalties_amended.yaml"
DIFF_CHECK_SPLIT = "diff_check"
SCENARIO_ID = "demo_penalties_amended"
SCORE_METRIC = "coverage"
PLACEHOLDER = "TODO"


class DiffCheckCase(GoldenCase):
    """A reference case for the R37 diff check: never a train, val or holdout case."""

    split: Literal["diff_check"]  # type: ignore[assignment]


class Reference(StrictModel):
    available: bool
    reason: str | None = None
    case: DiffCheckCase | None = None


def _placeholders(value: Any) -> int:
    if isinstance(value, str):
        return int(PLACEHOLDER in value)
    if isinstance(value, dict):
        return sum(_placeholders(v) for v in value.values())
    if isinstance(value, list):
        return sum(_placeholders(v) for v in value)
    return 0


def _not_available(reason: str) -> Reference:
    return Reference(available=False, reason=reason)


def load_reference(path: Path = REFERENCE_PATH) -> Reference:
    """The hand-written reference answers, or ``available=False`` with the reason: no file, a
    template not marked written, or placeholders left. An invalid written file is an error."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return _not_available(f"no reference answers at {path}")
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected a mapping")
    if raw.get("status") != "written" or not str(raw.get("written_by") or "").strip():
        return _not_available(
            "the reference answers are still a template: a person writes them by hand (R37), "
            "sets status: written and written_by"
        )
    if n := _placeholders(raw.get("case")):
        return _not_available(f"{n} {PLACEHOLDER} placeholder(s) left in the reference answers")
    try:
        case = DiffCheckCase.model_validate(raw.get("case"))
    except ValidationError as exc:
        raise ValueError(f"{path}: {exc}") from None
    check_reference(case)
    return Reference(available=True, case=case)


def check_reference(case: DiffCheckCase) -> None:
    """The reference answers belong to the R2 demo scenario, within its provisions."""
    if case.scenario_id != SCENARIO_ID:
        raise ValueError(f"{case.case_id}: the R37 check is defined on {SCENARIO_ID}")
    try:
        scenario = load_case_fixture(case).scenario(case.scenario_id)
    except FixtureError as exc:
        raise ValueError(f"{case.case_id}: {exc}") from None
    keys = set(scenario.provision_keys)
    for item in [*case.expected_impacts, *case.important_omissions]:
        missing = [k for k in item.provision_keys if k not in keys]
        if missing:
            raise ValueError(f"{case.case_id}: provision_keys {missing} are not in {SCENARIO_ID}")


# ----------------------------------------------------------------------------- scores


async def diff_check_score(archive: Archive, version_id: str) -> dict | None:
    """The version's latest diff-check score: mean coverage over its runs, with the run-to-run
    SD (``n`` runs), or None when it was never checked."""
    rows = [r for r in await archive.metrics(version_id)
            if r["split"] == DIFF_CHECK_SPLIT and r["level"] == "case"
            and r["metric"] == SCORE_METRIC]  # fmt: skip
    if not rows:
        return None
    r = rows[-1]
    return {"mean": r["mean"], "sd": r["sd"], "n": r["n"], "judge_version": r["judge_version"]}


def is_regression(candidate: dict, incumbent: dict) -> bool:
    """The candidate scores below the incumbent by more than one pooled run-to-run SD."""
    sds = [s["sd"] or 0.0 for s in (candidate, incumbent)]
    pooled = math.sqrt(sum(sd * sd for sd in sds) / 2)
    return candidate["mean"] < incumbent["mean"] - pooled - 1e-12


async def r37_report(
    archive: Archive, candidate_id: str, incumbent_id: str, reference: Reference
) -> dict[str, Any]:
    """What a promotion record carries: both scores and the regression flag (never gating)."""
    if not reference.available:
        return {"status": "not_available", "reason": reference.reason, "candidate": None,
                "incumbent": None, "regression": None}  # fmt: skip
    cand = await diff_check_score(archive, candidate_id)
    inc = await diff_check_score(archive, incumbent_id)
    if cand is None or inc is None or cand["mean"] is None or inc["mean"] is None:
        missing = [v for v, s in ((candidate_id, cand), (incumbent_id, inc))
                   if s is None or s["mean"] is None]  # fmt: skip
        return {"status": "not_run", "reason": f"no diff-check score for {', '.join(missing)} "
                "(womm evolve diffcheck)", "candidate": cand, "incumbent": inc,
                "regression": None}  # fmt: skip
    return {"status": "available", "reason": None, "candidate": cand, "incumbent": inc,
            "regression": is_regression(cand, inc)}  # fmt: skip
