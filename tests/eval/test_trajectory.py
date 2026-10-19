"""Deterministic trajectory metrics from a RunResult and its golden case (no LLM, no network)."""

import datetime as dt

import pytest

from womm.config import REPO_ROOT
from womm.eval.golden import ExpectedImpact, GoldenCase
from womm.eval.trajectory import (
    TRAJECTORY_KEYS,
    anchor_keys,
    feedback_scores,
    trajectory_metrics,
    trajectory_summary,
)
from womm.models.findings import Evidence, ImpactFinding, Provenance
from womm.models.run import CodeIdentity, PlannerTrace, RetrievalRecord, RunResult
from womm.models.system_version import load_system_version

AT = dt.datetime(2026, 10, 4, tzinfo=dt.UTC)
KA, KB, KC = "k/a", "k/b", "k/c"
SA, SB = "v1/art_a", "v1/art_b"
MEMO = "v1/memorandum/context"


def _case(*key_sets: list[str]) -> GoldenCase:
    impacts = [
        ExpectedImpact(expected_id=f"E{i}", affected_actor="a", mechanism="m", impact="i",
                       provision_keys=keys, ia_section="s")
        for i, keys in enumerate(key_sets, 1)
    ]  # fmt: skip
    return GoldenCase(case_id="c", scenario_id="s", ia_reference="r", expected_impacts=impacts)


def _rec(agent, key, status, sids=()):
    return RetrievalRecord(agent=agent, key=key, status=status, source_ids=list(sids), at=AT)


def _finding(agent, key, *sources):
    prov = Provenance(agent=agent, system_version="sv", prompt_hash="h", backend="fake", model="m")
    return ImpactFinding(
        finding_id=f"f_{agent}_{key}", agent=agent, provision_key=key, affected_actor="a",
        impact="i", mechanism="m", confidence=0.5, provenance=prov,
        evidence=[Evidence(evidence_id=f"e{i}", source_id=s, quote="q")
                  for i, s in enumerate(sources)],
    )  # fmt: skip


def _run(*, planner=None, retrievals=(), board=()):
    return RunResult(
        run_id="r", scenario_id="s", status="succeeded", system_version="sv",
        code_identity=CodeIdentity(git_sha=None, dirty=False), planner=planner,
        retrievals=list(retrievals), board=list(board),
    )  # fmt: skip


def test_anchor_keys_are_the_union_of_expected_impact_keys_in_order():
    assert anchor_keys(_case([KA, KB], [KB, KC])) == [KA, KB, KC]
    assert anchor_keys(None) == []


def test_planner_key_recall_and_precision():
    run = _run(planner=PlannerTrace(keys=[KA, "k/x"], areas=[[KA, "k/x"]]))
    m = trajectory_metrics(run, _case([KA], [KB]))
    assert m["planner_key_recall"] == pytest.approx(0.5)
    assert m["planner_key_precision"] == pytest.approx(0.5)


def test_planner_metrics_skip_without_annotation_or_focus():
    m = trajectory_metrics(_run(planner=PlannerTrace(keys=[KA])), None)
    assert m["planner_key_recall"] is None and m["planner_key_precision"] is None
    m = trajectory_metrics(_run(planner=None), _case([KA]))
    assert m["planner_key_recall"] is None
    m = trajectory_metrics(_run(planner=PlannerTrace(keys=[])), _case([KA]))
    assert m["planner_key_recall"] == 0.0 and m["planner_key_precision"] is None


def test_router_recall_and_brier_from_labelled_decisions():
    labelled = [
        {"subject": "legal", "decision": "relevant", "probability": 0.9, "relevant": True},
        {"subject": "fiscal", "decision": "not_relevant", "probability": 0.2, "relevant": True},
        {"subject": "stakeholder", "decision": "relevant", "probability": 0.6, "relevant": False},
    ]
    m = trajectory_metrics(_run(), None, labeled_decisions=labelled)
    assert m["router_recall"] == pytest.approx(0.5)
    assert m["router_brier"] == pytest.approx(((0.1) ** 2 + (0.8) ** 2 + (0.6) ** 2) / 3)


def test_router_metrics_skip_without_labels_or_probabilities():
    m = trajectory_metrics(_run(), None, labeled_decisions=[])
    assert m["router_recall"] is None and m["router_brier"] is None
    no_p = [{"subject": "legal", "decision": "relevant", "probability": None, "relevant": True}]
    m = trajectory_metrics(_run(), None, labeled_decisions=no_p)
    assert m["router_recall"] == 1.0 and m["router_brier"] is None


def test_citation_checks_scope_violations_and_out_of_retrieval():
    retrievals = [
        _rec("legal", KA, "granted_text", [SA]),
        _rec("legal", KB, "out_of_scope"),
        _rec("fiscal", KB, "granted_text", [SB]),
    ]
    board = [
        _finding("legal", KA, SA),  # fine
        _finding("legal", KB, SB),  # another expert's grant: scope violation
        _finding("legal", KA, "v1/art_zzz"),  # never retrieved by anyone
        _finding("fiscal", KB, SB, MEMO),  # memorandum: citable when the scope is unknown
    ]
    m = trajectory_metrics(_run(retrievals=retrievals, board=board), None)
    assert m["scope_violations"] == 1
    assert m["citation_out_of_retrieval"] == 1


def test_memorandum_citation_is_a_violation_when_the_scope_withholds_it():
    sv = load_system_version(REPO_ROOT / "system_versions" / "v1.0-scoped.yaml", REPO_ROOT)
    withheld = next(e.id for e in sv.spec.experts if e.scope and not e.scope.sees_memorandum)
    retrievals = [_rec(withheld, KA, "granted_text", [SA])]
    board = [_finding(withheld, KA, SA, MEMO)]
    m = trajectory_metrics(_run(retrievals=retrievals, board=board), None, sv=sv)
    assert m["scope_violations"] == 1 and m["citation_out_of_retrieval"] == 0


def test_needed_key_refused_counts_anchor_keys_no_expert_got():
    retrievals = [
        _rec("legal", KA, "out_of_scope"),
        _rec("fiscal", KA, "out_of_scope"),
        _rec("legal", KB, "out_of_scope"),
        _rec("fiscal", KB, "granted_obligations", ["v1/obligations/art_b"]),
        _rec("legal", KC, "unknown_key"),
    ]
    m = trajectory_metrics(_run(retrievals=retrievals), _case([KA, KB], [KC, "k/never"]))
    assert m["needed_key_refused"] == 2  # KA (out of scope for all) and KC (unknown)
    assert m["details"]["needed_keys_refused"] == [KA, KC]


def test_retrieval_metrics_skip_without_records():
    m = trajectory_metrics(_run(board=[_finding("legal", KA, SA)]), _case([KA]))
    assert m["scope_violations"] is None
    assert m["citation_out_of_retrieval"] is None
    assert m["needed_key_refused"] is None


def test_feedback_scores_are_prefixed_and_exclude_details():
    m = trajectory_metrics(_run(planner=PlannerTrace(keys=[KA])), _case([KA]))
    scores = feedback_scores(m)
    assert set(scores) == {f"traj.{k}" for k in TRAJECTORY_KEYS}
    assert scores["traj.planner_key_recall"] == 1.0


def test_summary_means_skip_none():
    rows = [
        {"planner_key_recall": 1.0, "scope_violations": 0, "router_brier": None},
        {"planner_key_recall": 0.5, "scope_violations": 2, "router_brier": None},
    ]
    s = trajectory_summary(rows)
    assert s["planner_key_recall"] == {"n": 2, "mean": 0.75}
    assert s["scope_violations"] == {"n": 2, "mean": 1.0}
    assert s["router_brier"] == {"n": 0, "mean": None}
    assert trajectory_summary([]) is None
