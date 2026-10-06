"""PlannerView (U6): the Improvement Planner's only data access, train/val by construction.

Holdout cases, holdout results and promotion decisions must never reach the Improvement Planner
(R23, R28, AE3, AE4). That is enforced by structure, not convention:

- **Import boundary.** Every ``womm.evolve`` module except ``promotion`` is Planner-side
  (a new module is by default) and never imports ``womm.eval.holdout`` or
  ``womm.evolve.promotion``, directly, transitively or through ``importlib.import_module`` /
  ``__import__`` string literals; ``tests/evolve/test_planner_boundary.py`` walks the graph.
- **Process boundary.** A PlannerView refuses to exist in a process whose environment holds
  ``HOLDOUT_DATABASE_URL``, and so does every ``womm evolve`` command (checked in
  ``womm.cli.main`` before any handler runs); only the promotion command needs that URL.
- **Query boundary.** Every query is a fixed string in ``QUERIES`` over ``ALLOWED_TABLES``; there
  is no free-form SQL, and the connection is read-only. Metrics are train/val only, so the R37
  diff check is not something the Planner can optimise.
- **Content boundary.** Archive rows carry no decision fields; the parent id is the only
  promotion-derived fact the Planner sees.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from womm.eval.golden import GoldenCase, GoldenError, load_all_golden
from womm.evolve.failure_memory import CaseRun, FailureEvent, patterns
from womm.models.run import RunResult

# The holdout module's HOLDOUT_URL_ENV, spelled out here so this module need not import it.
HOLDOUT_URL_ENV = "HOLDOUT_DATABASE_URL"
ALLOWED_TABLES = frozenset(
    {"failure_events", "failure_case_runs", "sv_archive", "sv_prompts", "sv_metrics"}
)
PLANNER_SPLITS = ("train", "val")
_ARCHIVE = "version_id, parent_id, twin_of, cycle_id, origin, name, spec, prompt_map, diff"

QUERIES: dict[str, str] = {
    "failure_events": (
        "SELECT system_version, kind, case_id, fixture, split, item_id, category,"
        " touching_agents, owner, run_id, repetition, detail, judge_version, git_sha"
        " FROM failure_events WHERE system_version = %s AND split = ANY(%s) ORDER BY id"
    ),
    "case_runs": (
        "SELECT system_version, case_id, fixture, split, run_id, repetition, judge_version,"
        " git_sha FROM failure_case_runs WHERE system_version = %s AND split = ANY(%s)"
        " ORDER BY case_id, run_id"
    ),
    "candidate": f"SELECT {_ARCHIVE} FROM sv_archive WHERE version_id = %s",
    "prompts": "SELECT sha256, text FROM sv_prompts WHERE sha256 = ANY(%s)",
    "lineage": (
        "WITH RECURSIVE up AS ("
        f" SELECT {_ARCHIVE}, 0 AS depth FROM sv_archive WHERE version_id = %s"
        " UNION ALL"
        " SELECT a.version_id, a.parent_id, a.twin_of, a.cycle_id, a.origin, a.name, a.spec,"
        "  a.prompt_map, a.diff, up.depth + 1 FROM sv_archive a JOIN up"
        "  ON a.version_id = coalesce(up.parent_id, up.twin_of) AND up.depth < 1000"
        f") SELECT {_ARCHIVE} FROM up ORDER BY depth DESC"
    ),
    "children": f"SELECT {_ARCHIVE} FROM sv_archive WHERE parent_id = %s ORDER BY created_at",
    # The latest full-split batch per (split, judge); see womm.evolve.archive.LATEST_METRICS.
    "metrics": (
        "SELECT version_id, split, judge_version, git_sha, batch_id, level, subject, metric, n,"
        " mean, sd FROM sv_metrics WHERE version_id = %s AND split = ANY(%s) AND batch_id IN ("
        "  SELECT DISTINCT ON (split, judge_version) batch_id FROM sv_metrics"
        "  WHERE version_id = %s AND split = ANY(%s) AND full_split"
        "  ORDER BY split, judge_version, updated_at DESC, batch_id DESC"
        ") ORDER BY split, judge_version, level, subject, metric"
    ),
    "train_val_run": (
        "SELECT run_id FROM failure_case_runs WHERE run_id = %s AND split = ANY(%s) LIMIT 1"
    ),
}


class HoldoutEnvRefused(RuntimeError):
    pass


def refuse_holdout_env(env: Mapping[str, str] | None = None) -> None:
    """Refuse to run Planner-side code in a process that could reach the holdout database."""
    env = os.environ if env is None else env
    if env.get(HOLDOUT_URL_ENV):
        raise HoldoutEnvRefused(
            f"{HOLDOUT_URL_ENV} is set in this process's environment; the Improvement Planner "
            "side must never be able to reach the holdout. Unset it (or remove it from .env) "
            "for evolution commands; only the promotion command uses it."
        )


class PlannerView:
    def __init__(
        self,
        database_url: str,
        *,
        runs_dir: Path,
        env: Mapping[str, str] | None = None,
        load_cases: Callable[[str], list[GoldenCase]] | None = None,
    ) -> None:
        refuse_holdout_env(env)
        self.runs_dir = runs_dir
        self._load_cases = load_cases or (lambda split: load_all_golden(split=split))
        self._pool = AsyncConnectionPool(
            database_url, min_size=1, max_size=2, open=False,
            kwargs={"row_factory": dict_row, "autocommit": True,
                    "options": "-c default_transaction_read_only=on -c statement_timeout=30000"},
        )  # fmt: skip

    async def open(self) -> None:
        await self._pool.open(wait=True)

    async def close(self) -> None:
        await self._pool.close()

    async def __aenter__(self) -> PlannerView:
        await self.open()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _all(self, name: str, *params: Any) -> list[dict]:
        async with self._pool.connection() as conn:
            return await (await conn.execute(QUERIES[name], params)).fetchall()

    # ------------------------------------------------------------ Failure Memory

    async def failure_events(self, system_version: str) -> list[FailureEvent]:
        rows = await self._all("failure_events", system_version, list(PLANNER_SPLITS))
        return [FailureEvent.model_validate(r) for r in rows]

    async def case_runs(self, system_version: str) -> list[CaseRun]:
        rows = await self._all("case_runs", system_version, list(PLANNER_SPLITS))
        return [CaseRun.model_validate(r) for r in rows]

    async def failure_patterns(self, system_version: str) -> list[dict]:
        return patterns(
            await self.failure_events(system_version), await self.case_runs(system_version)
        )

    # ------------------------------------------------------------ archive

    async def candidate(self, version_id: str) -> dict | None:
        """Spec, diff and prompt texts by path of one archived version."""
        rows = await self._all("candidate", version_id)
        if not rows:
            return None
        row = rows[0]
        shas = sorted(set(row["prompt_map"].values()))
        texts = {r["sha256"]: r["text"] for r in await self._all("prompts", shas)}
        prompts = {path: texts[sha] for path, sha in row.pop("prompt_map").items()}
        return row | {"prompts": prompts}

    async def lineage(self, version_id: str) -> list[dict]:
        return [_slim(r) for r in await self._all("lineage", version_id)]

    async def children(self, version_id: str) -> list[dict]:
        return [_slim(r) for r in await self._all("children", version_id)]

    async def metrics(self, version_id: str, judge_version: str | None = None) -> list[dict]:
        """Train/val metrics only (not the R37 diff check, never the holdout): the latest
        full-split batch per (split, judge), one judge's only when ``judge_version`` is given."""
        splits = list(PLANNER_SPLITS)
        rows = await self._all("metrics", version_id, splits, version_id, splits)
        return [r for r in rows if judge_version is None or r["judge_version"] == judge_version]

    # ------------------------------------------------------------ golden cases

    def cases(self, split: str) -> dict[str, GoldenCase]:
        """Train or val golden cases by id; the train/val answers are visible by design, the
        holdout never is. Cases the loader returns from another split are dropped."""
        if split not in PLANNER_SPLITS:
            raise GoldenError(f"PlannerView reads train/val cases only, not {split!r}")
        return {c.case_id: c for c in self._load_cases(split) if c.split == split}

    # ------------------------------------------------------------ runs

    async def run(self, run_id: str) -> RunResult | None:
        """A saved run from ``runs/``, only when it is a scored train/val run."""
        if not await self._all("train_val_run", run_id, list(PLANNER_SPLITS)):
            return None
        path = self.runs_dir / f"{run_id}.json"
        if path.parent != self.runs_dir or not path.is_file():
            return None
        return RunResult.model_validate_json(path.read_text(encoding="utf-8"))


def _slim(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "prompt_map"}
