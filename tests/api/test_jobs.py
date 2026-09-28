import asyncio

import pytest

from womm.api.db import Database
from womm.api.jobs import JobRunner, QueueFull
from womm.data.fixtures import load_fixture
from womm.decisions.stub import StubDecisionService
from womm.llm.base import LLMBackend
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity, RunResult, RunStatus

from ..graph.conftest import fake_sv, good_script
from .test_db import synthesis_all


class BlockingBackend(LLMBackend):
    """Every call waits until `release` is set; lets tests hold runs in flight."""

    name = "fake"

    def __init__(self):
        self.release = asyncio.Event()

    async def _invoke(self, *a, **k):
        await self.release.wait()
        raise AssertionError("released backends are not used in these tests")


def _runner(db, backend, **kw) -> JobRunner:
    return JobRunner(
        db=db, sv=fake_sv(), fixture=load_fixture(), backends={"fake": backend},
        decisions=StubDecisionService(), code=CodeIdentity(git_sha="t", dirty=False), **kw,
    )  # fmt: skip


async def _status(db, run_id):
    return (await db.get_run(run_id))["status"]


async def test_shutdown_cancels_running_and_queued_runs(db):
    runner = _runner(db, BlockingBackend(), max_concurrent_runs=1)
    running = await runner.submit("eval_sme_impacts")
    queued = await runner.submit("eval_sme_impacts")
    for _ in range(100):
        if await _status(db, running) == "running":
            break
        await asyncio.sleep(0.01)
    assert await _status(db, queued) == "queued"
    await runner.shutdown()
    for run_id in (running, queued):
        row = await db.get_run(run_id)
        assert (row["status"], row["error_kind"]) == ("failed", "cancelled")


async def test_queue_cap(db):
    runner = _runner(db, BlockingBackend(), max_pending_runs=1)
    await runner.submit("eval_sme_impacts")
    with pytest.raises(QueueFull):
        await runner.submit("eval_sme_impacts")
    await runner.shutdown()


async def test_event_store_failure_does_not_abort_run(db, monkeypatch):
    async def broken(event):
        raise RuntimeError("events table unavailable")

    monkeypatch.setattr(db, "append_event", broken)
    runner = _runner(db, FakeBackend(good_script(synthesis_all())))
    run_id = await runner.submit("eval_sme_impacts")
    await asyncio.gather(*runner._tasks)
    assert await _status(db, run_id) == "succeeded"


async def test_mark_running_failure_is_recorded(db, monkeypatch):
    async def broken(run_id):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(db, "mark_running", broken)
    runner = _runner(db, FakeBackend({}))
    run_id = await runner.submit("eval_sme_impacts")
    await asyncio.gather(*runner._tasks)
    row = await db.get_run(run_id)
    assert (row["status"], row["error_kind"]) == ("failed", "process_error")


async def test_terminal_status_is_never_overwritten(db):
    await db.create_run("r", "s", "sv", "inst_a")
    await db.mark_running("r")
    assert await db.reconcile_orphans(stale_after_s=0) == 1
    late = RunResult(
        run_id="r", scenario_id="s", status=RunStatus.succeeded, system_version="sv",
        code_identity=CodeIdentity(git_sha="t", dirty=False),
    )  # fmt: skip
    assert await db.save_result(late) is False
    assert await db.mark_failed("r", "cancelled", "late") is False
    row = await db.get_run("r")
    assert (row["status"], row["error_kind"]) == ("failed", "orphaned")


async def test_live_instance_runs_survive_other_replicas_reconcile(database_url):
    a, b = Database(database_url), Database(database_url)
    await a.open()
    await b.open()
    try:
        await a.migrate()
        await b.migrate()  # concurrent-safe, no-op the second time
        await a.create_run("live", "s", "sv", "inst_a")
        await a.mark_running("live")
        assert await b.reconcile_orphans(stale_after_s=120) == 0
        assert await a.heartbeat("inst_a") == 1
        assert (await a.get_run("live"))["status"] == "running"
    finally:
        await a.close()
        await b.close()
