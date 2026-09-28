"""Postgres persistence for runs, run events, decisions and failures (U9).

Optional: the CLI works without a database. Migrations are numbered SQL files applied once,
in order, and recorded in schema_migrations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from womm.models.decisions import DecisionRecord
from womm.models.run import RunEvent, RunResult, RunStatus
from womm.models.system_version import SystemVersion

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
ACTIVE = (RunStatus.queued.value, RunStatus.running.value)


class Database:
    def __init__(
        self, url: str, *, min_size: int = 1, max_size: int = 5, statement_timeout_ms: int = 30_000
    ) -> None:
        # A statement timeout keeps one stuck query from pinning a pool connection forever.
        self.pool = AsyncConnectionPool(
            url, min_size=min_size, max_size=max_size, open=False,
            kwargs={"row_factory": dict_row, "autocommit": True,
                    "options": f"-c statement_timeout={statement_timeout_ms}"},
        )  # fmt: skip

    async def open(self) -> None:
        await self.pool.open(wait=True)

    async def close(self) -> None:
        await self.pool.close()

    async def migrate(self) -> list[str]:
        """Apply pending migrations; returns the ones applied now. Safe to run repeatedly."""
        applied_now = []
        async with self.pool.connection() as conn:
            # Serialize concurrent starters (e.g. two replicas booting at once), including the
            # bookkeeping table's creation.
            await conn.execute("SELECT pg_advisory_lock(727001)")
            try:
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations ("
                    " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
                )
                rows = await (
                    await conn.execute("SELECT version FROM schema_migrations")
                ).fetchall()
                done = {r["version"] for r in rows}
                for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                    if path.stem in done:
                        continue
                    async with conn.transaction():
                        await conn.execute(path.read_text(encoding="utf-8"))
                        await conn.execute(
                            "INSERT INTO schema_migrations (version) VALUES (%s)", (path.stem,)
                        )
                    applied_now.append(path.stem)
            finally:
                await conn.execute("SELECT pg_advisory_unlock(727001)")
        return applied_now

    # ------------------------------------------------------------------ system versions

    async def upsert_system_version(self, sv: SystemVersion) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO system_versions (version_id, source_path, spec, prompt_hashes)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (version_id) DO NOTHING",
                (sv.version_id, sv.source_path, Jsonb(sv.spec.model_dump(mode="json")),
                 Jsonb(sv.prompt_hashes)),
            )  # fmt: skip

    # ------------------------------------------------------------------ runs

    # Status transitions only move forward: queued -> running -> terminal. Every update is
    # guarded, so a late write (e.g. cancellation after save, or a reconcile race) can never
    # overwrite a terminal status. Each returns whether the transition happened.

    async def create_run(
        self, run_id: str, scenario_id: str, system_version: str, instance_id: str | None = None
    ) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO runs (run_id, scenario_id, status, system_version, instance_id,"
                " heartbeat_at) VALUES (%s, %s, %s, %s, %s, now())",
                (run_id, scenario_id, RunStatus.queued.value, system_version, instance_id),
            )

    async def mark_running(self, run_id: str) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE runs SET status = %s, started_at = now(), heartbeat_at = now()"
                " WHERE run_id = %s AND status = %s",
                (RunStatus.running.value, run_id, RunStatus.queued.value),
            )
            return cur.rowcount == 1

    async def mark_failed(self, run_id: str, error_kind: str, error: str) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE runs SET status = %s, error_kind = %s, error = %s, finished_at = now()"
                " WHERE run_id = %s AND status = ANY(%s)",
                (RunStatus.failed.value, error_kind, error[:2000], run_id, list(ACTIVE)),
            )
            return cur.rowcount == 1

    async def heartbeat(self, instance_id: str) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE runs SET heartbeat_at = now() WHERE instance_id = %s AND status = ANY(%s)",
                (instance_id, list(ACTIVE)),
            )
            return cur.rowcount

    async def save_result(self, result: RunResult) -> bool:
        """Store the finished run and its decision records in one transaction. Returns False
        (and stores nothing) when the run is no longer active, e.g. reconciled as orphaned."""
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE runs SET status = %s, error = %s, finished_at = now(), result = %s"
                " WHERE run_id = %s AND status = ANY(%s)",
                (result.status.value, result.error,
                 Jsonb(json.loads(result.model_dump_json())), result.run_id, list(ACTIVE)),
            )  # fmt: skip
            if cur.rowcount != 1:
                return False
            for d in result.decisions:
                await self._insert_decision(conn, result.run_id, d)
            return True

    @staticmethod
    async def _insert_decision(conn: Any, run_id: str, d: DecisionRecord) -> None:
        await conn.execute(
            "INSERT INTO decision_records (run_id, decision_point, subject, decision,"
            " probability, mode, decider, system_version, error, truncated, created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, d.decision_point, d.subject, d.decision, d.probability, d.mode,
             d.decider, d.system_version, d.error, d.truncated, d.created_at),
        )  # fmt: skip

    async def get_run(self, run_id: str) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute("SELECT * FROM runs WHERE run_id = %s", (run_id,))
            return await cur.fetchone()

    async def reconcile_orphans(self, stale_after_s: float = 120.0) -> int:
        """Fail active runs whose owner stopped heartbeating (process died or was redeployed).
        Runs of live instances keep fresh heartbeats and are left alone."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE runs SET status = %s, error_kind = 'orphaned',"
                " error = 'owner stopped before the run finished', finished_at = now()"
                " WHERE status = ANY(%s)"
                " AND coalesce(heartbeat_at, created_at) < now() - make_interval(secs => %s)",
                (RunStatus.failed.value, list(ACTIVE), stale_after_s),
            )
            return cur.rowcount

    # ------------------------------------------------------------------ events

    async def append_event(self, event: RunEvent) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO run_events (run_id, seq, node, event, payload, at)"
                " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (event.run_id, event.seq, event.node, event.event, Jsonb(event.payload),
                 event.at),
            )  # fmt: skip

    async def list_events(self, run_id: str, after_seq: int = 0, limit: int = 500) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT seq, node, event, payload, at FROM run_events"
                " WHERE run_id = %s AND seq > %s ORDER BY seq LIMIT %s",
                (run_id, after_seq, limit),
            )
            return await cur.fetchall()

    async def count_decisions(self, run_id: str) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT count(*) AS n FROM decision_records WHERE run_id = %s", (run_id,)
            )
            return (await cur.fetchone())["n"]

    # ------------------------------------------------------------------ failures (R14b)

    async def record_failure(
        self, *, case_id: str, scenario_id: str, category: str, system_version: str,
        run_id: str | None = None, agent: str | None = None, detail: dict | None = None,
        git_sha: str | None = None,
    ) -> None:  # fmt: skip
        async with self.pool.connection() as conn:
            await conn.execute(
                "INSERT INTO failures (run_id, case_id, scenario_id, agent, category, detail,"
                " system_version, git_sha) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                (run_id, case_id, scenario_id, agent, category, Jsonb(detail or {}),
                 system_version, git_sha),
            )  # fmt: skip

    async def list_failures(self, system_version: str) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM failures WHERE system_version = %s ORDER BY id", (system_version,)
            )
            return await cur.fetchall()
