from womm.api.db import Database, result_error_kind
from womm.decisions.stub import StubDecisionService
from womm.graph.build import run_scenario
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity, RunEvent, RunResult, RunStatus

from ..graph.conftest import fake_sv, fixture, good_script  # noqa: F401


async def test_migrate_is_idempotent(database_url):
    database = Database(database_url)
    await database.open()
    try:
        assert await database.migrate() == ["001_init", "002_run_ownership", "003_decision_usage"]
        assert await database.migrate() == []
    finally:
        await database.close()


async def test_events_roundtrip_in_order(db):
    await db.create_run("r1", "s", "sv")
    for seq in (2, 1, 3):
        await db.append_event(RunEvent(run_id="r1", seq=seq, node=f"n{seq}", event="started"))
    await db.append_event(RunEvent(run_id="r1", seq=1, node="dup", event="started"))
    rows = await db.list_events("r1")
    assert [r["seq"] for r in rows] == [1, 2, 3] and rows[0]["node"] == "n1"
    assert [r["seq"] for r in await db.list_events("r1", after_seq=2)] == [3]


async def test_reconcile_orphans(db):
    await db.create_run("queued", "s", "sv")
    await db.create_run("running", "s", "sv")
    await db.mark_running("running")
    await db.create_run("done", "s", "sv")
    await db.mark_failed("done", "timeout", "x")
    assert await db.reconcile_orphans(stale_after_s=60) == 0  # fresh heartbeats: still owned
    assert await db.reconcile_orphans(stale_after_s=0) == 2
    run = await db.get_run("running")
    assert (run["status"], run["error_kind"]) == ("failed", "orphaned")
    assert (await db.get_run("done"))["error_kind"] == "timeout"


def _result(status: RunStatus, error: str | None) -> RunResult:
    return RunResult(
        run_id="r", scenario_id="s", status=status, system_version="sv", error=error,
        code_identity=CodeIdentity(git_sha="t", dirty=False),
    )  # fmt: skip


def test_result_error_kind():
    assert result_error_kind(_result(RunStatus.succeeded, None)) is None
    failed = _result(RunStatus.failed, "planner failed: [timeout] claude CLI timed out")
    assert result_error_kind(failed) == "timeout"
    assert result_error_kind(_result(RunStatus.failed, "all experts failed")) == "pipeline_failed"


async def test_failed_result_stores_error_kind(db):
    await db.create_run("r", "s", "sv")
    await db.mark_running("r")
    assert await db.save_result(_result(RunStatus.failed, "planner failed: [auth] not logged in"))
    assert (await db.get_run("r"))["error_kind"] == "auth"


async def test_fake_run_persisted_consistently(db, fixture):  # noqa: F811
    sv = fake_sv()
    await db.upsert_system_version(sv)
    await db.upsert_system_version(sv)
    await db.create_run("run_test", "eval_sme_impacts", sv.version_id)
    await db.mark_running("run_test")
    result = await run_scenario(
        "eval_sme_impacts", sv=sv, fixture=fixture,
        backends={"fake": FakeBackend(good_script(synthesis_all()))},
        decisions=StubDecisionService(), code_identity=CodeIdentity(git_sha="t", dirty=False),
        run_id="run_test", on_event=db.append_event,
    )  # fmt: skip
    await db.save_result(result)
    row = await db.get_run("run_test")
    assert row["status"] == result.status.value == "succeeded"
    assert row["result"]["dossier"]["status"] == "succeeded"
    assert await db.count_decisions("run_test") == len(result.decisions) == 3
    events = await db.list_events("run_test")
    assert events[0]["node"] == "planner" and events[-1]["node"] == "assemble"


async def test_failures(db):
    await db.record_failure(
        case_id="c1", scenario_id="s", category="low_coverage", system_version="sv1",
        detail={"coverage": 0.2}, git_sha="abc",
    )  # fmt: skip
    (row,) = await db.list_failures("sv1")
    assert row["detail"] == {"coverage": 0.2} and row["category"] == "low_coverage"
    assert await db.list_failures("other") == []


def synthesis_all():
    def synth(_system, user):
        import json

        rows = json.loads(user.split("Validated findings:\n", 1)[1])
        impact = {"impact_id": "I1", "summary": "s", "finding_ids": [r["finding_id"] for r in rows]}
        empty = {"chains": [], "disagreements": [], "open_questions": [], "discarded": []}
        return {"impacts": [impact], **empty}

    return synth
