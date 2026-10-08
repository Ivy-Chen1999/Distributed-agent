"""Whole-version cost sweep (EU cost plan U5): cost records for every duty and prohibition of one
corpus version, for the IA cost check (R6) and the late-added report (R7).

Batches are fixed by version, key and record order, so a batch id is stable across restarts.
Each finished batch is written to

    <root>/<system version id>/<corpus version>/<code>/rep<k>/<batch id>.json

with the code identity; a restart skips finished batches, and a failed batch is not written (a
restart retries it). ``<code>`` names the git sha (plus the diff hash when dirty), so a changed
SystemVersion or code writes to a new directory and scores are never reused across changes.

``max_usd`` (``--max-usd``) is a hard cap on the USD the sweep directory has cost so far: the
spend recorded in its batch files (earlier runs, every repetition) plus this run's calls. A call
starts only when that spend, plus one reserved call per call in flight, plus the call itself
stays within the cap; a call is priced at the largest per-call cost seen so far, and until one
is known a single call runs at a time. So the cap is exceeded only when a call costs more than
any call before it. Calls of failed batches count in this run but are not in any batch file,
so a resumed run does not see them. A backend that reports no USD cost cannot be capped: the
sweep stops after the first unpriced call (``unpriced``).

The sweep reads the corpus and the cost prompt only; it never reads any evaluation material.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from womm.cost.estimate import (
    BatchResult,
    WorkBatch,
    failure_note,
    make_batches,
    relevant_records,
    render_batch,
    run_batch,
)
from womm.data.corpus import Corpus
from womm.llm.base import LLMBackend
from womm.models.cost import CostRecord
from womm.models.run import CallUsage, CodeIdentity
from womm.models.system_version import CostConfig, SystemVersion

__all__ = ["SweepError", "load_sweep", "plan_sweep", "render_batch", "run_sweep", "sweep_dir"]

MANIFEST = "manifest.json"


class SweepError(ValueError):
    pass


@dataclass(frozen=True)
class SweepPlan:
    sv: SystemVersion
    role: CostConfig
    version: str
    batches: list[WorkBatch]
    not_covered: list[str]


def plan_sweep(sv: SystemVersion, corpus: Corpus, version: str) -> SweepPlan:
    """Every duty and prohibition of ``version`` (its own records; the consolidated text borrows
    the adopted records of its unamended units), in corpus order."""
    role = sv.spec.cost
    if role is None:
        raise SweepError(f"{sv.version_id} ({sv.spec.name}) has no cost role")
    versions = [v.version_id for v in corpus.index.versions]
    if version not in versions:
        raise SweepError(f"unknown corpus version {version!r}; versions: {', '.join(versions)}")
    keys = [r.key for r in corpus.index_rows(version)]
    relevant = relevant_records(corpus, version, keys)
    batches = make_batches(relevant.items, role.max_records_per_call)
    return SweepPlan(sv, role, version, batches, relevant.not_covered)


def _code_tag(code: CodeIdentity) -> str:
    tag = f"git-{(code.git_sha or 'unknown')[:12]}"
    if code.dirty:
        tag += f"-dirty-{code.diff_sha or 'unknown'}"
    return tag


def sweep_dir(root: Path, sv: SystemVersion, version: str, code: CodeIdentity) -> Path:
    return root / sv.version_id / version / _code_tag(code)


def _write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)  # a batch file is either complete or absent


@dataclass
class SweepProgress:
    calls: int = 0
    skipped: int = 0
    failed: list[str] = field(default_factory=list)
    usage: list[CallUsage] = field(default_factory=list)
    stopped_at_budget: bool = False
    unpriced: bool = False
    prior_usd: float = 0.0  # spend recorded in the sweep's batch files before this run

    @property
    def cost_usd(self) -> float:
        """This run's spend."""
        return sum(u.cost_usd or 0 for u in self.usage)

    @property
    def total_usd(self) -> float:
        return self.prior_usd + self.cost_usd


def _recorded_usage(out: Path) -> list[CallUsage]:
    usage: list[CallUsage] = []
    for path in sorted(out.glob("rep*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        usage.extend(CallUsage.model_validate(u) for u in data.get("usage", []))
    return usage


class _Budget:
    """Reserve-before-call accounting for ``max_usd`` (see the module docstring)."""

    def __init__(self, max_usd: float, progress: SweepProgress, recorded: list[CallUsage]):
        self.max_usd = max_usd
        self.progress = progress
        self.cond = asyncio.Condition()
        self.in_flight: dict[int, float] = {}
        self.per_call = max((u.cost_usd for u in recorded if u.cost_usd is not None), default=None)
        if any(u.cost_usd is None for u in recorded):
            progress.unpriced = True

    def _stop(self) -> None:
        self.progress.stopped_at_budget = True
        self.cond.notify_all()

    async def reserve(self, ticket: int) -> bool:
        async with self.cond:
            while True:
                if self.progress.stopped_at_budget or self.progress.unpriced:
                    self._stop()
                    return False
                if self.per_call is None:
                    if self.in_flight:
                        await self.cond.wait()  # the first call prices the rest
                        continue
                    self.in_flight[ticket] = 0.0
                    return True
                reserved = sum(self.in_flight.values())
                if self.progress.total_usd + reserved + self.per_call > self.max_usd:
                    self._stop()
                    return False
                self.in_flight[ticket] = self.per_call
                return True

    async def release(self, ticket: int, usage: list[CallUsage]) -> None:
        async with self.cond:
            self.in_flight.pop(ticket, None)
            costs = [u.cost_usd for u in usage]
            if any(c is None for c in costs):
                self.progress.unpriced = True
            known = [c for c in costs if c is not None]
            if known:
                total = sum(known)
                self.per_call = total if self.per_call is None else max(self.per_call, total)
            self.cond.notify_all()


async def run_sweep(
    plan: SweepPlan,
    *,
    backend: LLMBackend,
    root: Path,
    code: CodeIdentity,
    repetitions: int,
    max_parallel: int | None = None,
    max_usd: float | None = None,
    progress: SweepProgress | None = None,
    log=None,
) -> Path:
    """Run repetitions 1..``repetitions``, skipping batches already written. Returns the sweep
    directory (the one ``womm cost check`` and ``womm cost late-added`` read)."""
    out = sweep_dir(root, plan.sv, plan.version, code)
    out.mkdir(parents=True, exist_ok=True)
    _write_json(
        out / MANIFEST,
        {
            "system_version": plan.sv.version_id,
            "system_version_name": plan.sv.spec.name,
            "version": plan.version,
            "code_identity": code.model_dump(mode="json"),
            "cost_backend": plan.role.backend,
            "cost_model": plan.role.model,
            "max_records_per_call": plan.role.max_records_per_call,
            "batches": [b.batch_id for b in plan.batches],
            "not_covered": plan.not_covered,
        },
    )
    progress = progress if progress is not None else SweepProgress()
    prompt = plan.sv.prompt_text(plan.role)
    gate = asyncio.Semaphore(max_parallel or plan.sv.spec.max_parallel_llm_calls)
    budget = None
    if max_usd is not None:
        recorded = _recorded_usage(out)
        progress.prior_usd = sum(u.cost_usd or 0 for u in recorded)
        budget = _Budget(max_usd, progress, recorded)
    tickets = iter(range(1 << 30))

    async def one(rep_dir: Path, rep: int, batch: WorkBatch) -> None:
        path = rep_dir / f"{batch.batch_id}.json"
        if path.exists():
            progress.skipped += 1
            return
        async with gate:
            ticket = next(tickets)
            if budget is not None and not await budget.reserve(ticket):
                return
            progress.calls += 1
            res: BatchResult | None = None
            try:
                res = await run_batch(batch, backend=backend, role=plan.role, prompt=prompt)
                progress.usage.extend(res.usage)
            finally:
                if budget is not None:
                    await budget.release(ticket, res.usage if res is not None else [])
        if res.error is not None:
            progress.failed.append(f"rep{rep}: {failure_note(res)}")
            if log:
                log(f"rep{rep}: {failure_note(res)}")
            return
        _write_json(
            path,
            {
                "batch_id": batch.batch_id,
                "system_version": plan.sv.version_id,
                "version": plan.version,
                "repetition": rep,
                "code_identity": code.model_dump(mode="json"),
                "invalid": res.outcome.invalid,
                "usage": [u.model_dump(mode="json") for u in res.usage],
                "records": [r.model_dump(mode="json") for r in res.outcome.records],
            },
        )
        if log:
            log(f"rep{rep}: {batch.batch_id} done ({len(batch.items)} records)")

    for rep in range(1, repetitions + 1):
        rep_dir = out / f"rep{rep}"
        rep_dir.mkdir(exist_ok=True)
        await asyncio.gather(*(one(rep_dir, rep, b) for b in plan.batches))
    return out


@dataclass
class SweepResult:
    directory: Path
    system_version: str
    version: str
    code_identity: dict
    cost_backend: str
    repetitions: dict[int, list[CostRecord]]
    missing: dict[int, list[str]]
    not_covered: list[str]


def load_sweep(directory: Path) -> SweepResult:
    """Every repetition's records, plus the batches each repetition is still missing."""
    try:
        manifest = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SweepError(f"{directory} is not a cost sweep directory: {exc}") from None
    reps: dict[int, list[CostRecord]] = {}
    missing: dict[int, list[str]] = {}
    for rep_dir in sorted(directory.glob("rep*"), key=lambda p: int(p.name[3:] or 0)):
        rep = int(rep_dir.name[3:])
        records: list[CostRecord] = []
        gaps = []
        for batch_id in manifest["batches"]:
            path = rep_dir / f"{batch_id}.json"
            if not path.exists():
                gaps.append(batch_id)
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            records.extend(CostRecord.model_validate(r) for r in data["records"])
        reps[rep] = records
        if gaps:
            missing[rep] = gaps
    return SweepResult(
        directory=directory,
        system_version=manifest["system_version"],
        version=manifest["version"],
        code_identity=manifest["code_identity"],
        cost_backend=manifest["cost_backend"],
        repetitions=reps,
        missing=missing,
        not_covered=manifest.get("not_covered", []),
    )
