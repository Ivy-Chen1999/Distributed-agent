"""Failure Memory (U1, R25): per-item failure events and their pattern aggregation."""

import json

import pytest

from womm.eval.evaluators import CaseScore, JudgeOutput
from womm.eval.golden import ExpectedImpact, GoldenCase, GoldenError, Omission
from womm.evolve.failure_memory import (
    FailureEvent,
    case_run,
    failure_events,
    load_report_events,
    patterns,
)
from womm.models.dossier import ImpactDossier, OpenQuestion
from womm.models.findings import ExpertFailure, ImpactFinding, Provenance
from womm.models.run import CodeIdentity, RunResult, RunStatus

K1, K2, K3 = "reg/a/1", "reg/a/2", "reg/a/3"


def _case(case_id="case_x", fixture="ai_act", n=5, categories=True) -> GoldenCase:
    keys = [K1, K2, K3, K1, K2]
    cats = ["compliance_cost", "social_environmental", "compliance_cost", None, None]
    return GoldenCase(
        case_id=case_id, scenario_id="s", ia_reference="ref", fixture=fixture, split="train",
        expected_impacts=[
            ExpectedImpact(expected_id=f"e{i}", affected_actor="a", mechanism="m", impact="i",
                           provision_keys=[keys[i]], ia_section="x",
                           category=cats[i] if categories else None)
            for i in range(n)
        ],
        important_omissions=[
            Omission(omission_id="o1", description="d", provision_keys=[K3], source="s",
                     category="public_enforcement_cost")
        ],
    )  # fmt: skip


def _finding(agent: str, key: str, fid: str) -> ImpactFinding:
    prov = Provenance(agent=agent, system_version="sv", prompt_hash="h", backend="fake", model="m")
    return ImpactFinding(
        finding_id=fid, agent=agent, provision_key=key, affected_actor="a", impact="i",
        mechanism="m", evidence=[], confidence=0.5, provenance=prov,
    )  # fmt: skip


def _run(run_id="r1", board=(), failures=(), open_questions=()) -> RunResult:
    dossier = ImpactDossier(
        run_id=run_id, scenario_id="s", status="succeeded", system_version="sv",
        open_questions=list(open_questions),
    )  # fmt: skip
    return RunResult(
        run_id=run_id, scenario_id="s", status=RunStatus.succeeded, system_version="sv",
        code_identity=CodeIdentity(git_sha="g", dirty=False), dossier=dossier,
        board=list(board), failures=list(failures),
    )  # fmt: skip


def _score(case: GoldenCase, missed=(), omitted=(), run_id="r1", outcome="scored") -> CaseScore:
    judge = JudgeOutput(
        expected=[{"expected_id": e.expected_id, "covered": e.expected_id not in missed,
                   "impact_id": None, "justification": "j"} for e in case.expected_impacts],
        omissions=[{"omission_id": o.omission_id, "addressed": o.omission_id not in omitted,
                    "impact_id": None, "justification": "j"} for o in case.important_omissions],
    )  # fmt: skip
    return CaseScore(
        case_id=case.case_id, scenario_id="s", outcome=outcome, run_id=run_id, judge=judge
    )


def _events(case, score, run, rep=1, split="train"):
    return failure_events(case, score, run, split=split, repetition=rep, system_version="sv")


def test_missed_impacts_become_one_row_each_with_category():
    case = _case()
    events = _events(case, _score(case, missed=("e0", "e1")), _run())
    missed = [e for e in events if e.kind == "missed_impact"]
    assert [(e.item_id, e.category) for e in missed] == [
        ("e0", "compliance_cost"),
        ("e1", "social_environmental"),
    ]
    assert all(e.split == "train" and e.fixture == "ai_act" for e in missed)


def test_missed_omission_row():
    case = _case()
    (event,) = _events(case, _score(case, omitted=("o1",)), _run())
    assert (event.kind, event.item_id, event.category) == (
        "missed_omission",
        "o1",
        "public_enforcement_cost",
    )


@pytest.mark.parametrize(
    ("board", "owner", "touching"),
    [
        ([], "none", []),
        ([("legal", K1)], "legal", ["legal"]),
        ([("legal", K1), ("fiscal", K1), ("fiscal", K2)], "multiple", ["fiscal", "legal"]),
        ([("fiscal", K2)], "none", []),
    ],
)
def test_owner_is_the_expert_citing_the_item_keys(board, owner, touching):
    case = _case()
    findings = [_finding(a, k, f"f{i}") for i, (a, k) in enumerate(board)]
    (event,) = _events(case, _score(case, missed=("e0",)), _run(board=findings))
    assert (event.owner, event.touching_agents) == (owner, touching)


def test_uncategorised_hand_written_case():
    case = _case(categories=False)
    (event,) = _events(case, _score(case, missed=("e0",)), _run())
    assert event.category == "uncategorised"


def test_unsupported_findings_and_expert_errors():
    case = _case()
    run = _run(
        board=[_finding("fiscal", K2, "f1")],
        failures=[
            ExpertFailure(agent="legal", error_kind="timeout", message="t"),
            ExpertFailure(agent="stakeholder", error_kind="no_data_in_scope", message="n"),
        ],
        open_questions=[
            OpenQuestion(question="q", finding_id="f1", reason="evidence_unresolved"),
            OpenQuestion(question="q", finding_id=None, reason="synthesis"),
        ],
    )
    events = _events(case, _score(case), run)
    assert [(e.kind, e.item_id, e.owner) for e in events] == [
        ("unsupported_finding", f"fiscal:{K2}", "fiscal"),
        ("expert_error", "legal:timeout", "legal"),
    ]


def test_errored_cases_and_judge_failures_give_no_item_rows():
    case = _case()
    assert _events(case, _score(case, missed=("e0",), outcome="errored"), _run()) == []
    no_judge = _score(case).model_copy(update={"judge": None, "judge_error": "bad"})
    assert _events(case, no_judge, _run()) == []
    assert case_run(case, no_judge, split="train", repetition=1, system_version="sv") is None


def test_holdout_split_is_refused():
    case = _case()
    with pytest.raises(GoldenError, match="holdout"):
        _events(case, _score(case, missed=("e0",)), _run(), split="holdout")
    with pytest.raises(ValueError):
        FailureEvent(kind="missed_impact", case_id="c", fixture="f", split="holdout",
                     item_id="e", category="c", owner="none", run_id="r",
                     repetition=1, system_version="sv")  # fmt: skip


def _cycle(case, misses_per_rep: list[tuple[str, ...]]):
    events, runs = [], []
    for rep, missed in enumerate(misses_per_rep, start=1):
        score = _score(case, missed=missed, run_id=f"{case.case_id}-r{rep}")
        run = _run(run_id=f"{case.case_id}-r{rep}")
        events += _events(case, score, run, rep=rep)
        runs.append(case_run(case, score, split="train", repetition=rep, system_version="sv"))
    return events, runs


def test_persistence_needs_half_of_the_repetitions():
    case = _case()
    events, runs = _cycle(case, [("e0", "e1"), ("e0",), ()])
    rows = {(p["kind"], p["category"]): p for p in patterns(events, runs)}
    cost = rows[("missed_impact", "compliance_cost")]
    assert cost["persistent_misses"] == 1 and cost["persistence"] == "known"
    assert cost["cases"] == ["case_x"] and cost["proposals"] == ["ai_act"]
    assert cost["mean_miss_rate"] == pytest.approx(2 / 3)
    social = rows[("missed_impact", "social_environmental")]
    assert social["persistent_misses"] == 0
    assert social["mean_miss_rate"] == pytest.approx(1 / 3)


def test_single_repetition_persistence_is_unknown():
    case = _case()
    events, runs = _cycle(case, [("e0",)])
    (row,) = patterns(events, runs)
    assert row["persistence"] == "unknown" and row["persistent_misses"] == 0


def test_patterns_group_across_proposals():
    a, b = _case("case_a", "ai_act"), _case("case_b", "cra")
    ea, ra = _cycle(a, [("e1",), ("e1",)])
    eb, rb = _cycle(b, [("e1",), ("e1",)])
    (row,) = patterns(ea + eb, ra + rb)
    assert (row["kind"], row["category"], row["owner"]) == (
        "missed_impact",
        "social_environmental",
        "none",
    )
    assert row["persistent_misses"] == 2 and row["proposals"] == ["ai_act", "cra"]


def test_load_report_events_reads_saved_eval_reports(tmp_path):
    case = _case()
    events, runs = _cycle(case, [("e0",), ("e0",)])
    report = {
        "system_version": "sv",
        "failure_events": [e.model_dump(mode="json") for e in events],
        "case_runs": [r.model_dump(mode="json") for r in runs],
    }
    (tmp_path / "eval_20261006T000000Z_sv.json").write_text(json.dumps(report))
    (tmp_path / "eval_20261006T000001Z_other.json").write_text(
        json.dumps({**report, "system_version": "other"})
    )
    got_events, got_runs = load_report_events(tmp_path, "sv")
    assert len(got_events) == 2 and len(got_runs) == 2
    assert patterns(got_events, got_runs)[0]["persistent_misses"] == 1


def test_partial_judge_verdict_is_neither_an_event_nor_a_denominator_run():
    """A judge that skipped ids gives a verdict but no complete one: it must not count as a
    scored run either, or it inflates the miss-rate denominator."""
    case = _case()
    partial = _score(case, missed=("e0",)).model_copy(update={"judge_error": "judge skipped ids"})
    assert partial.judge is not None
    assert _events(case, partial, _run()) == []
    assert case_run(case, partial, split="train", repetition=1, system_version="sv") is None


def _identified_cycle(case, misses_per_rep, **identity):
    events, runs = [], []
    for rep, missed in enumerate(misses_per_rep, start=1):
        rid = f"{case.case_id}-{identity.get('judge_version')}-{identity.get('git_sha')}-r{rep}"
        score = _score(case, missed=missed, run_id=rid)
        memory = {"split": "train", "repetition": rep, "system_version": "sv", **identity}
        events += failure_events(case, score, _run(run_id=rid), **memory)
        runs.append(case_run(case, score, **memory))
    return events, runs


@pytest.mark.parametrize("field", ["judge_version", "git_sha"])
def test_patterns_never_pool_runs_of_different_judges_or_code(field):
    case = _case()
    other = {"judge_version": "jv_a", "git_sha": "sha_a"}
    e1, r1 = _identified_cycle(case, [("e0",)], **other)
    e2, r2 = _identified_cycle(case, [("e0",)], **(other | {field: "changed"}))
    rows = patterns(e1 + e2, r1 + r2)
    assert len(rows) == 2  # one single-run (unknown) pattern each, never one 2-run pattern
    assert all(r["persistence"] == "unknown" and r["persistent_misses"] == 0 for r in rows)
    assert {r[field] for r in rows} == {other[field], "changed"}

    e3, r3 = _identified_cycle(case, [("e0",), ("e0",)], **other)
    (row,) = patterns(e3, r3)
    assert row["persistent_misses"] == 1 and row["judge_version"] == "jv_a"
