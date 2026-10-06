"""Resumable batch replay (U4, R29): a candidate on train/val cases x repetitions, as Postgres
work items that survive a crash or redeploy.

- One item per ``(version_id, case_id, repetition, judge_version, git_sha)``. Items outlive
  batches: re-submitting a batch reuses every finished item, so replay is also the cache the
  Improvement Planner's evaluation reads.
- Workers claim items with ``FOR UPDATE SKIP LOCKED`` and heartbeat the item they run (the
  ``womm.api.jobs`` pattern). On start, and before every claim, items whose owner stopped
  heartbeating go back to ``pending``; done items are never re-run.
- An errored item is ``infra`` (rate limit, timeout, network, missing backend: says nothing
  about the candidate) or ``deterministic`` (final). Any worker, including one started by a
  re-submitted batch, retries infra-errored items until ``max_attempts``; a batch with an
  infra-errored item is not complete, records no metric and fails the CLI.
- The judge is pinned per batch to the seed's judge (``judge_sv``), so a candidate cannot move
  its own yardstick.
- The code is pinned too: a worker refuses a batch whose ``git_sha`` is not its own
  ``code_version``. Dirty trees are allowed and identified by a hash of their diff.
- Holdout cases cannot be enqueued: cases come from ``load_all_golden(split=...)``, which refuses
  the holdout split, and every case id must belong to the batch's split.
- A rate-limit error marks the item ``errored`` (infra) and halts the batch: no new claims
  until ``resume`` (``womm evolve replay|worker --resume``) lifts the halt and gives exhausted
  infra-errored items a new attempt budget.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from womm import tracing
from womm.api.db import Database, _without_nul
from womm.data.fixtures import Fixture
from womm.decisions.service import DecisionService
from womm.eval.evaluators import INFRA_ERRORS, CaseScore, score_case
from womm.eval.golden import GoldenCase, GoldenError, load_all_golden, load_case_fixture
from womm.eval.run_eval import _hit_rate_limit
from womm.eval.trajectory import trajectory_metrics
from womm.evolve.archive import Archive, metric_rows
from womm.evolve.failure_memory import case_run, failure_events
from womm.graph.build import run_scenario
from womm.llm.base import LLMBackend, LLMError
from womm.models.run import CodeIdentity, RunResult
from womm.models.system_version import SystemVersion

log = logging.getLogger("womm.replay")
REPLAY_SPLITS = ("train", "val")
STATUSES = ("pending", "running", "done", "errored")
MAX_ATTEMPTS = 3
# Exceptions that say nothing about the candidate: retried, never final.
_INFRA_EXCEPTIONS = (ConnectionError, TimeoutError, psycopg.OperationalError)
_IN_BATCH = (
    "i.version_id = b.version_id AND i.judge_version = b.judge_version"
    " AND i.git_sha = b.git_sha AND i.case_id = ANY(b.case_ids)"
    " AND i.repetition <= b.repetitions"
)


def judge_version(sv: SystemVersion) -> str:
    """The judge's identity: backend, model and prompt hash, nothing else of the version."""
    judge = sv.spec.judge
    blob = json.dumps(
        {"backend": judge.backend, "model": judge.model,
         "prompt": sv.prompt_hashes[judge.prompt]}, sort_keys=True,
    )  # fmt: skip
    return f"jv_{hashlib.sha256(blob.encode()).hexdigest()[:12]}"


def code_version(code: CodeIdentity) -> str:
    """The code a replay item was scored on: the git sha, plus a hash of the uncommitted diff
    for a dirty tree. Dirty trees are allowed (dev replays), but two different uncommitted
    edits never share items, and a worker only runs a batch on exactly the code it was
    submitted from. Code that cannot be identified (no sha, or a dirty tree whose diff could
    not be read) is refused."""
    if not code.git_sha or (code.dirty and not code.diff_sha):
        raise ValueError(f"unidentifiable code {code!r}: replay needs a git sha (and a diff "
                         "hash for a dirty tree)")  # fmt: skip
    return f"{code.git_sha}+dirty.{code.diff_sha}" if code.dirty else code.git_sha


def _load_split(split: str) -> list[GoldenCase]:
    return load_all_golden(split=split)


class ReplayRefused(RuntimeError):
    """A worker cannot run this batch faithfully (wrong judge, code or missing backend)."""


class ReplayIncomplete(RuntimeError):
    """A batch ended with items left to run (halted, or infra errors past the cap)."""


class MissingBackend(LookupError):
    pass


def _is_infra(exc: BaseException) -> bool:
    if isinstance(exc, LLMError):
        return exc.error_kind in INFRA_ERRORS
    return isinstance(exc, (MissingBackend, *_INFRA_EXCEPTIONS))


class ReplayStore:
    def __init__(
        self,
        db: Database,
        load_cases: Callable[[str], list[GoldenCase]] = _load_split,
        max_attempts: int = MAX_ATTEMPTS,
    ) -> None:
        self.db = db
        self.load_cases = load_cases
        self.max_attempts = max_attempts

    def cases(self, split: str) -> dict[str, GoldenCase]:
        if split not in REPLAY_SPLITS:
            raise GoldenError(f"replay takes train/val only; split {split!r} (holdout is sealed)")
        return {c.case_id: c for c in self.load_cases(split)}

    async def submit(
        self,
        version_id: str,
        split: str,
        repetitions: int,
        *,
        judge_sv: SystemVersion,
        code: CodeIdentity,
        case_ids: list[str] | None = None,
    ) -> str:
        """Enqueue ``version_id`` on ``split`` (every case, or ``case_ids``) and return the batch
        id. Submitting the same batch again returns the same id and adds no item."""
        available = self.cases(split)
        chosen = sorted(case_ids if case_ids is not None else available)
        foreign = [c for c in chosen if c not in available]
        if foreign:
            raise GoldenError(f"{foreign}: not a {split} case; holdout cases are never replayed")
        if not chosen:
            raise GoldenError(f"no {split} cases to replay")
        if repetitions < 1:
            raise ValueError("repetitions must be at least 1")
        jv, sha = judge_version(judge_sv), code_version(code)
        key = json.dumps([version_id, split, chosen, repetitions, jv, sha])
        batch_id = f"rb_{hashlib.sha256(key.encode()).hexdigest()[:16]}"
        async with self.db.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO replay_batches (batch_id, version_id, split, case_ids, repetitions,"
                " judge_version, git_sha) VALUES (%s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (batch_id) DO NOTHING",
                (batch_id, version_id, split, chosen, repetitions, jv, sha),
            )
            for case_id in chosen:
                for rep in range(1, repetitions + 1):
                    await conn.execute(
                        "INSERT INTO replay_items (version_id, case_id, repetition,"
                        " judge_version, git_sha, split) VALUES (%s, %s, %s, %s, %s, %s)"
                        " ON CONFLICT DO NOTHING",
                        (version_id, case_id, rep, jv, sha, split),
                    )
        return batch_id

    async def batch(self, batch_id: str) -> dict:
        async with self.db.pool.connection() as conn:
            row = await (
                await conn.execute("SELECT * FROM replay_batches WHERE batch_id = %s", (batch_id,))
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown replay batch {batch_id}")
        return row

    async def reclaim_stale(self, stale_after_s: float) -> int:
        """Put running items whose owner stopped heartbeating back to pending."""
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE replay_items SET status = 'pending', owner = NULL"
                " WHERE status = 'running'"
                " AND heartbeat_at < now() - make_interval(secs => %s)",
                (stale_after_s,),
            )
            return cur.rowcount

    async def claim(self, batch_id: str, owner: str) -> dict | None:
        """The next pending (or retryable infra-errored) item of a batch that is not halted,
        now running for ``owner``."""
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE replay_items SET status = 'running', owner = %s, heartbeat_at = now(),"
                " attempts = attempts + 1 WHERE item_id = ("
                "  SELECT i.item_id FROM replay_items i JOIN replay_batches b"
                f"   ON b.batch_id = %s AND b.halted IS NULL AND {_IN_BATCH}"
                "  WHERE i.status = 'pending' OR (i.status = 'errored'"
                "   AND i.error_kind = 'infra' AND i.attempts < %s)"
                "  ORDER BY i.case_id, i.repetition"
                "  FOR UPDATE OF i SKIP LOCKED LIMIT 1"
                ") RETURNING *",
                (owner, batch_id, self.max_attempts),
            )
            return await cur.fetchone()

    async def heartbeat(self, item_id: int, owner: str) -> bool:
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE replay_items SET heartbeat_at = now()"
                " WHERE item_id = %s AND owner = %s AND status = 'running'",
                (item_id, owner),
            )
            return cur.rowcount == 1

    async def finish(
        self,
        item_id: int,
        owner: str,
        *,
        status: str,
        run_id: str | None = None,
        score: CaseScore | None = None,
        trajectory: dict | None = None,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> bool:
        """Record a finished item; False when it is no longer this owner's (reclaimed)."""
        if status == "errored" and error_kind not in ("infra", "deterministic"):
            raise ValueError(f"errored needs error_kind infra|deterministic, not {error_kind!r}")
        payload = _without_nul(score.model_dump(mode="json")) if score else None
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE replay_items SET status = %s, run_id = %s, score = %s, trajectory = %s,"
                " error = %s, error_kind = %s, finished_at = now()"
                " WHERE item_id = %s AND owner = %s AND status = 'running'",
                (status, run_id, Jsonb(payload) if payload else None,
                 Jsonb(_without_nul(trajectory)) if trajectory is not None else None,
                 (error or "")[:2000] or None, error_kind if status == "errored" else None,
                 item_id, owner),
            )  # fmt: skip
            return cur.rowcount == 1

    async def halt(self, batch_id: str, reason: str) -> None:
        async with self.db.pool.connection() as conn:
            await conn.execute(
                "UPDATE replay_batches SET halted = %s WHERE batch_id = %s", (reason, batch_id)
            )

    async def resume(self, batch_id: str) -> None:
        """Lift a halt and give the batch's infra-errored items a new attempt budget.
        Deterministic errors stay final (a fix is a new git sha, hence new items)."""
        await self.batch(batch_id)
        async with self.db.pool.connection() as conn, conn.transaction():
            await conn.execute(
                "UPDATE replay_batches SET halted = NULL WHERE batch_id = %s", (batch_id,)
            )
            await conn.execute(
                "UPDATE replay_items i SET attempts = 0 FROM replay_batches b"
                f" WHERE b.batch_id = %s AND {_IN_BATCH}"
                " AND i.status = 'errored' AND i.error_kind = 'infra'",
                (batch_id,),
            )

    async def status(self, batch_id: str) -> dict:
        """Item counts by status. ``infra_errored`` items (of which ``retryable`` are under
        the attempts cap) are still owed a result: the batch is complete only when every item
        is done or deterministically errored."""
        batch = await self.batch(batch_id)
        async with self.db.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT i.status, i.error_kind, i.attempts < %s AS retry, count(*) AS n"
                " FROM replay_items i JOIN replay_batches b"
                f" ON b.batch_id = %s AND {_IN_BATCH} GROUP BY 1, 2, 3",
                (self.max_attempts, batch_id),
            )).fetchall()  # fmt: skip
        counts = dict.fromkeys(STATUSES, 0)
        infra = retryable = 0
        for r in rows:
            counts[r["status"]] += r["n"]
            if r["status"] == "errored" and r["error_kind"] == "infra":
                infra += r["n"]
                retryable += r["n"] if r["retry"] else 0
        total = len(batch["case_ids"]) * batch["repetitions"]
        return {
            "batch_id": batch_id, "version_id": batch["version_id"], "split": batch["split"],
            **counts, "infra_errored": infra, "retryable": retryable, "total": total,
            "halted": batch["halted"],
            "complete": counts["done"] + counts["errored"] - infra == total,
        }  # fmt: skip

    async def results(self, batch_id: str) -> list[CaseScore]:
        """The scores of a batch's finished items, by case then repetition."""
        async with self.db.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT i.score FROM replay_items i JOIN replay_batches b"
                f" ON b.batch_id = %s AND {_IN_BATCH}"
                " WHERE i.status = 'done' ORDER BY i.case_id, i.repetition", (batch_id,),
            )).fetchall()  # fmt: skip
        return [CaseScore.model_validate(r["score"]) for r in rows]


@dataclass
class ReplayWorker:
    store: ReplayStore
    archive: Archive
    judge_sv: SystemVersion
    backends: dict[str, LLMBackend]
    decisions: DecisionService
    code: CodeIdentity
    runs_dir: Path | None = None
    owner: str = field(default_factory=lambda: f"replay_{uuid.uuid4()}")
    heartbeat_s: float = 30.0
    stale_after_s: float = 120.0
    _fixtures: dict[str, Fixture] = field(init=False, default_factory=dict)

    async def run_batch(self, batch_id: str) -> dict:
        """Run pending items of a batch until none is left (or the batch is halted). Metrics go
        to the archive once every item is finished. Cancelling the worker leaves its current
        item running; it is reclaimed once its heartbeat is stale."""
        batch = await self.store.batch(batch_id)
        if batch["judge_version"] != judge_version(self.judge_sv):
            raise ReplayRefused(f"batch {batch_id} is pinned to another judge")
        try:
            here = code_version(self.code)
        except ValueError as exc:
            raise ReplayRefused(str(exc)) from None
        if here != batch["git_sha"]:
            raise ReplayRefused(f"batch {batch_id} was submitted from code {batch['git_sha']}; "
                                f"this worker runs {here}")  # fmt: skip
        sv = await self.archive.load_candidate(batch["version_id"])
        needed = {r.backend for r in sv.spec.roles().values()} | {self.judge_sv.spec.judge.backend}
        if missing := sorted(needed - set(self.backends)):
            raise ReplayRefused(f"batch {batch_id} needs backend(s) {missing} (candidate roles "
                                "and the pinned judge); this worker has none of them")  # fmt: skip
        cases = self.store.cases(batch["split"])
        while True:
            if n := await self.store.reclaim_stale(self.stale_after_s):
                log.warning("reclaimed %d stale replay item(s)", n)
            item = await self.store.claim(batch_id, self.owner)
            if item is None:
                break
            case = cases.get(item["case_id"])
            if case is None:
                await self.store.finish(item["item_id"], self.owner, status="errored",
                                        error_kind="deterministic",
                                        error="case no longer in the golden set")  # fmt: skip
                continue
            await self._run_item(batch_id, item, sv, case)
        status = await self.store.status(batch_id)
        if status["complete"]:
            scores = await self.store.results(batch_id)
            fixtures = {c.case_id: c.fixture for c in cases.values()}
            await self.archive.record_metrics(
                sv.version_id, batch["split"], metric_rows(scores, fixtures)
            )
        return status

    async def _beat(self, item_id: int, work: asyncio.Task, lost: asyncio.Event) -> None:
        """Heartbeat ``item_id`` while ``work`` scores it. When the item is no longer this
        worker's (reclaimed), or heartbeats keep failing for ``stale_after_s`` (so it will be
        reclaimed), abort ``work``: its result could only be dropped or double-counted."""
        loop = asyncio.get_running_loop()
        last_ok = loop.time()
        while True:
            await asyncio.sleep(self.heartbeat_s)
            try:
                ours = await self.store.heartbeat(item_id, self.owner)
            except Exception as exc:  # noqa: BLE001 - logged; aborts once the item goes stale
                log.warning("heartbeat of replay item %s failed: %s: %s",
                            item_id, type(exc).__name__, exc)  # fmt: skip
                if loop.time() - last_ok < self.stale_after_s:
                    continue
                log.warning("replay item %s: no heartbeat for %.0fs; aborting it",
                            item_id, self.stale_after_s)  # fmt: skip
            else:
                if ours:
                    last_ok = loop.time()
                    continue
                log.warning("replay item %s was reclaimed by another worker; aborting it",
                            item_id)  # fmt: skip
            lost.set()
            work.cancel()
            return

    async def _run_item(self, batch_id: str, item: dict, sv: SystemVersion, case: GoldenCase):
        lost = asyncio.Event()
        work = asyncio.create_task(self._score(item, sv, case))
        beat = asyncio.create_task(self._beat(item["item_id"], work, lost))
        try:
            try:
                run, score, traj = await work
            except asyncio.CancelledError:
                me = asyncio.current_task()
                if lost.is_set() and not (me and me.cancelling()):
                    return  # aborted by the heartbeat: the item is (or will be) someone else's
                raise
            except Exception as exc:  # noqa: BLE001 - record, never lose an item silently
                kind = "infra" if _is_infra(exc) else "deterministic"
                await self.store.finish(item["item_id"], self.owner, status="errored",
                                        error_kind=kind,
                                        error=f"{type(exc).__name__}: {exc}")  # fmt: skip
                return
            limited = _hit_rate_limit(run, score)
            # An infra-errored run, or one that hit a rate limit anywhere, is no verdict on the
            # candidate: errored (infra), so it is retried instead of cached.
            errored = score.outcome == "errored" or limited
            finished = await self.store.finish(
                item["item_id"], self.owner, status="errored" if errored else "done",
                run_id=run.run_id, score=score, trajectory=traj,
                error=(score.error or "rate_limit") if errored else None,
                error_kind="infra" if errored else None,
            )  # fmt: skip
            if limited:
                await self.store.halt(batch_id, f"rate_limit during {case.case_id} "
                                                f"repetition {item['repetition']}")  # fmt: skip
                return
            if not finished:
                log.warning("replay item %s was reclaimed before it finished; result dropped",
                            item["item_id"])  # fmt: skip
                return
            await self._remember(item, sv, case, run, score)
        finally:
            beat.cancel()
            work.cancel()

    async def _score(self, item: dict, sv: SystemVersion, case: GoldenCase):
        split, rep = item["split"], item["repetition"]
        fixture = load_case_fixture(case, self._fixtures)
        judge = self.judge_sv.spec.judge
        if judge.backend not in self.backends:
            raise MissingBackend(f"judge backend {judge.backend!r} is not prepared")

        async def one() -> tuple[RunResult, CaseScore, dict]:
            run = await run_scenario(
                case.scenario_id, sv=sv, fixture=fixture, backends=self.backends,
                decisions=self.decisions, code_identity=self.code,
                tags=["replay", case.case_id], run_mode="eval", case_id=case.case_id,
                split=split,
            )  # fmt: skip
            score, _ = await score_case(
                case, run, self.backends[judge.backend], judge, self.judge_sv.prompt_text(judge)
            )
            traj = trajectory_metrics(run, case, labeled_decisions=score.decisions, sv=sv)
            score = score.model_copy(update={"trajectory": traj, "graph_run_id": run.trace_run_id})
            return run, score, traj

        meta = tracing.run_metadata(sv, scenario_id=case.scenario_id, mode="eval", code=self.code,
                                    case_id=case.case_id, split=split)  # fmt: skip
        traced = tracing.traced(
            one, split=split, name="womm:replay_case", run_type="chain",
            metadata={**meta, "repetition": rep, "replay_item": item["item_id"]},
            tags=["replay", case.case_id, f"split:{split}"],
        )  # fmt: skip
        run, score, traj = await traced()
        if self.runs_dir:
            self.runs_dir.mkdir(parents=True, exist_ok=True)
            (self.runs_dir / f"{run.run_id}.json").write_text(run.model_dump_json(indent=2))
        return run, score, traj

    async def _remember(self, item, sv, case, run, score) -> None:
        """Feed Failure Memory with the scored run; best effort, the replay result is kept."""
        memory = {"split": item["split"], "repetition": item["repetition"],
                  "system_version": sv.version_id}  # fmt: skip
        try:
            runs = [r for r in [case_run(case, score, **memory)] if r is not None]
            events = failure_events(case, score, run, **memory)
            await self.store.db.record_failure_events(events, runs, git_sha=item["git_sha"])
        except Exception:  # noqa: BLE001
            log.exception("could not record failure events for replay item %s", item["item_id"])
