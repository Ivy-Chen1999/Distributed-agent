"""Publish a local evolution archive to a hosted console (R36 page 4).

The dev cycle runs locally; the hosted console's Evolution page reads ``sv_archive``,
``sv_metrics`` and ``sv_prompts``. ``womm evolve publish`` exports those rows from DATABASE_URL
(one cycle with ``--cycle``, plus its ancestors and twins) and posts them to
``POST /evolution/archive``, which checks the bundle as a whole and inserts it in one
transaction.

Checks: every version must rebuild from its spec and prompt texts to its own ``version_id``
(content-addressed, as ``Archive.load_candidate`` checks), parents, twins and prompts must be in
the bundle, and each ``diff`` must have the shape the Evolution page reads. Rows already stored
are never overwritten: the response counts them as identical or different, so a stale or
divergent prod copy is visible. Original timestamps are kept, since readers take the latest
full-split batch by ``updated_at``.

Train/val (and R37 diff-check) data only, like the tables: a holdout split is refused by the
model and by the tables' CHECK constraints. Promotion decisions are not published here; they
come from the holdout side's publish_summary.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any, Literal

import httpx
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from womm.api.db import Database
from womm.eval.publish import TOKEN_ENV, URL_ENV, PublishError
from womm.models.system_version import SystemVersionSpec, build_system_version_from_texts

MAX_VERSIONS = 500
MAX_METRICS = 100_000
MAX_PROMPTS = 5_000
MAX_PROMPT_CHARS = 20_000_000  # all prompt texts of one bundle together
MAX_JSON_CHARS = {"spec": 200_000, "diff": 2_000_000, "proposer": 20_000}
TIMEOUT_S = 300.0
MAX_N = 1_000_000
MAX_VALUE = 1e6
VERSION_ID = r"^sv_[0-9a-f]{12}$"
SHA256 = r"^[0-9a-f]{64}$"
# Bounded identifiers: batch, judge, git and case ids are short machine strings.
Ident = Field(min_length=1, max_length=200)
TABLES = ("versions", "metrics", "prompts")


class AddExpertOp(BaseModel):
    """What the Evolution page reads from an ``add_expert`` op."""

    model_config = ConfigDict(extra="allow")

    op: Literal["add_expert"]
    id: str
    domain: str
    router_gloss: str
    prompt_text: str


class RenderedDiff(BaseModel):
    model_config = ConfigDict(extra="allow")

    summary: dict[str, Any] | None = None
    prompts: dict[str, str] = {}


class DiffBody(BaseModel):
    """The stored ``diff`` as the Evolution page reads it (lineage SQL, candidate detail and
    the prompt-diff panel); other keys pass through."""

    model_config = ConfigDict(extra="allow")

    ops: list[dict[str, Any]] = []
    rendered: RenderedDiff | None = None
    rationale: str | dict[str, str] | None = None
    target_pattern: str | dict[str, Any] | None = None

    @field_validator("ops")
    @classmethod
    def _ops(cls, ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
        for op in ops:
            if not isinstance(op.get("op"), str):
                raise ValueError("every op needs an 'op' name")
            if op["op"] == "add_expert":
                AddExpertOp.model_validate(op)
        return ops


def _bounded(name: str, value: Any) -> None:
    if value is not None and len(json.dumps(value)) > MAX_JSON_CHARS[name]:
        raise ValueError(f"{name} is larger than {MAX_JSON_CHARS[name]} characters of JSON")


class ArchiveRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(pattern=VERSION_ID)
    parent_id: str | None = Field(default=None, pattern=VERSION_ID)
    twin_of: str | None = Field(default=None, pattern=VERSION_ID)
    cycle_id: str | None = Field(default=None, max_length=200)
    origin: Literal["seed", "gepa", "topology", "twin", "manual"]
    name: str = Ident
    spec: dict[str, Any]
    prompt_map: dict[str, str]
    diff: dict[str, Any] | None = None
    proposer: dict[str, Any] | None = None
    created_at: datetime

    @field_validator("spec")
    @classmethod
    def _spec(cls, spec: dict[str, Any]) -> dict[str, Any]:
        _bounded("spec", spec)
        SystemVersionSpec.model_validate(spec)
        return spec

    @field_validator("diff")
    @classmethod
    def _diff(cls, diff: dict[str, Any] | None) -> dict[str, Any] | None:
        _bounded("diff", diff)
        if diff is not None:
            DiffBody.model_validate(diff)
        return diff  # stored as sent; the model only checks its shape

    @field_validator("proposer")
    @classmethod
    def _proposer(cls, proposer: dict[str, Any] | None) -> dict[str, Any] | None:
        _bounded("proposer", proposer)
        return proposer


class MetricRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(pattern=VERSION_ID)
    split: Literal["train", "val", "diff_check"]
    judge_version: str = Ident
    git_sha: str = Ident
    batch_id: str = Ident
    full_split: bool
    level: Literal["case", "proposal", "split"]
    subject: str = Field(max_length=200)
    metric: str = Ident
    # Bounded so the page's pooled-noise SQL (sum of sd*sd*(n-1)) can never overflow; real
    # rows are counts and scores in [0, 1].
    n: int = Field(ge=0, le=MAX_N)
    mean: float | None = Field(allow_inf_nan=False, ge=-MAX_VALUE, le=MAX_VALUE)
    sd: float | None = Field(allow_inf_nan=False, ge=0, le=MAX_VALUE)
    updated_at: datetime

    def key(self) -> tuple:
        return (self.version_id, self.split, self.judge_version, self.git_sha, self.batch_id,
                self.level, self.subject, self.metric)  # fmt: skip


class PromptRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(pattern=SHA256)
    text: str = Field(max_length=200_000)


class EvolutionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    versions: list[ArchiveRow] = Field(min_length=1, max_length=MAX_VERSIONS)
    metrics: list[MetricRow] = Field(max_length=MAX_METRICS)
    prompts: list[PromptRow] = Field(max_length=MAX_PROMPTS)


def check_bundle(bundle: EvolutionBundle) -> list[ArchiveRow]:
    """The bundle's versions, parents and twins first; raises PublishError unless the bundle is
    self-contained and every version is what its id says."""
    texts = {p.sha256: p.text for p in bundle.prompts}
    if sum(len(t) for t in texts.values()) > MAX_PROMPT_CHARS:
        raise PublishError(f"prompt texts exceed {MAX_PROMPT_CHARS} characters in total")
    for sha, text in texts.items():
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != sha:
            raise PublishError(f"prompt {sha[:12]} does not match its text")
    versions = {v.version_id: v for v in bundle.versions}
    if len(versions) != len(bundle.versions):
        raise PublishError("a version is listed twice")
    for v in bundle.versions:
        for ref, what in ((v.parent_id, "parent"), (v.twin_of, "twin")):
            if ref is not None and ref not in versions:
                raise PublishError(f"{v.version_id}: {what} {ref} is not in the bundle")
        missing = sorted(set(v.prompt_map.values()) - set(texts))
        if missing:
            raise PublishError(f"{v.version_id}: prompt {missing[0][:12]} is not in the bundle")
        _check_content(v, texts)
    for m in bundle.metrics:
        if m.version_id not in versions:
            raise PublishError(f"metrics for {m.version_id}, which is not in the bundle")
        if m.level == "split" and not m.full_split:
            raise PublishError(f"{m.version_id}: a split-level row from a partial batch")
    return _ancestors_first(versions)


def _check_content(v: ArchiveRow, texts: dict[str, str]) -> None:
    """``v`` must rebuild from its spec and prompt texts to its own content-addressed id."""
    spec = SystemVersionSpec.model_validate(v.spec)
    try:
        sv = build_system_version_from_texts(
            spec, {path: texts[sha] for path, sha in v.prompt_map.items()}
        )
    except KeyError as exc:
        raise PublishError(f"{v.version_id}: {exc.args[0]}") from None
    if sv.version_id != v.version_id:
        raise PublishError(f"{v.version_id}: its spec and prompts rebuild as {sv.version_id}")
    if v.name != spec.name:
        raise PublishError(f"{v.version_id}: name {v.name!r} is not the spec's {spec.name!r}")
    if set(v.prompt_map) != set(spec.prompt_paths()):
        raise PublishError(f"{v.version_id}: prompt_map does not list the spec's prompts")


def _ancestors_first(versions: dict[str, ArchiveRow]) -> list[ArchiveRow]:
    out: list[ArchiveRow] = []
    done: set[str] = set()
    visiting: set[str] = set()

    def visit(vid: str) -> None:
        if vid in done:
            return
        if vid in visiting:
            raise PublishError(f"{vid}: parent/twin links form a cycle")
        visiting.add(vid)
        v = versions[vid]
        for ref in (v.parent_id, v.twin_of):
            if ref is not None:
                visit(ref)
        visiting.discard(vid)
        done.add(vid)
        out.append(v)

    for vid in sorted(versions):
        visit(vid)
    return out


def _closure(rows: list[dict], cycle_id: str) -> list[dict]:
    """The cycle's versions, their ancestors (parents and twin_of), and the twins of all those."""
    by_id = {r["version_id"]: r for r in rows}
    keep = {r["version_id"] for r in rows if r["cycle_id"] == cycle_id}
    if not keep:
        raise PublishError(f"no archived version belongs to cycle {cycle_id}")
    todo = list(keep)
    while todo:
        r = by_id[todo.pop()]
        for ref in (r["parent_id"], r["twin_of"]):
            if ref is not None and ref not in keep:
                keep.add(ref)
                todo.append(ref)
    keep |= {r["version_id"] for r in rows if r["twin_of"] in keep}
    return [r for r in rows if r["version_id"] in keep]


async def export_bundle(db: Database, cycle_id: str | None = None) -> dict[str, Any]:
    """The archived versions of ``db`` (all, or one cycle's closure) with their metrics and
    prompt texts, checked and JSON-ready."""
    async with db.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT version_id, parent_id, twin_of, cycle_id, origin, name, spec, prompt_map,"
            " diff, proposer, created_at FROM sv_archive ORDER BY created_at, version_id"
        )
        versions = await cur.fetchall()
        if not versions:
            raise PublishError("nothing to publish: this database has no archived version")
        if cycle_id is not None:
            versions = _closure(versions, cycle_id)
        ids = [v["version_id"] for v in versions]
        cur = await conn.execute(
            "SELECT version_id, split, judge_version, git_sha, batch_id, full_split, level,"
            " subject, metric, n, mean, sd, updated_at FROM sv_metrics WHERE version_id = ANY(%s)"
            " ORDER BY version_id, split, judge_version, batch_id, level, subject, metric",
            (ids,),
        )
        metrics = await cur.fetchall()
        hashes = sorted({h for v in versions for h in (v["prompt_map"] or {}).values()})
        cur = await conn.execute(
            "SELECT sha256, text FROM sv_prompts WHERE sha256 = ANY(%s) ORDER BY sha256",
            (hashes,),
        )
        prompts = await cur.fetchall()
    try:
        bundle = EvolutionBundle.model_validate(
            {"versions": versions, "metrics": metrics, "prompts": prompts}
        )
    except ValidationError as exc:
        raise PublishError(f"the local archive does not form a valid bundle: {exc}") from None
    check_bundle(bundle)
    return bundle.model_dump(mode="json")


def summarize(bundle: EvolutionBundle) -> dict[str, Any]:
    """What a bundle holds: counts, and each version's id, origin, cycle and name."""
    return {
        **{t: len(getattr(bundle, t)) for t in TABLES},
        "version_list": [
            {"version_id": v.version_id, "origin": v.origin, "cycle_id": v.cycle_id, "name": v.name}
            for v in bundle.versions
        ],  # fmt: skip
    }


_VERSION_FIELDS = ("parent_id", "twin_of", "cycle_id", "origin", "name", "spec", "prompt_map",
                   "diff", "proposer", "created_at")  # fmt: skip
_METRIC_FIELDS = ("full_split", "n", "mean", "sd", "updated_at")


def _same(stored: dict, row: BaseModel, fields: tuple[str, ...]) -> bool:
    return all(stored[f] == getattr(row, f) for f in fields)


async def import_bundle(db: Database, bundle: EvolutionBundle) -> dict[str, Any]:
    """Insert a checked bundle in one transaction. Rows already stored are left unchanged and
    counted as identical or different (the differing ids are listed)."""
    ordered = check_bundle(bundle)
    out: dict[str, Any] = {t: {"inserted": 0, "identical": 0, "different": 0} for t in TABLES}
    differing: dict[str, list] = {t: [] for t in TABLES}
    async with db.pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT sha256 FROM sv_prompts WHERE sha256 = ANY(%s)",
            ([p.sha256 for p in bundle.prompts],),
        )
        stored_prompts = {r["sha256"] for r in await cur.fetchall()}
        # A prompt is addressed by the hash of its text: a stored one is always identical.
        new_prompts = [p for p in bundle.prompts if p.sha256 not in stored_prompts]
        out["prompts"]["identical"] = len(bundle.prompts) - len(new_prompts)

        cur = await conn.execute(
            f"SELECT version_id, {', '.join(_VERSION_FIELDS)} FROM sv_archive"
            " WHERE version_id = ANY(%s)",
            ([v.version_id for v in ordered],),
        )
        stored_versions = {r["version_id"]: r for r in await cur.fetchall()}
        new_versions = []
        for v in ordered:
            stored = stored_versions.get(v.version_id)
            if stored is None:
                new_versions.append(v)
            elif _same(stored, v, _VERSION_FIELDS):
                out["versions"]["identical"] += 1
            else:
                out["versions"]["different"] += 1
                differing["versions"].append(v.version_id)

        cur = await conn.execute(
            "SELECT version_id, split, judge_version, git_sha, batch_id, level, subject, metric,"
            f" {', '.join(_METRIC_FIELDS)} FROM sv_metrics WHERE version_id = ANY(%s)",
            (sorted({m.version_id for m in bundle.metrics}),),
        )
        stored_metrics = {
            (r["version_id"], r["split"], r["judge_version"], r["git_sha"], r["batch_id"],
             r["level"], r["subject"], r["metric"]): r
            for r in await cur.fetchall()
        }  # fmt: skip
        new_metrics = []
        for m in bundle.metrics:
            stored = stored_metrics.get(m.key())
            if stored is None:
                new_metrics.append(m)
            elif _same(stored, m, _METRIC_FIELDS):
                out["metrics"]["identical"] += 1
            else:
                out["metrics"]["different"] += 1
                if m.batch_id not in differing["metrics"]:
                    differing["metrics"].append(m.batch_id)

        async with conn.cursor() as c:
            await c.executemany(
                "INSERT INTO sv_prompts (sha256, text) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                [(p.sha256, p.text) for p in new_prompts],
            )
            # One row at a time in ancestor order: a child's parent must exist when it lands.
            for v in new_versions:
                await c.execute(
                    "INSERT INTO sv_archive (version_id, parent_id, twin_of, cycle_id, origin,"
                    " name, spec, prompt_map, diff, proposer, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                    " ON CONFLICT (version_id) DO NOTHING",
                    (v.version_id, v.parent_id, v.twin_of, v.cycle_id, v.origin, v.name,
                     Jsonb(v.spec), Jsonb(v.prompt_map),
                     Jsonb(v.diff) if v.diff is not None else None,
                     Jsonb(v.proposer) if v.proposer is not None else None, v.created_at),
                )  # fmt: skip
            await c.executemany(
                "INSERT INTO sv_metrics (version_id, split, judge_version, git_sha, batch_id,"
                " full_split, level, subject, metric, n, mean, sd, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT DO NOTHING",
                [(m.version_id, m.split, m.judge_version, m.git_sha, m.batch_id, m.full_split,
                  m.level, m.subject, m.metric, m.n, m.mean, m.sd, m.updated_at)
                 for m in new_metrics],
            )  # fmt: skip
    out["prompts"]["inserted"] = len(new_prompts)
    out["versions"]["inserted"] = len(new_versions)
    out["metrics"]["inserted"] = len(new_metrics)
    for t in TABLES:
        out[t]["differing"] = differing[t]
    return out


def post(
    bundle: dict[str, Any],
    url: str | None = None,
    *,
    client: httpx.Client | None = None,
    timeout: float = TIMEOUT_S,
) -> dict:
    """POST the bundle to ``{url}/evolution/archive`` with the console token."""
    url = (url or os.environ.get(URL_ENV, "")).rstrip("/")
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not url:
        raise PublishError(f"set {URL_ENV} (the console's base URL) or pass --url")
    if not token:
        raise PublishError(f"set {TOKEN_ENV} (the console's token)")
    client = client or httpx.Client(timeout=timeout)
    try:
        r = client.post(f"{url}/evolution/archive", json=bundle,
                        headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    except httpx.TimeoutException:
        raise PublishError(
            f"no answer within {timeout:.0f}s; the server may still commit. Publish again: "
            "rows already stored are reported as identical, never written twice"
        ) from None
    if r.status_code >= 400:
        raise PublishError(f"publish failed: HTTP {r.status_code}: {r.text[:300]}")
    return r.json()
