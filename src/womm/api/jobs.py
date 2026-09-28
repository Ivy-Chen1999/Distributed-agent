"""In-process background runs (v0 scale): a bounded set of asyncio tasks backed by the runs table.

A redeploy kills in-flight runs; on the next start `Database.reconcile_orphans` marks them failed.
Resuming interrupted runs is v1 work (R29).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from womm.api.db import Database
from womm.data.fixtures import Fixture
from womm.decisions.service import DecisionService
from womm.graph.build import run_scenario
from womm.llm.base import LLMBackend
from womm.models.run import CodeIdentity
from womm.models.system_version import SystemVersion


@dataclass
class JobRunner:
    db: Database
    sv: SystemVersion
    fixture: Fixture
    backends: dict[str, LLMBackend]
    decisions: DecisionService
    code: CodeIdentity
    max_concurrent_runs: int = 2
    _sem: asyncio.Semaphore = field(init=False)
    _tasks: set[asyncio.Task] = field(init=False, default_factory=set)

    def __post_init__(self) -> None:
        self._sem = asyncio.Semaphore(self.max_concurrent_runs)

    async def submit(self, scenario_id: str) -> str:
        self.fixture.scenario(scenario_id)  # raises FixtureError for unknown scenarios
        run_id = f"run_{uuid.uuid4()}"
        await self.db.create_run(run_id, scenario_id, self.sv.version_id)
        task = asyncio.create_task(self._execute(run_id, scenario_id), name=run_id)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run_id

    async def _execute(self, run_id: str, scenario_id: str) -> None:
        async with self._sem:
            await self.db.mark_running(run_id)
            try:
                result = await run_scenario(
                    scenario_id, sv=self.sv, fixture=self.fixture, backends=self.backends,
                    decisions=self.decisions, code_identity=self.code, run_id=run_id,
                    on_event=self.db.append_event, tags=["api"],
                )  # fmt: skip
                await self.db.save_result(result)
            except asyncio.CancelledError:
                await self.db.mark_failed(run_id, "cancelled", "server shutting down")
                raise
            except Exception as exc:  # noqa: BLE001 - record, never lose a run silently
                await self.db.mark_failed(run_id, "process_error", f"{type(exc).__name__}: {exc}")

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    @property
    def active(self) -> int:
        return len(self._tasks)
