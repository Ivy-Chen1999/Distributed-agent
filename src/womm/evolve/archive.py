"""Candidate archive and lineage (U3, R27).

Every candidate is stored with its parent, the ConfigDiff that produced it (ops plus the
rendered diff), its provenance and its train/val metrics; not only the best. Prompt texts are
stored once per hash, so ``load_candidate`` rebuilds any archived SystemVersion from the
database alone, with no prompt file on disk.

Holdout metrics are excluded by construction: ``sv_metrics.split`` is constrained to train, val
and the R37 diff check, and nothing here reads ``womm.eval.holdout``.
"""

from __future__ import annotations

import hashlib
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from psycopg.types.json import Jsonb

from womm.api.db import Database
from womm.eval.evaluators import CaseScore
from womm.evolve.edits import ConfigDiff, build_candidate, render_diff
from womm.models.system_version import (
    SystemVersion,
    SystemVersionSpec,
    build_system_version_from_texts,
    derive_system_version,
    load_system_version,
)

Origin = Literal["seed", "gepa", "topology", "twin", "manual"]
SEED_FILES = ("v1.0-unscoped.yaml", "v1.0-unscoped-api.yaml")
METRICS = ("coverage", "omissions_addressed", "grounding")
# The population readers use: per (version, split, judge), the latest batch that covered the
# whole split. Partial batches (e.g. GEPA minibatches) stay stored but are not read here.
LATEST_METRICS = (
    "SELECT version_id, split, judge_version, git_sha, batch_id, level, subject, metric, n,"
    " mean, sd FROM sv_metrics WHERE version_id = %s AND batch_id IN ("
    "  SELECT DISTINCT ON (split, judge_version) batch_id FROM sv_metrics"
    "  WHERE version_id = %s AND full_split"
    "  ORDER BY split, judge_version, updated_at DESC, batch_id DESC"
    ") ORDER BY split, judge_version, level, subject, metric"
)
_ROW = ("version_id, parent_id, twin_of, cycle_id, origin, name, spec, prompt_map, diff,"
        " proposer, created_at")  # fmt: skip


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Archive:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def archive(
        self,
        sv: SystemVersion,
        *,
        origin: Origin,
        parent_id: str | None = None,
        twin_of: str | None = None,
        cycle_id: str | None = None,
        diff: dict | None = None,
        proposer: dict | None = None,
    ) -> bool:
        """Store ``sv``; returns False (and changes nothing) when it is already archived."""
        prompt_map = {path: _sha(text) for path, text in sv.prompts.items()}
        async with self.db.pool.connection() as conn, conn.transaction():
            for path, text in sv.prompts.items():
                await conn.execute(
                    "INSERT INTO sv_prompts (sha256, text) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                    (prompt_map[path], text),
                )
            cur = await conn.execute(
                "INSERT INTO sv_archive (version_id, parent_id, twin_of, cycle_id, origin, name,"
                " spec, prompt_map, diff, proposer) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,"
                " %s) ON CONFLICT (version_id) DO NOTHING",
                (sv.version_id, parent_id, twin_of, cycle_id, origin, sv.spec.name,
                 Jsonb(sv.spec.model_dump(mode="json")), Jsonb(prompt_map),
                 Jsonb(diff) if diff is not None else None,
                 Jsonb(proposer) if proposer is not None else None),
            )  # fmt: skip
            return cur.rowcount == 1

    async def get(self, version_id: str) -> dict | None:
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_ROW} FROM sv_archive WHERE version_id = %s", (version_id,)
            )
            return await cur.fetchone()

    async def load_candidate(self, version_id: str) -> SystemVersion:
        """Rebuild an archived SystemVersion from the database alone."""
        row = await self.get(version_id)
        if row is None:
            raise KeyError(f"{version_id} is not archived")
        shas = sorted(set(row["prompt_map"].values()))
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT sha256, text FROM sv_prompts WHERE sha256 = ANY(%s)", (shas,)
            )
            texts = {r["sha256"]: r["text"] for r in await cur.fetchall()}
        prompts = {path: texts[sha] for path, sha in row["prompt_map"].items()}
        sv = build_system_version_from_texts(SystemVersionSpec.model_validate(row["spec"]), prompts)
        if sv.version_id != version_id:
            raise RuntimeError(f"archive row {version_id} rebuilds as {sv.version_id}")
        return sv

    async def lineage(self, version_id: str) -> list[dict]:
        """Root first, ending with ``version_id``. An api twin hangs off its dev candidate."""
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(
                "WITH RECURSIVE up AS ("
                f"  SELECT {_ROW}, 0 AS depth FROM sv_archive WHERE version_id = %s"
                "  UNION ALL"
                "  SELECT a.version_id, a.parent_id, a.twin_of, a.cycle_id, a.origin, a.name,"
                "    a.spec, a.prompt_map, a.diff, a.proposer, a.created_at, up.depth + 1"
                "  FROM sv_archive a JOIN up"
                "    ON a.version_id = coalesce(up.parent_id, up.twin_of) AND up.depth < 1000"
                f") SELECT {_ROW} FROM up ORDER BY depth DESC",
                (version_id,),
            )
            return await cur.fetchall()

    async def children(self, version_id: str) -> list[dict]:
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(
                f"SELECT {_ROW} FROM sv_archive WHERE parent_id = %s ORDER BY created_at",
                (version_id,),
            )
            return await cur.fetchall()

    async def record_metrics(
        self,
        version_id: str,
        split: str,
        rows: list[dict],
        *,
        batch_id: str,
        judge_version: str,
        git_sha: str,
        full_split: bool,
    ) -> None:
        """Store the metric rows (``level``, ``subject``, ``metric``, ``n``, ``mean``, ``sd``) of
        one batch: one population per (batch, judge, code), never overwriting another batch's.
        Split-level rows need a batch that covers the whole split."""
        if not full_split and any(r["level"] == "split" for r in rows):
            raise ValueError("split-level metrics come from full-split batches only")
        async with self.db.pool.connection() as conn, conn.transaction():
            for r in rows:
                await conn.execute(
                    "INSERT INTO sv_metrics (version_id, split, judge_version, git_sha, batch_id,"
                    " full_split, level, subject, metric, n, mean, sd)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (version_id, split, judge_version, git_sha, batch_id, level,"
                    " subject, metric) DO UPDATE SET n = excluded.n, mean = excluded.mean,"
                    " sd = excluded.sd, updated_at = now()",
                    (version_id, split, judge_version, git_sha, batch_id, full_split,
                     r["level"], r["subject"], r["metric"], r["n"], r["mean"], r["sd"]),
                )  # fmt: skip

    async def metrics(self, version_id: str, judge_version: str | None = None) -> list[dict]:
        """The metrics of the latest full-split batch per (split, judge); one judge's only
        when ``judge_version`` is given."""
        async with self.db.pool.connection() as conn:
            cur = await conn.execute(LATEST_METRICS, (version_id, version_id))
            rows = await cur.fetchall()
        return [r for r in rows if judge_version is None or r["judge_version"] == judge_version]


async def archive_child(
    archive: Archive,
    parent: SystemVersion,
    diff: ConfigDiff,
    *,
    origin: Origin,
    cycle_id: str | None = None,
    proposer: dict | None = None,
) -> SystemVersion:
    """Build the candidate ``diff`` describes and archive it under ``parent``."""
    child = build_candidate(parent, diff)
    await archive.archive(
        child, origin=origin, parent_id=parent.version_id, cycle_id=cycle_id, proposer=proposer,
        diff={"ops": diff.ops_json(), "rendered": render_diff(parent, child)},
    )  # fmt: skip
    return child


async def archive_twin(archive: Archive, sv: SystemVersion, cycle_id: str | None = None):
    """The api-backend twin of ``sv`` (every role on ``api``), archived with ``twin_of``."""
    twin = derive_system_version(sv, Path("."), backends=dict.fromkeys(sv.spec.roles(), "api"))
    await archive.archive(twin, origin="twin", twin_of=sv.version_id, cycle_id=cycle_id)
    return twin


async def seed_archive(archive: Archive, repo_root: Path) -> list[SystemVersion]:
    """Archive the self-evolution base, ``v1.0-unscoped``, and its api twin (origin seed)."""
    dev, api = (load_system_version(repo_root / "system_versions" / f, repo_root)
                for f in SEED_FILES)  # fmt: skip
    await archive.archive(dev, origin="seed")
    await archive.archive(api, origin="seed", twin_of=dev.version_id)
    return [dev, api]


def _stats(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "sd": statistics.stdev(values) if len(values) > 1 else None,
    }


def metric_rows(
    scores: list[CaseScore], case_fixture: dict[str, str], *, split_level: bool = True
) -> list[dict]:
    """Per-case means over repetitions, then per-proposal and (with ``split_level``, for a
    batch covering the whole split) per-split means of case means. Errored runs and missing
    metrics are skipped, not zeroed."""
    rows: list[dict] = []
    for metric in METRICS:
        per_case: dict[str, list[float]] = defaultdict(list)
        for s in scores:
            value = getattr(s, metric)
            if s.outcome == "scored" and value is not None:
                per_case[s.case_id].append(value)
        if not per_case:
            continue
        case_means = {c: statistics.fmean(v) for c, v in per_case.items()}
        per_proposal: dict[str, list[float]] = defaultdict(list)
        for case_id, mean in case_means.items():
            per_proposal[case_fixture[case_id]].append(mean)
        rows += [{"level": "case", "subject": c, "metric": metric, **_stats(v)}
                 for c, v in sorted(per_case.items())]  # fmt: skip
        rows += [{"level": "proposal", "subject": p, "metric": metric, **_stats(v)}
                 for p, v in sorted(per_proposal.items())]  # fmt: skip
        if split_level:
            rows.append({"level": "split", "subject": "", "metric": metric,
                         **_stats(list(case_means.values()))})  # fmt: skip
    return rows
