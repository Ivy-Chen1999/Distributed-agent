"""Failure Memory (U1, R25): structured train/val failure events, aggregated by pattern.

One event per missed expected impact, missed omission, unsupported finding and expert error of
a scored run. Each carries the golden item's drafting ``category`` and the experts whose board
findings cite the item's provision keys (``touching_agents``); the ``owner`` is that one expert,
``multiple`` or ``none``. A pattern is ``(kind, category, owner)``; its frequency counts
persistent misses only: a (case, item) pair missed in at least half of the case's scored runs.
Single-run misses are noise (docs/solutions/evaluation/single-run-scores-are-noise.md).

Holdout data can never become an event: the models refuse ``split = holdout``, and the database
tables carry a ``CHECK`` on the split as well.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from womm.eval.evaluators import CaseScore
from womm.eval.golden import HOLDOUT_REFUSAL, GoldenCase, GoldenError
from womm.models.findings import NO_DATA_IN_SCOPE
from womm.models.run import RunResult

FailureKind = Literal["missed_impact", "missed_omission", "unsupported_finding", "expert_error"]
MemorySplit = Literal["train", "val"]
UNCATEGORISED = "uncategorised"


class FailureEvent(BaseModel):
    kind: FailureKind
    case_id: str
    fixture: str = Field(description="The proposal (fixture) the case is scored against.")
    split: MemorySplit
    item_id: str = Field(
        description="expected_id, omission_id, '<agent>:<provision_key>' for an unsupported "
        "finding, or '<agent>:<error_kind>' for an expert error."
    )
    category: str
    touching_agents: list[str] = Field(default_factory=list)
    owner: str = Field(description="The one touching agent, 'multiple' or 'none'.")
    run_id: str
    repetition: int = Field(ge=1)
    system_version: str
    detail: dict = Field(default_factory=dict)


class CaseRun(BaseModel):
    """One scored run of a case: the denominator of a miss rate."""

    case_id: str
    fixture: str
    split: MemorySplit
    run_id: str
    repetition: int = Field(ge=1)
    system_version: str


def _check_split(split: str | None) -> None:
    if split not in ("train", "val"):
        raise GoldenError(f"Failure Memory takes train/val only, got {split!r}; {HOLDOUT_REFUSAL}")


def _owner(agents: list[str]) -> str:
    if not agents:
        return "none"
    return agents[0] if len(agents) == 1 else "multiple"


def case_run(
    case: GoldenCase, score: CaseScore, *, split: str, repetition: int, system_version: str
) -> CaseRun | None:
    """The scored-run record for ``score``, or None when the run gave no judge verdict."""
    _check_split(split)
    if score.outcome != "scored" or score.judge is None or score.run_id is None:
        return None
    return CaseRun(
        case_id=case.case_id, fixture=case.fixture, split=split, run_id=score.run_id,
        repetition=repetition, system_version=system_version,
    )  # fmt: skip


def failure_events(
    case: GoldenCase,
    score: CaseScore,
    run: RunResult,
    *,
    split: str,
    repetition: int,
    system_version: str,
) -> list[FailureEvent]:
    """Every failure event of one scored repetition. Infrastructure-errored runs and runs
    without a complete judge verdict give none (they say nothing about quality)."""
    _check_split(split)
    if score.outcome != "scored" or score.judge is None or score.judge_error:
        return []
    by_key: dict[str, set[str]] = defaultdict(set)
    for f in run.board:
        by_key[f.provision_key].add(f.agent)

    def event(kind: FailureKind, item_id: str, category: str | None, agents: list[str],
              **detail) -> FailureEvent:  # fmt: skip
        return FailureEvent(
            kind=kind, case_id=case.case_id, fixture=case.fixture, split=split, item_id=item_id,
            category=category or UNCATEGORISED, touching_agents=agents, owner=_owner(agents),
            run_id=run.run_id, repetition=repetition, system_version=system_version,
            detail=detail,
        )  # fmt: skip

    def touching(keys: list[str]) -> list[str]:
        return sorted({a for k in keys for a in by_key.get(k, ())})

    events: list[FailureEvent] = []
    expected = {e.expected_id: e for e in case.expected_impacts}
    for v in score.judge.expected:
        if not v.covered and v.expected_id in expected:
            item = expected[v.expected_id]
            events.append(event("missed_impact", item.expected_id, item.category,
                                touching(item.provision_keys)))  # fmt: skip
    omissions = {o.omission_id: o for o in case.important_omissions}
    for v in score.judge.omissions:
        if not v.addressed and v.omission_id in omissions:
            item = omissions[v.omission_id]
            events.append(event("missed_omission", item.omission_id, item.category,
                                touching(item.provision_keys)))  # fmt: skip
    findings = {f.finding_id: f for f in run.board}
    for q in run.dossier.open_questions if run.dossier else []:
        f = findings.get(q.finding_id or "")
        if q.reason == "evidence_unresolved" and f is not None:
            events.append(event("unsupported_finding", f"{f.agent}:{f.provision_key}", None,
                                [f.agent], finding_id=f.finding_id))  # fmt: skip
    for failure in run.failures:
        if failure.error_kind != NO_DATA_IN_SCOPE:
            events.append(event("expert_error", f"{failure.agent}:{failure.error_kind}", None,
                                [failure.agent]))  # fmt: skip
    return events


def patterns(events: Iterable[FailureEvent], runs: Iterable[CaseRun]) -> list[dict]:
    """Aggregate events by ``(kind, category, owner)``, most persistent first.

    Per (case, item) pair, the miss rate is the share of the case's scored runs that missed
    it; the pair is persistent when that share is at least one half over two or more runs.
    With a single run per case, persistence is ``unknown`` and never counted."""
    n_runs: dict[tuple[str, str], int] = Counter()
    for r in {(r.system_version, r.case_id, r.run_id) for r in runs}:
        n_runs[r[:2]] += 1
    pairs: dict[tuple, dict] = {}
    for e in events:
        key = (e.system_version, e.kind, e.case_id, e.item_id)
        p = pairs.setdefault(key, {"fixture": e.fixture, "category": e.category,
                                   "owners": Counter(), "runs": set()})  # fmt: skip
        p["owners"][e.owner] += 1
        p["runs"].add(e.run_id)
    groups: dict[tuple, dict] = {}
    for (sv, kind, case_id, _item), p in pairs.items():
        top = max(p["owners"].values())
        owner = sorted(o for o, c in p["owners"].items() if c == top)
        owner = owner[0] if len(owner) == 1 else "multiple"
        total = max(n_runs.get((sv, case_id), 0), len(p["runs"]))
        rate = len(p["runs"]) / total
        known = total > 1
        g = groups.setdefault((sv, kind, p["category"], owner), {
            "system_version": sv, "kind": kind, "category": p["category"], "owner": owner,
            "persistent_misses": 0, "pairs": 0, "_cases": set(), "_proposals": set(),
            "_rates": [], "_known": True,
        })  # fmt: skip
        g["pairs"] += 1
        g["_rates"].append(rate)
        g["_known"] &= known
        if known and rate >= 0.5:
            g["persistent_misses"] += 1
            g["_cases"].add(case_id)
            g["_proposals"].add(p["fixture"])
        elif not known:
            g["_cases"].add(case_id)
            g["_proposals"].add(p["fixture"])
    out = []
    for g in groups.values():
        rates = g.pop("_rates")
        known = g.pop("_known")
        out.append(g | {
            "cases": sorted(g.pop("_cases")), "proposals": sorted(g.pop("_proposals")),
            "mean_miss_rate": sum(rates) / len(rates),
            "persistence": "known" if known else "unknown",
        })  # fmt: skip
    return sorted(
        out,
        key=lambda g: (-g["persistent_misses"], -g["mean_miss_rate"], g["kind"], g["category"],
                       g["owner"]),
    )  # fmt: skip


def load_report_events(runs_dir: Path, system_version: str) -> tuple[list[FailureEvent],
                                                                     list[CaseRun]]:  # fmt: skip
    """Events and scored runs from the saved ``eval_*.json`` reports of one system version."""
    events: list[FailureEvent] = []
    runs: list[CaseRun] = []
    for path in sorted(runs_dir.glob("eval_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("system_version") != system_version:
            continue
        events += [FailureEvent.model_validate(e) for e in data.get("failure_events") or []]
        runs += [CaseRun.model_validate(r) for r in data.get("case_runs") or []]
    return events, runs


def format_patterns(rows: list[dict]) -> str:
    if not rows:
        return "no failure patterns"
    lines = [f"{'kind':20} {'category':26} {'owner':12} persistent  pairs  miss-rate  proposals"]
    for r in rows:
        persistent = str(r["persistent_misses"]) if r["persistence"] == "known" else "unknown"
        lines.append(
            f"{r['kind']:20} {r['category']:26} {r['owner']:12} {persistent:>10}  "
            f"{r['pairs']:>5}  {r['mean_miss_rate']:>9.2f}  {','.join(r['proposals'])}"
        )
    return "\n".join(lines)
