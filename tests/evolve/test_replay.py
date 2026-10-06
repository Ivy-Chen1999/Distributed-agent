"""Resumable batch replay (U4, R29) on the fake backend and a throwaway database."""

import asyncio
import json

import pytest

from womm.decisions.stub import StubDecisionService
from womm.eval.golden import GoldenError
from womm.evolve.archive import Archive
from womm.evolve.cycle import ReplayEvaluator
from womm.evolve.replay import (
    ReplayIncomplete,
    ReplayRefused,
    ReplayStore,
    ReplayWorker,
    judge_version,
)
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity

from ..eval.test_run_eval_fake import CASE, _script
from ..graph.conftest import PLAN, fake_sv

CASE_B = CASE.model_copy(update={"case_id": "case_02b_copy"})
CASES = {"train": [CASE, CASE_B]}
CODE = CodeIdentity(git_sha="sha1", dirty=False)


def judge_all_covered(_system, user):
    """A judge step that covers every expected impact and addresses every omission it is
    shown, so one script serves any case."""
    expected = json.loads(user.split("Expected impacts:\n", 1)[1].split("\n\nImportant")[0])
    omissions = json.loads(user.split("Important omissions:\n", 1)[1])
    return {
        "expected": [{"expected_id": e["expected_id"], "covered": True, "impact_id": "I1",
                      "justification": "j"} for e in expected],
        "omissions": [{"omission_id": o["omission_id"], "addressed": True, "impact_id": "I1",
                       "justification": "j"} for o in omissions],
    }  # fmt: skip


def script(items: int) -> dict:
    s = _script(reps=items)
    s["judge"] = [judge_all_covered] * items
    return s


@pytest.fixture
async def setup(db):
    sv = fake_sv()
    archive = Archive(db)
    await archive.archive(sv, origin="seed")
    store = ReplayStore(db, load_cases=lambda split: CASES.get(split, []))
    return sv, archive, store


def worker(store, archive, sv, backend, tmp_path, **kw) -> ReplayWorker:
    return ReplayWorker(
        store=store, archive=archive, judge_sv=sv, backends={"fake": backend},
        decisions=StubDecisionService(), code=CODE, runs_dir=tmp_path / "runs", **kw,
    )  # fmt: skip


def planner_calls(backend: FakeBackend) -> int:
    return sum(c.role_name == "planner" for c in backend.calls)


async def test_batch_completes_and_records_metrics(setup, db, tmp_path):
    sv, archive, store = setup
    batch = await store.submit(sv.version_id, "train", 2, judge_sv=sv, code=CODE)
    backend = FakeBackend(script(4))
    status = await worker(store, archive, sv, backend, tmp_path).run_batch(batch)
    assert status["done"] == 4 and status["complete"] and planner_calls(backend) == 4
    metrics = {
        (m["level"], m["subject"], m["metric"]): m for m in await archive.metrics(sv.version_id)
    }
    assert metrics[("case", CASE.case_id, "coverage")]["n"] == 2
    assert metrics[("split", "", "coverage")]["mean"] == 1.0
    assert {m["split"] for m in await archive.metrics(sv.version_id)} == {"train"}
    scores = await store.results(batch)
    assert len(scores) == 4 and all(s.outcome == "scored" for s in scores)
    assert len(list((tmp_path / "runs").glob("*.json"))) == 4
    # Replays also feed Failure Memory with their scored runs.
    _, runs = await db.failure_memory(sv.version_id)
    assert len(runs) == 4


async def test_killed_worker_is_resumed_without_rerunning_finished_items(setup, tmp_path):
    sv, archive, store = setup
    batch = await store.submit(sv.version_id, "train", 2, judge_sv=sv, code=CODE)
    holder = {}

    def kill_then_plan(_system, _user):
        holder["task"].cancel()  # lands at the next await: the worker dies mid-item
        return PLAN

    first = script(2)
    first["planner"] = [PLAN, kill_then_plan]
    a = worker(store, archive, sv, FakeBackend(first), tmp_path)
    holder["task"] = asyncio.create_task(a.run_batch(batch))
    with pytest.raises(asyncio.CancelledError):
        await holder["task"]
    status = await store.status(batch)
    assert (status["done"], status["running"], status["pending"]) == (1, 1, 2)

    second = FakeBackend(script(3))
    status = await worker(store, archive, sv, second, tmp_path, stale_after_s=0).run_batch(batch)
    assert status["done"] == 4 and status["complete"]
    assert planner_calls(second) == 3  # the finished item was not re-run


async def test_resubmitting_a_finished_batch_is_cached(setup, tmp_path):
    sv, archive, store = setup
    batch = await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=CODE)
    await worker(store, archive, sv, FakeBackend(script(2)), tmp_path).run_batch(batch)
    again = await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=CODE)
    assert again == batch
    idle = FakeBackend({})
    status = await worker(store, archive, sv, idle, tmp_path).run_batch(again)
    assert status["complete"] and idle.calls == []


async def test_rate_limit_marks_errored_and_stops_claiming(setup, tmp_path):
    sv, archive, store = setup
    batch = await store.submit(sv.version_id, "train", 2, judge_sv=sv, code=CODE)
    s = script(4)
    s["judge"] = [LLMError("rate_limit", "usage limit reached")] + s["judge"][1:]
    backend = FakeBackend(s)
    status = await worker(store, archive, sv, backend, tmp_path).run_batch(batch)
    assert (status["errored"], status["infra_errored"], status["pending"]) == (1, 1, 3)
    assert status["halted"].startswith("rate_limit") and not status["complete"]
    assert planner_calls(backend) == 1

    # A halt holds across workers and re-submissions until it is lifted explicitly.
    idle = FakeBackend({})
    assert await store.submit(sv.version_id, "train", 2, judge_sv=sv, code=CODE) == batch
    status = await worker(store, archive, sv, idle, tmp_path).run_batch(batch)
    assert status["halted"] and status["pending"] == 3 and idle.calls == []

    await store.resume(batch)
    again = FakeBackend(script(4))
    status = await worker(store, archive, sv, again, tmp_path).run_batch(batch)
    assert status["done"] == 4 and status["complete"] and status["halted"] is None
    assert planner_calls(again) == 4  # the rate-limited item was retried


async def test_infra_error_is_retried(setup, tmp_path):
    sv, archive, store = setup
    batch = await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=CODE)
    s = script(3)
    s["judge"] = [LLMError("timeout", "judge timed out")] + s["judge"][1:]
    status = await worker(store, archive, sv, FakeBackend(s), tmp_path).run_batch(batch)
    assert status["done"] == 2 and status["complete"] and status["errored"] == 0
    assert len(await store.results(batch)) == 2


async def test_infra_retries_stop_at_the_attempts_cap_and_resume_resets_them(db, tmp_path):
    sv = fake_sv()
    archive = Archive(db)
    await archive.archive(sv, origin="seed")
    store = ReplayStore(db, load_cases=lambda split: CASES.get(split, []), max_attempts=2)
    batch = await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=CODE,
                               case_ids=[CASE.case_id])  # fmt: skip
    s = script(2)
    s["judge"] = [LLMError("timeout", "judge timed out")] * 2
    backend = FakeBackend(s)
    status = await worker(store, archive, sv, backend, tmp_path).run_batch(batch)
    assert (status["errored"], status["infra_errored"], status["retryable"]) == (1, 1, 0)
    assert not status["complete"] and planner_calls(backend) == 2
    assert await archive.metrics(sv.version_id) == []  # an incomplete batch records no metric

    # A new worker does not retry an exhausted item; an explicit resume gives it a new budget.
    idle = FakeBackend({})
    status = await worker(store, archive, sv, idle, tmp_path).run_batch(batch)
    assert not status["complete"] and idle.calls == []
    await store.resume(batch)
    status = await worker(store, archive, sv, FakeBackend(script(1)), tmp_path).run_batch(batch)
    assert status["complete"] and status["done"] == 1


async def test_deterministic_error_is_final(db, tmp_path):
    sv = fake_sv()
    archive = Archive(db)
    await archive.archive(sv, origin="seed")
    cases = {"train": [CASE, CASE_B]}
    store = ReplayStore(db, load_cases=lambda split: cases.get(split, []))
    batch = await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=CODE)
    cases["train"] = [CASE]  # CASE_B left the golden set: a deterministic failure
    backend = FakeBackend(script(1))
    status = await worker(store, archive, sv, backend, tmp_path).run_batch(batch)
    assert (status["done"], status["errored"], status["infra_errored"]) == (1, 1, 0)
    assert status["complete"]
    idle = FakeBackend({})
    await store.resume(batch)
    status = await worker(store, archive, sv, idle, tmp_path).run_batch(batch)
    assert status["complete"] and idle.calls == []


async def test_worker_refuses_without_the_judge_backend(setup, tmp_path):
    sv, archive, store = setup
    batch = await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=CODE)
    w = ReplayWorker(store=store, archive=archive, judge_sv=sv, backends={},
                     decisions=StubDecisionService(), code=CODE)  # fmt: skip
    with pytest.raises(ReplayRefused, match="fake"):
        await w.run_batch(batch)
    status = await store.status(batch)
    assert status["pending"] == 2 and status["errored"] == 0


async def test_holdout_cannot_be_enqueued(db):
    sv = fake_sv()
    await Archive(db).archive(sv, origin="seed")
    store = ReplayStore(db)  # the real golden loader
    with pytest.raises(GoldenError, match="holdout"):
        await store.submit(sv.version_id, "holdout", 1, judge_sv=sv, code=CODE)
    with pytest.raises(GoldenError, match="not a train case"):
        await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=CODE,
                           case_ids=["case_99_sealed_holdout"])  # fmt: skip


async def test_changed_git_sha_creates_new_items(setup, tmp_path):
    sv, archive, store = setup
    first = await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=CODE)
    await worker(store, archive, sv, FakeBackend(script(2)), tmp_path).run_batch(first)
    code2 = CodeIdentity(git_sha="sha2", dirty=False)
    second = await store.submit(sv.version_id, "train", 1, judge_sv=sv, code=code2)
    assert second != first
    status = await store.status(second)
    assert (status["pending"], status["done"]) == (2, 0)


def test_judge_version_ignores_everything_but_the_judge():
    a = fake_sv()
    b = fake_sv(experts=["legal"])
    assert a.version_id != b.version_id and judge_version(a) == judge_version(b)


async def test_evaluator_refuses_an_incomplete_batch(setup, tmp_path):
    """A halted batch must not reach GEPA as a set of missing (zero) scores."""
    sv, archive, store = setup
    s = script(2)
    s["judge"] = [LLMError("rate_limit", "usage limit reached")] + s["judge"][1:]
    w = worker(store, archive, sv, FakeBackend(s), tmp_path)
    evaluator = ReplayEvaluator(archive_store=archive, store=store, worker=w)
    with pytest.raises(ReplayIncomplete, match="halted"):
        await evaluator.evaluate(sv, "train", [CASE.case_id, CASE_B.case_id], 1)
