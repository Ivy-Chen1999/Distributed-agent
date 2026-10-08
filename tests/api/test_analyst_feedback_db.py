"""Analyst feedback in Postgres (U11, R31): the audit rows, their Failure Memory events, the
golden-candidate queue, and the constraints that keep holdout material and unknown runs out."""

import datetime as dt

import psycopg
import pytest

from womm.evolve.failure_memory import CaseRun
from womm.evolve.feedback import AnalystFeedback, resolve_feedback
from womm.evolve.planner_view import PlannerView

from ..evolve.test_failure_memory import K1, _finding, _run

NOW = dt.datetime(2026, 10, 7, tzinfo=dt.UTC)


def _case_run(run_id="r1", rep=1) -> CaseRun:
    return CaseRun(case_id="case_x", fixture="ai_act", split="train", run_id=run_id,
                   repetition=rep, system_version="sv_x")  # fmt: skip


def _record(mark="missing_impact", fid="fb1", run_id="r1", **kw):
    fields = {"impact": "SMEs pay twice", "provision_keys": ["reg/a/9"]}
    if mark != "missing_impact":
        fields = {"finding_id": "f1", "edited": "better" if mark == "edit" else None}
    fb = AnalystFeedback(feedback_id=fid, trace_run_id=f"t-{run_id}", mark=mark, analyst="ana",
                         note="n", created_at=NOW, **fields | kw)  # fmt: skip
    run = _run(run_id, board=[_finding("legal", K1, "f1")])
    return resolve_feedback(fb, _case_run(run_id), run)


async def _with_runs(db, *run_ids):
    await db.record_failure_events([], [_case_run(r, i + 1) for i, r in enumerate(run_ids)])


async def test_marks_are_stored_once_with_their_events(db):
    await _with_runs(db, "r1", "r2")
    assert await db.record_analyst_feedback(_record()) == "new"
    assert await db.record_analyst_feedback(_record()) == "unchanged"  # idempotent by id
    assert await db.record_analyst_feedback(_record("weak_evidence", "fb2", "r2")) == "new"
    assert await db.record_analyst_feedback(_record("accept", "fb3")) == "new"
    rows = {r["feedback_id"]: r for r in await db.analyst_feedback("sv_x")}
    assert (rows["fb1"]["golden_candidate"], rows["fb3"]["golden_candidate"]) == ("queued", None)
    assert rows["fb3"]["failure_event_id"] is None and rows["fb1"]["failure_event_id"]
    assert rows["fb1"]["payload"]["impact"] == "SMEs pay twice"
    events, _ = await db.failure_memory("sv_x")
    assert sorted((e.kind, e.source) for e in events) == [
        ("analyst_missing_impact", "human"), ("analyst_weak_evidence", "human")
    ]  # fmt: skip
    pats = await db.failure_patterns("sv_x")
    assert {p["source"] for p in pats} == {"human"}


async def test_two_analysts_on_the_same_item_share_one_event(db):
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record())
    await db.record_analyst_feedback(_record(fid="fb9"))
    rows = await db.analyst_feedback("sv_x")
    assert len(rows) == 2 and len({r["failure_event_id"] for r in rows}) == 1


async def test_a_mark_on_a_run_missing_from_failure_memory_is_refused(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await db.record_analyst_feedback(_record())
    assert await db.analyst_feedback("sv_x") == []
    assert (await db.failure_memory("sv_x"))[0] == []  # the event rolled back with the mark


async def test_the_golden_candidate_queue(db):
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record())
    (row,) = await db.queued_golden_candidates()
    assert (row["feedback_id"], row["case_id"], row["fixture"]) == ("fb1", "case_x", "ai_act")
    await db.mark_candidate_staged("fb1", "evals/golden/drafts/case_x.yaml")
    assert await db.queued_golden_candidates() == []
    (row,) = await db.analyst_feedback("sv_x")
    assert (row["golden_candidate"], row["golden_draft"]) == (
        "staged",
        "evals/golden/drafts/case_x.yaml",
    )


async def test_constraints_keep_holdout_and_mislabelled_rows_out(db):
    await _with_runs(db, "r1")
    async with db.pool.connection() as conn:
        for sql in (
            "INSERT INTO analyst_feedback (feedback_id, mark, system_version, case_id, fixture,"
            " split, run_id, trace_run_id, created_at) VALUES ('h', 'accept', 'sv_x', 'case_x',"
            " 'ai_act', 'holdout', 'r1', 't', now())",
            "INSERT INTO failure_events (system_version, kind, case_id, fixture, split, item_id,"
            " category, owner, run_id, repetition, source) VALUES"
            " ('sv_x', 'missed_impact', 'case_x', 'ai_act', 'train', 'e', 'x', 'none', 'r1', 1,"
            " 'human')",
            "INSERT INTO failure_events (system_version, kind, case_id, fixture, split, item_id,"
            " category, owner, run_id, repetition) VALUES"
            " ('sv_x', 'analyst_missing_impact', 'case_x', 'ai_act', 'train', 'e', 'x', 'none',"
            " 'r1', 1)",
        ):
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(sql)


async def test_the_planner_view_sees_analyst_events(db, database_url, tmp_path):
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record())
    async with PlannerView(database_url, runs_dir=tmp_path) as view:
        (row,) = await view.failure_patterns("sv_x")
    assert (row["kind"], row["source"]) == ("analyst_missing_impact", "human")


async def test_a_val_missing_impact_is_stored_but_never_queued(db):
    await db.record_failure_events([], [_case_run("r1").model_copy(update={"split": "val"})])
    fb = AnalystFeedback(feedback_id="fbv", trace_run_id="t", mark="missing_impact",
                         impact="x", created_at=NOW)  # fmt: skip
    rec = resolve_feedback(fb, _case_run("r1").model_copy(update={"split": "val"}), _run("r1"))
    assert await db.record_analyst_feedback(rec) == "new"
    (row,) = await db.analyst_feedback("sv_x")
    assert row["golden_candidate"] is None and row["failure_event_id"]
    assert await db.queued_golden_candidates() == []


LATER = NOW + dt.timedelta(hours=1)


async def _events(db):
    return sorted((e.kind, e.item_id, e.detail.get("impact")) for e in
                  (await db.failure_memory("sv_x"))[0])  # fmt: skip


async def test_an_edited_mark_replaces_its_row_and_event(db):
    """P2: a newer LangSmith modification updates the audit row (keeping the old version in its
    history) and replaces the derived Failure Memory event."""
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record(modified_at=NOW))
    assert await db.record_analyst_feedback(_record(modified_at=NOW)) == "unchanged"
    edited = _record(modified_at=LATER, impact="SMEs pay three times",
                     provision_keys=["reg/a/7"])  # fmt: skip
    assert await db.record_analyst_feedback(edited) == "updated"
    (row,) = await db.analyst_feedback("sv_x")
    assert row["payload"]["impact"] == "SMEs pay three times" and row["modified_at"] == LATER
    assert [h["payload"]["impact"] for h in row["history"]] == ["SMEs pay twice"]
    assert row["golden_candidate"] == "queued"
    assert await _events(db) == [("analyst_missing_impact", "human:reg/a/7",
                                  "SMEs pay three times")]  # fmt: skip
    # An older copy never overwrites a newer one.
    assert await db.record_analyst_feedback(_record(modified_at=NOW)) == "unchanged"


async def test_an_edit_that_changes_the_mark_drops_the_candidate_and_event(db):
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record(modified_at=NOW))
    assert await db.record_analyst_feedback(_record("accept", modified_at=LATER)) == "updated"
    (row,) = await db.analyst_feedback("sv_x")
    assert (row["mark"], row["golden_candidate"], row["failure_event_id"]) == ("accept", None,
                                                                               None)  # fmt: skip
    assert await _events(db) == []


async def test_an_edit_keeps_a_shared_event_another_mark_still_uses(db):
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record(modified_at=NOW))
    await db.record_analyst_feedback(_record(fid="fb9", modified_at=NOW))
    await db.record_analyst_feedback(_record("accept", modified_at=LATER))
    assert [k for k, *_ in await _events(db)] == ["analyst_missing_impact"]


async def test_an_edit_after_staging_is_applied_and_reported(db):
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record(modified_at=NOW))
    await db.mark_candidate_staged("fb1", "evals/golden/drafts/case_x.yaml")
    assert await db.record_analyst_feedback(_record(modified_at=LATER, impact="new")) == (
        "updated_staged"
    )
    (row,) = await db.analyst_feedback("sv_x")
    assert (row["golden_candidate"], row["payload"]["impact"]) == ("staged", "new")


async def test_retracting_a_mark_keeps_the_row_and_removes_its_event(db):
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record(modified_at=NOW))
    await db.record_analyst_feedback(_record("weak_evidence", "fb2", modified_at=NOW))
    assert await db.retract_analyst_feedback("fb1") == "retracted"
    assert await db.retract_analyst_feedback("fb1") == "unchanged"
    rows = {r["feedback_id"]: r for r in await db.analyst_feedback("sv_x")}
    assert rows["fb1"]["retracted_at"] is not None and rows["fb1"]["golden_candidate"] is None
    assert rows["fb1"]["failure_event_id"] is None and rows["fb2"]["retracted_at"] is None
    assert [k for k, *_ in await _events(db)] == ["analyst_weak_evidence"]
    assert await db.queued_golden_candidates() == []
    # A mark that shows up again is restored.
    assert await db.record_analyst_feedback(_record(modified_at=NOW)) == "updated"
    rows = {r["feedback_id"]: r for r in await db.analyst_feedback("sv_x")}
    assert rows["fb1"]["retracted_at"] is None and rows["fb1"]["golden_candidate"] == "queued"


async def test_retracting_a_staged_mark_is_reported(db):
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record(modified_at=NOW))
    await db.mark_candidate_staged("fb1", "d.yaml")
    assert await db.retract_analyst_feedback("fb1") == "retracted_staged"
    (row,) = await db.analyst_feedback("sv_x")
    assert row["golden_candidate"] == "staged" and row["retracted_at"] is not None


async def test_marks_are_read_back_by_feedback_id(db):
    """The publish script cross-checks staged analyst candidates against these rows."""
    await _with_runs(db, "r1")
    await db.record_analyst_feedback(_record(modified_at=NOW))
    await db.record_analyst_feedback(_record("weak_evidence", "fb2", modified_at=NOW))
    rows = await db.analyst_feedback_by_id(["fb1", "nope"])
    assert list(rows) == ["fb1"]
    assert (rows["fb1"]["analyst"], rows["fb1"]["case_id"]) == ("ana", "case_x")
    assert await db.analyst_feedback_by_id([]) == {}
