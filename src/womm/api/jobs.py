"""In-process background runs (v0 scale): a bounded set of asyncio tasks backed by the runs table.

Each process has an instance id and heartbeats its active runs. A periodic reconcile fails runs
whose owner stopped heartbeating (crash or redeploy) without touching another live replica's runs.
Resuming interrupted runs is v1 work (R29).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field

from womm.api.db import Database
from womm.data.fixtures import Fixture
from womm.decisions.service import DecisionService
from womm.graph.build import run_scenario
from womm.llm.base import LLMBackend
from womm.models.run import CodeIdentity, RunEvent
from womm.models.system_version import SystemVersion

log = logging.getLogger("womm.jobs")


class QueueFull(RuntimeError):
    """Too many runs queued or running in this process."""


@dataclass
class JobRunner:
    db: Database
    sv: SystemVersion
    fixture: Fixture
    backends: dict[str, LLMBackend]
    decisions: DecisionService
    code: CodeIdentity
    max_concurrent_runs: int = 2
    max_pending_runs: int = 20
    heartbeat_s: float = 30.0
    stale_after_s: float = 120.0
    instance_id: str = field(default_factory=lambda: f"inst_{uuid.uuid4()}")
    _sem: asyncio.Semaphore = field(init=False)
    _tasks: set[asyncio.Task] = field(init=False, default_factory=set)
    _maintenance: asyncio.Task | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.max_concurrent_runs)

    def start(self) -> None:
        self._maintenance = asyncio.create_task(self._maintain(), name="womm-maintenance")

    async def _maintain(self) -> None:
        """Heartbeat this instance's runs and fail other owners' stale ones, forever."""
        while True:
            try:
                await self.db.heartbeat(self.instance_id)
                if n := await self.db.reconcile_orphans(self.stale_after_s):
                    log.warning("marked %d orphaned run(s) failed", n)
            except Exception:  # noqa: BLE001 - maintenance must keep going
                log.exception("run maintenance failed")
            await asyncio.sleep(self.heartbeat_s)

    async def submit(self, scenario_id: str, sv: SystemVersion | None = None) -> str:
        """Queue a run with the runner's version, or a derived one (e.g. with overrides)."""
        self.fixture.scenario(scenario_id)  # raises FixtureError for unknown scenarios
        if len(self._tasks) >= self.max_pending_runs:
            raise QueueFull(f"{len(self._tasks)} runs already queued or running")
        sv = sv or self.sv
        if sv is not self.sv:
            await self.db.upsert_system_version(sv)
        run_id = f"run_{uuid.uuid4()}"
        await self.db.create_run(run_id, scenario_id, sv.version_id, self.instance_id)
        task = asyncio.create_task(self._execute(run_id, scenario_id, sv), name=run_id)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run_id

    async def _event(self, event: RunEvent) -> None:
        """Live events are best-effort: a failed insert must not abort the run."""
        try:
            await self.db.append_event(event)
        except Exception:  # noqa: BLE001
            log.exception("could not store event %s/%s", event.run_id, event.seq)

    async def _execute(self, run_id: str, scenario_id: str, sv: SystemVersion) -> None:
        try:
            async with self._sem:
                await self.db.mark_running(run_id)
                result = await run_scenario(
                    scenario_id, sv=sv, fixture=self.fixture, backends=self.backends,
                    decisions=self.decisions, code_identity=self.code, run_id=run_id,
                    on_event=self._event, tags=["api"],
                )  # fmt: skip
                if not await self.db.save_result(result):
                    log.warning(
                        "run %s finished after it was marked failed; result dropped", run_id
                    )
        except asyncio.CancelledError:
            await self._fail(run_id, "cancelled", "server shutting down")
            raise
        except Exception as exc:  # noqa: BLE001 - record, never lose a run silently
            await self._fail(run_id, "process_error", f"{type(exc).__name__}: {exc}")

    async def _fail(self, run_id: str, kind: str, message: str) -> None:
        try:
            await self.db.mark_failed(run_id, kind, message)
        except Exception:  # noqa: BLE001 - the stale-heartbeat reconcile will catch it later
            log.exception("could not mark run %s failed", run_id)

    async def shutdown(self) -> None:
        if self._maintenance:
            self._maintenance.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._maintenance
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    @property
    def active(self) -> int:
        return len(self._tasks)
