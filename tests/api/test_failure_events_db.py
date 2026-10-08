"""Failure Memory in Postgres (U1): events, scored runs, patterns and the holdout constraint."""

import psycopg
import pytest

from womm.eval.evaluators import CaseScore, JudgeOutput
from womm.eval.golden import load_all_golden
from womm.eval.run_eval import EvalReport, persist_failures
from womm.evolve.failure_memory import CaseRun, FailureEvent

TRAIN_VAL_IDS = {c.case_id for c in load_all_golden()}


def _event(run_id: str, rep: int, case_id="case_02_sme_impacts", **kw) -> FailureEvent:
    return FailureEvent(**{
        "kind": "missed_impact", "case_id": case_id, "fixture": "ai_act", "split": "train",
        "item_id": "c02_e01", "category": "compliance_cost", "owner": "none", "run_id": run_id,
        "repetition": rep, "system_version": "sv_x", **kw,
    })  # fmt: skip


def _run(run_id: str, rep: int, case_id="case_02_sme_impacts") -> CaseRun:
    return CaseRun(case_id=case_id, fixture="ai_act", split="train", run_id=run_id,
                   repetition=rep, system_version="sv_x")  # fmt: skip


async def test_events_round_trip_and_aggregate(db):
    runs = [_run("r1", 1), _run("r2", 2), _run("r3", 3)]
    events = [_event("r1", 1), _event("r2", 2)]
    assert await db.record_failure_events(events, runs, git_sha="abc") == 2
    # Re-recording the same runs is a no-op.
    assert await db.record_failure_events(events, runs, git_sha="abc") == 0
    (row,) = await db.failure_patterns("sv_x")
    assert row["persistent_misses"] == 1 and row["cases"] == ["case_02_sme_impacts"]
    assert row["mean_miss_rate"] == pytest.approx(2 / 3)


async def test_holdout_rows_fail_on_the_database_constraint(db):
    async with db.pool.connection() as conn:
        for sql in (
            "INSERT INTO failure_events (system_version, kind, case_id, fixture, split, item_id,"
            " category, owner, run_id, repetition) VALUES"
            " ('sv', 'missed_impact', 'c', 'f', 'holdout', 'e', 'x', 'none', 'r', 1)",
            "INSERT INTO failure_case_runs (system_version, case_id, fixture, split, run_id,"
            " repetition) VALUES ('sv', 'c', 'f', 'holdout', 'r', 1)",
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(sql)


async def test_persist_failures_writes_events(database_url, db):
    case = next(c for c in load_all_golden() if c.case_id == "case_02_sme_impacts")
    judge = JudgeOutput(
        expected=[{"expected_id": e.expected_id, "covered": False, "impact_id": None,
                   "justification": "j"} for e in case.expected_impacts],
        omissions=[{"omission_id": o.omission_id, "addressed": True, "impact_id": None,
                    "justification": "j"} for o in case.important_omissions],
    )  # fmt: skip
    score = CaseScore(case_id=case.case_id, scenario_id=case.scenario_id, outcome="scored",
                      coverage=0.0, grounding=1.0, run_id="r1", judge=judge)  # fmt: skip
    events = [_event("r1", 1, item_id=e.expected_id) for e in case.expected_impacts]
    report = EvalReport(
        system_version="sv_x", metadata={"git_sha": "abc", "repetitions": 1},
        scores=[score], case_splits={case.case_id: "train"},
        failure_events=events, case_runs=[_run("r1", 1)],
    )  # fmt: skip
    written = await persist_failures(report, database_url)
    assert written == 2 + len(events)  # low_coverage + missed_expected_impacts + events
    rows = await db.failure_patterns("sv_x")
    # AE3 (part): Failure Memory holds train/val case ids only.
    assert {c for r in rows for c in r["cases"]} <= TRAIN_VAL_IDS
    assert rows[0]["persistence"] == "unknown"
