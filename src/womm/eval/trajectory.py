"""Deterministic trajectory metrics: how the run got to its dossier, not only what it produced.

Computed from a ``RunResult`` (Planner focus, retrieval records, board), the golden case's
expected impacts and the judge-labelled router decisions (``CaseScore.decisions``). No LLM is
called, so the metrics can be recomputed offline from ``runs/``. A metric is ``None`` when its
inputs are missing (no annotation, no Planner focus on an old run, no retrieval records), never
a silent zero.

| key | meaning |
|---|---|
| planner_key_recall | share of the case's anchor keys the Planner chose |
| planner_key_precision | share of the Planner's keys that are anchor keys |
| router_recall | share of judge-relevant experts the router called relevant |
| router_brier | mean (p - relevant)^2 over labelled decisions with a probability |
| scope_violations | evidence citing a source the run holds but this expert may not cite |
| citation_out_of_retrieval | evidence citing a source the run never retrieved for anyone |
| needed_key_refused | anchor keys some expert requested and no expert was granted |

Anchor keys are the union of ``expected_impacts[].provision_keys``. LangSmith feedback keys are
the metric names prefixed ``traj.``.
"""

from __future__ import annotations

import statistics
from typing import Any

from womm.eval.golden import GoldenCase
from womm.models.run import RunResult
from womm.models.system_version import SystemVersion

TRAJECTORY_KEYS: tuple[str, ...] = (
    "planner_key_recall",
    "planner_key_precision",
    "router_recall",
    "router_brier",
    "scope_violations",
    "citation_out_of_retrieval",
    "needed_key_refused",
)
FEEDBACK_PREFIX = "traj."


def anchor_keys(case: GoldenCase | None) -> list[str]:
    """The provision keys the case's expected impacts follow from, in first-seen order."""
    if case is None:
        return []
    return list(dict.fromkeys(k for e in case.expected_impacts for k in e.provision_keys))


def _planner(run: RunResult, anchors: list[str]) -> dict[str, float | None]:
    if run.planner is None or not anchors:
        return {"planner_key_recall": None, "planner_key_precision": None}
    chosen = set(run.planner.keys)
    hits = len(chosen & set(anchors))
    return {
        "planner_key_recall": hits / len(anchors),
        "planner_key_precision": hits / len(chosen) if chosen else None,
    }


def _router(labeled: list[dict] | None) -> dict[str, float | None]:
    labeled = labeled or []
    relevant = [d for d in labeled if d.get("relevant")]
    recall = (
        sum(d.get("decision") == "relevant" for d in relevant) / len(relevant) if relevant else None
    )
    pts = [
        (d["probability"], 1.0 if d.get("relevant") else 0.0)
        for d in labeled
        if d.get("probability") is not None
    ]
    brier = sum((p - y) ** 2 for p, y in pts) / len(pts) if pts else None
    return {"router_recall": recall, "router_brier": brier}


def _is_memorandum(source_id: str, kinds: dict[str, str]) -> bool:
    return kinds.get(source_id) == "memorandum" or "/memorandum/" in source_id


def _memorandum_allowed(agent: str, sv: SystemVersion | None) -> bool:
    """Unscoped experts and scopes with ``sees_memorandum`` get the memorandum. Without the
    SystemVersion the scope is unknown, so a memorandum citation is not counted against it."""
    if sv is None:
        return True
    expert = next((e for e in sv.spec.experts if e.id == agent), None)
    return expert is None or expert.scope is None or expert.scope.sees_memorandum


def _citations(run: RunResult, sv: SystemVersion | None) -> dict[str, int | None]:
    if not run.retrievals:
        return {"scope_violations": None, "citation_out_of_retrieval": None}
    granted: dict[str, set[str]] = {}
    for r in run.retrievals:
        if r.granted:
            granted.setdefault(r.agent, set()).update(r.source_ids)
    kinds = {s.source_id: s.kind for s in run.citable_sources or []}
    known = set(kinds) | {sid for ids in granted.values() for sid in ids}
    violations = outside = 0
    for finding in run.board:
        mine = granted.get(finding.agent, set())
        for ev in finding.evidence:
            sid = ev.source_id
            if sid in mine:
                continue
            if _is_memorandum(sid, kinds):
                violations += not _memorandum_allowed(finding.agent, sv)
            elif sid in known:
                violations += 1
            else:
                outside += 1
    return {"scope_violations": violations, "citation_out_of_retrieval": outside}


def _needed_refused(run: RunResult, anchors: list[str]) -> tuple[int | None, list[str]]:
    if not run.retrievals or not anchors:
        return None, []
    requested: dict[str, bool] = {}
    for r in run.retrievals:
        requested[r.key] = requested.get(r.key, False) or r.granted
    refused = [k for k in anchors if k in requested and not requested[k]]
    return len(refused), refused


def trajectory_metrics(
    run: RunResult,
    case: GoldenCase | None,
    *,
    labeled_decisions: list[dict] | None = None,
    sv: SystemVersion | None = None,
) -> dict[str, Any]:
    """Every ``TRAJECTORY_KEYS`` metric for one run, plus ``details`` (which anchor keys were
    refused). ``labeled_decisions`` are ``CaseScore.decisions``; ``sv`` lets memorandum
    citations be checked against each expert's scope."""
    anchors = anchor_keys(case)
    n_refused, refused = _needed_refused(run, anchors)
    return {
        **_planner(run, anchors),
        **_router(labeled_decisions),
        **_citations(run, sv),
        "needed_key_refused": n_refused,
        "details": {"needed_keys_refused": refused},
    }


def feedback_scores(metrics: dict[str, Any]) -> dict[str, float | int | None]:
    """The metrics as LangSmith feedback keys (``traj.<name>``)."""
    return {FEEDBACK_PREFIX + k: metrics.get(k) for k in TRAJECTORY_KEYS}


def trajectory_summary(rows: list[dict[str, Any]]) -> dict[str, dict] | None:
    """Per-metric count and mean over the given rows, skipping None (None when no rows)."""
    if not rows:
        return None
    out: dict[str, dict] = {}
    for key in TRAJECTORY_KEYS:
        vals = [r[key] for r in rows if r.get(key) is not None]
        out[key] = {"n": len(vals), "mean": statistics.fmean(vals) if vals else None}
    return out
