"""Postgres persistence for runs, run events, decisions and failures (U9).

Optional: the CLI works without a database. Migrations are numbered SQL files applied once,
in order, and recorded in schema_migrations.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from womm.models.decisions import DecisionRecord
from womm.models.run import RunEvent, RunResult, RunStatus
from womm.models.system_version import SystemVersion

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
ACTIVE = (RunStatus.queued.value, RunStatus.running.value)
_KIND_TAG = re.compile(r"\[(\w+)\]")


def _without_nul(value: Any) -> Any:
    """Postgres jsonb rejects U+0000; LLM output can contain it, so strip it before storing."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, list):
        return [_without_nul(v) for v in value]
    if isinstance(value, dict):
        return {_without_nul(k): _without_nul(v) for k, v in value.items()}
    return value


def result_error_kind(result: RunResult) -> str | None:
    """A pipeline-level failure carries its kind as a `[kind]` tag in the error text."""
    if result.status != RunStatus.failed:
        return None
    m = _KIND_TAG.search(result.error or "")
    return m.group(1) if m else "pipeline_failed"


_INSERT_EVENT = (
    "INSERT INTO failure_events (system_version, kind, case_id, fixture, split, item_id,"
    " category, touching_agents, owner, run_id, repetition, detail, judge_version, git_sha,"
    " source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _event_params(e: Any, git_sha: str | None) -> tuple:
    return (e.system_version, e.kind, e.case_id, e.fixture, e.split, e.item_id, e.category,
            e.touching_agents, e.owner, e.run_id, e.repetition, Jsonb(_without_nul(e.detail)),
            e.judge_version, e.git_sha or git_sha, e.source)  # fmt: skip


_PAYLOAD_FIELDS = {"provision_keys", "affected_actor", "mechanism", "impact", "category", "edited"}


def _feedback_payload(fb: Any) -> dict:
    return _without_nul(fb.model_dump(mode="json", include=_PAYLOAD_FIELDS))


def _history_entry(row: dict) -> dict:
    """The stored version of a mark, kept when an edit or a retraction replaces it."""
    keep = ("mark", "finding_id", "note", "payload", "failure_event_id", "golden_candidate")
    entry = {k: row[k] for k in keep}
    for k in ("modified_at", "retracted_at"):
        entry[k] = row[k].isoformat() if row[k] is not None else None
    return entry


async def _store_event(conn: Any, event: Any, *, refresh: bool = False) -> int | None:
    """Insert a mark's Failure Memory event; a second mark on the same item of the same run
    shares the first one's event. ``refresh`` (an edited mark) rewrites the shared event's
    description with the edited one."""
    if event is None:
        return None
    on_conflict = (
        "DO UPDATE SET category = EXCLUDED.category, touching_agents = EXCLUDED.touching_agents,"
        " owner = EXCLUDED.owner, detail = EXCLUDED.detail"
        if refresh
        else "DO UPDATE SET kind = EXCLUDED.kind"
    )
    cur = await conn.execute(
        _INSERT_EVENT + f" ON CONFLICT (run_id, kind, item_id) {on_conflict} RETURNING id",
        _event_params(event, None),
    )
    return (await cur.fetchone())["id"]


async def _drop_unused_event(conn: Any, event_id: int | None) -> None:
    """Delete a Failure Memory event no mark refers to any more (a mark that was edited or
    retracted); an event another mark shares stays."""
    if event_id is None:
        return
    await conn.execute(
        "DELETE FROM failure_events WHERE id = %s AND NOT EXISTS"
        " (SELECT 1 FROM analyst_feedback WHERE failure_event_id = %s)",
        (event_id, event_id),
    )


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

    async def get_role(self, version_id: str, role: str) -> dict | None:
        """One role's config from a stored system version (None if unknown)."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT spec -> %s AS role FROM system_versions WHERE version_id = %s",
                (role, version_id),
            )
            row = await cur.fetchone()
            return row["role"] if row else None

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

    async def heartbeat(self, instance_id: str, run_ids: list[str]) -> int:
        """Refresh only the runs this process is still executing, so a run whose final write
        failed stops heartbeating and the stale-owner reconcile fails it."""
        if not run_ids:
            return 0
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "UPDATE runs SET heartbeat_at = now()"
                " WHERE instance_id = %s AND run_id = ANY(%s) AND status = ANY(%s)",
                (instance_id, run_ids, list(ACTIVE)),
            )
            return cur.rowcount

    async def save_result(self, result: RunResult) -> bool:
        """Store the finished run and its decision records in one transaction. Returns False
        (and stores nothing) when the run is no longer active, e.g. reconciled as orphaned."""
        clean = _without_nul(json.loads(result.model_dump_json()))
        result = RunResult.model_validate(clean)
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "UPDATE runs SET status = %s, error = %s, error_kind = %s, finished_at = now(),"
                " result = %s WHERE run_id = %s AND status = ANY(%s)",
                (result.status.value, result.error, result_error_kind(result),
                 Jsonb(clean), result.run_id, list(ACTIVE)),
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
            " probability, mode, decider, system_version, error, truncated, created_at,"
            " model, latency_s, input_tokens, output_tokens)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (run_id, d.decision_point, d.subject, d.decision, d.probability, d.mode,
             d.decider, d.system_version, d.error, d.truncated, d.created_at,
             d.model, d.latency_s, d.input_tokens, d.output_tokens),
        )  # fmt: skip

    async def list_runs(self, limit: int = 20) -> list[dict]:
        """Newest first, with the summary fields the console's run table needs."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT run_id, scenario_id, status, system_version, error_kind, created_at,"
                " started_at, finished_at,"
                " extract(epoch FROM (finished_at - started_at))::float8 AS duration_s,"
                " CASE WHEN result->'dossier' IS NOT NULL AND result->'dossier' != 'null'::jsonb"
                "   THEN jsonb_array_length(result->'dossier'->'impacts') END AS impacts,"
                " result->'grounding' AS grounding"
                " FROM runs ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return await cur.fetchall()

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
                (event.run_id, event.seq, event.node, event.event,
                 Jsonb(_without_nul(event.payload)), event.at),
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

    # ------------------------------------------------------------------ Failure Memory (U1)

    async def record_failure_events(
        self, events: list[Any], runs: list[Any], git_sha: str | None = None
    ) -> int:
        """Store train/val failure events and scored runs (``womm.evolve.failure_memory``) in
        one transaction; re-recording the same run is a no-op. Returns the events written. A
        holdout split fails on the tables' CHECK constraints. ``git_sha`` is the fallback for
        records that carry none."""
        written = 0
        async with self.pool.connection() as conn, conn.transaction():
            for r in runs:
                await conn.execute(
                    "INSERT INTO failure_case_runs (system_version, case_id, fixture, split,"
                    " run_id, repetition, judge_version, git_sha)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (r.system_version, r.case_id, r.fixture, r.split, r.run_id, r.repetition,
                     r.judge_version, r.git_sha or git_sha),
                )  # fmt: skip
            for e in events:
                cur = await conn.execute(
                    _INSERT_EVENT + " ON CONFLICT DO NOTHING", _event_params(e, git_sha)
                )
                written += cur.rowcount
        return written

    async def failure_memory(self, system_version: str) -> tuple[list[Any], list[Any]]:
        """The stored failure events and scored runs of one system version."""
        from womm.evolve.failure_memory import CaseRun, FailureEvent

        async with self.pool.connection() as conn:
            events = await (await conn.execute(
                "SELECT system_version, kind, case_id, fixture, split, item_id, category,"
                " touching_agents, owner, run_id, repetition, detail, judge_version, git_sha"
                " FROM failure_events WHERE system_version = %s ORDER BY id", (system_version,),
            )).fetchall()  # fmt: skip
            runs = await (await conn.execute(
                "SELECT system_version, case_id, fixture, split, run_id, repetition,"
                " judge_version, git_sha FROM failure_case_runs WHERE system_version = %s"
                " ORDER BY case_id, run_id",
                (system_version,),
            )).fetchall()  # fmt: skip
        return (
            [FailureEvent.model_validate(e) for e in events],
            [CaseRun.model_validate(r) for r in runs],
        )

    async def failure_patterns(self, system_version: str) -> list[dict]:
        from womm.evolve.failure_memory import patterns

        return patterns(*await self.failure_memory(system_version))

    # ------------------------------------------------------------------ analyst feedback (U11)

    async def record_analyst_feedback(self, record: Any) -> str:
        """Store one resolved analyst mark (``womm.evolve.feedback.FeedbackRecord``) and its
        Failure Memory event in one transaction. Returns ``new``; ``unchanged`` when the mark
        was imported before and LangSmith has no newer modification; ``updated`` when a newer
        modification (or a retracted mark showing up again) replaced the stored one, its
        previous version kept in ``history`` and its event replaced; ``updated_staged`` when
        that mark's candidate was already staged into a draft, which is not changed here. The
        run must be a scored train/val run in Failure Memory: anything else fails on the
        foreign key or the split CHECK, and nothing is written."""
        try:
            return await self._record_analyst_feedback(record)
        except psycopg.errors.UniqueViolation:  # a concurrent import stored it first
            return "unchanged"

    async def _record_analyst_feedback(self, record: Any) -> str:
        fb, run = record.feedback, record.run
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT * FROM analyst_feedback WHERE feedback_id = %s FOR UPDATE",
                (fb.feedback_id,),
            )
            old = await cur.fetchone()
            if old is None:
                event_id = await _store_event(conn, record.event)
                await conn.execute(
                    "INSERT INTO analyst_feedback (feedback_id, mark, system_version, case_id,"
                    " fixture, split, run_id, trace_run_id, finding_id, analyst, note, payload,"
                    " failure_event_id, golden_candidate, created_at, modified_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (fb.feedback_id, fb.mark, run.system_version, run.case_id, run.fixture,
                     run.split, run.run_id, fb.trace_run_id, fb.finding_id, fb.analyst,
                     _without_nul(fb.note), Jsonb(_feedback_payload(fb)), event_id,
                     record.golden_candidate, fb.created_at, fb.modified_at),
                )  # fmt: skip
                return "new"
            newer = fb.modified_at is not None and (
                old["modified_at"] is None or fb.modified_at > old["modified_at"]
            )
            if not newer and old["retracted_at"] is None:
                return "unchanged"
            event_id = await _store_event(conn, record.event, refresh=True)
            staged = old["golden_candidate"] == "staged"
            candidate = "staged" if staged else record.golden_candidate
            await conn.execute(
                "UPDATE analyst_feedback SET mark = %s, trace_run_id = %s, finding_id = %s,"
                " analyst = %s, note = %s, payload = %s, failure_event_id = %s,"
                " golden_candidate = %s, modified_at = %s, retracted_at = NULL,"
                " history = history || %s WHERE feedback_id = %s",
                (fb.mark, fb.trace_run_id, fb.finding_id, fb.analyst, _without_nul(fb.note),
                 Jsonb(_feedback_payload(fb)), event_id, candidate, fb.modified_at,
                 Jsonb([_history_entry(old)]), fb.feedback_id),
            )  # fmt: skip
            if old["failure_event_id"] != event_id:
                await _drop_unused_event(conn, old["failure_event_id"])
        return "updated_staged" if staged else "updated"

    async def retract_analyst_feedback(self, feedback_id: str) -> str:
        """Retract a mark that is gone from LangSmith: the row stays (the audit record, its last
        version also appended to ``history``) with ``retracted_at`` set, its Failure Memory
        event is removed unless another mark shares it, and it is no longer a queued candidate.
        Returns ``retracted``, ``retracted_staged`` (its candidate is already in a draft, which
        is not changed here) or ``unchanged`` (unknown or already retracted)."""
        async with self.pool.connection() as conn, conn.transaction():
            cur = await conn.execute(
                "SELECT * FROM analyst_feedback WHERE feedback_id = %s FOR UPDATE",
                (feedback_id,),
            )
            old = await cur.fetchone()
            if old is None or old["retracted_at"] is not None:
                return "unchanged"
            staged = old["golden_candidate"] == "staged"
            await conn.execute(
                "UPDATE analyst_feedback SET failure_event_id = NULL, retracted_at = now(),"
                " golden_candidate = CASE WHEN golden_candidate = 'staged' THEN 'staged' END,"
                " history = history || %s WHERE feedback_id = %s",
                (Jsonb([_history_entry(old)]), feedback_id),
            )
            await _drop_unused_event(conn, old["failure_event_id"])
        return "retracted_staged" if staged else "retracted"

    async def analyst_feedback(self, system_version: str) -> list[dict]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM analyst_feedback WHERE system_version = %s"
                " ORDER BY created_at, feedback_id",
                (system_version,),
            )
            return await cur.fetchall()

    async def analyst_feedback_by_id(self, feedback_ids: list[str]) -> dict[str, dict]:
        """The stored marks with these ids (retracted ones included), by feedback id: the
        publish script cross-checks staged analyst candidates against them."""
        if not feedback_ids:
            return {}
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM analyst_feedback WHERE feedback_id = ANY(%s)", (list(feedback_ids),)
            )
            return {r["feedback_id"]: r for r in await cur.fetchall()}

    async def queued_golden_candidates(self) -> list[dict]:
        """Missing-impact marks not yet staged into a golden draft, oldest first."""
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT * FROM analyst_feedback WHERE golden_candidate = 'queued'"
                " ORDER BY created_at, feedback_id"
            )
            return await cur.fetchall()

    async def mark_candidate_staged(self, feedback_id: str, draft: str) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "UPDATE analyst_feedback SET golden_candidate = 'staged', golden_draft = %s"
                " WHERE feedback_id = %s AND golden_candidate = 'queued'",
                (draft, feedback_id),
            )

    # ------------------------------------------------------------------ evolution page (R36 p4)

    async def evolution_archive(self) -> list[dict]:
        """Every archived version, oldest first, without spec, diff or proposer bodies: the
        lineage reads only the ids, the origin and the id of an added expert (``new_expert``)."""
        async with self.pool.connection() as conn:
            return await (await conn.execute(
                "SELECT version_id, parent_id, twin_of, cycle_id, origin, name, created_at,"
                " (SELECT op ->> 'id' FROM jsonb_array_elements(coalesce(diff -> 'ops',"
                " '[]'::jsonb)) op WHERE op ->> 'op' = 'add_expert' LIMIT 1) AS new_expert"
                " FROM sv_archive ORDER BY created_at, version_id"
            )).fetchall()  # fmt: skip

    async def evolution_candidate(self, version_id: str) -> dict | None:
        """One archived version with its spec, diff (ops and rendered prompt diffs) and
        proposer; no prompt texts."""
        async with self.pool.connection() as conn:
            return await (await conn.execute(
                "SELECT version_id, parent_id, twin_of, cycle_id, origin, name, created_at, spec,"
                " diff, proposer FROM sv_archive WHERE version_id = %s",
                (version_id,),
            )).fetchone()  # fmt: skip

    async def evolution_metrics(
        self,
        *,
        version_ids: list[str] | None = None,
        splits: list[str] | tuple[str, ...] = ("diff_check",),
    ) -> list[dict]:
        """Archive metrics (train, val, R37 diff check; never holdout): per version, the latest
        full-split batch per (split, judge), for ``splits`` (default: the diff check the
        lineage needs) and, when given, only ``version_ids``."""
        async with self.pool.connection() as conn:
            return await (await conn.execute(
                "SELECT version_id, split, judge_version, batch_id, level, subject, metric, n,"
                " mean, sd, updated_at FROM sv_metrics WHERE (version_id, batch_id) IN ("
                "  SELECT DISTINCT ON (version_id, split, judge_version) version_id, batch_id"
                "  FROM sv_metrics WHERE full_split AND split = ANY(%(splits)s)"
                "  AND (%(ids)s::text[] IS NULL OR version_id = ANY(%(ids)s))"
                "  ORDER BY version_id, split, judge_version, updated_at DESC, batch_id DESC"
                ") AND split = ANY(%(splits)s)"
                " ORDER BY version_id, split, judge_version, level, subject, metric",
                {"splits": list(splits), "ids": version_ids},
            )).fetchall()  # fmt: skip

    async def promotion_decisions(self) -> list[dict]:
        """Published decision summaries (aggregates only), oldest first."""
        async with self.pool.connection() as conn:
            return await (await conn.execute(
                "SELECT gate_id, created_at, cycle_id, candidate_version, incumbent_version, mode,"
                " deployable, decision, label, reasons, notes, deltas, n_proposals, flags, r37"
                " FROM promotion_decisions ORDER BY created_at, gate_id"
            )).fetchall()  # fmt: skip
