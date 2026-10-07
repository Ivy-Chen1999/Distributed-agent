"""The cost step (EU cost plan U3): obligation records -> validated cost records.

One structured LLM call per batch of records. The input is the records alone, in the ``full``
obligation view plus the resolved payer: no provision text, no memorandum, no delta, no findings
and nothing from the impact assessment. Code fills every deterministic field and validates the
answer record by record (``validate_batch``): an answer for an id outside the batch, a duplicate
or a semantically invalid entry is dropped and counted; an obligation left without a valid
entry becomes ``not_estimated``. A failed batch (timeout, schema errors after retries) marks its
records ``not_estimated`` with the error kind and adds a note; it never fails the run.

Serves both the graph node (``womm.graph.cost``) and the whole-version sweep
(``womm.cost.sweep``).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import Counter
from dataclasses import dataclass, field

from womm.citations import normalize
from womm.cost.categories import ia_category
from womm.cost.payer import SECTOR, PayerResolution, resolve_payer, sector_for
from womm.data.corpus import Corpus, Obligation
from womm.data.fixtures import FixtureError
from womm.llm.base import LLMBackend, LLMError
from womm.models.cost import CostBatch, CostCoverage, CostDraft, CostRecord
from womm.models.run import CallUsage
from womm.models.system_version import CostConfig
from womm.retrieval import render_obligations

RELEVANT_STATEMENTS = frozenset({"duty", "prohibition"})
# R5: "changed after the proposal"; R7: only "added" is a cost the ex-ante IA could not see.
CHANGED_DELTAS = frozenset({"added", "modified", "split_merge"})
LATE_ADDED_DELTA = "added"
MIN_QUOTE_WORDS = 3
OMITTED = "omitted by the cost step"
INVALID = "invalid answer from the cost step"


@dataclass(frozen=True)
class CostItem:
    """One obligation record to estimate, with its deterministic context."""

    key: str
    records_version: str
    source_id: str
    record: Obligation
    payer: PayerResolution
    superseded: bool = False


@dataclass(frozen=True)
class RelevantRecords:
    items: list[CostItem]
    not_covered: list[str]


def relevant_records(corpus: Corpus, version_id: str, keys: list[str]) -> RelevantRecords:
    """Duties and prohibitions on ``keys`` in ``version_id``, in key then record order. A key with
    no records (an Omnibus-amended unit on the consolidated text, an unknown key) is listed as
    not covered: no cost is invented for it."""
    items: list[CostItem] = []
    not_covered: list[str] = []
    try:
        by_key = corpus.version(version_id).by_key()
    except FixtureError:
        return RelevantRecords([], list(dict.fromkeys(keys)))
    for key in dict.fromkeys(keys):
        records_from, records = (
            corpus.obligation_records(version_id, key) if key in by_key else (version_id, [])
        )
        relevant = [r for r in records if r.statement_type in RELEVANT_STATEMENTS]
        if not relevant:
            not_covered.append(key)
            continue
        provision = corpus.version(records_from).by_key()[key]
        suffix = provision.source_id.split("/", 1)[1]
        superseded = corpus.is_superseded(records_from, key)
        for r in relevant:
            items.append(
                CostItem(
                    key=key,
                    records_version=records_from,
                    source_id=f"{records_from}/obligations/{suffix}",
                    record=r,
                    payer=resolve_payer(r, records_from),
                    superseded=superseded,
                )
            )
    return RelevantRecords(items, not_covered)


# --- batches -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkBatch:
    batch_id: str
    items: tuple[CostItem, ...]


def _batch_id(n: int, items: list[CostItem]) -> str:
    digest = hashlib.sha256("\n".join(i.record.obligation_id for i in items).encode())
    return f"b{n:03d}-{digest.hexdigest()[:10]}"


def make_batches(items: list[CostItem], max_records: int) -> list[WorkBatch]:
    """Batches of at most ``max_records``, filled in order with whole provision keys where a key
    fits; a key larger than a batch is split. Ids depend only on the records, so they are stable
    across restarts."""
    groups: list[list[CostItem]] = []
    for item in items:
        if groups and groups[-1][0].key == item.key:
            groups[-1].append(item)
        else:
            groups.append([item])
    batches: list[list[CostItem]] = []
    current: list[CostItem] = []
    for group in groups:
        if current and len(current) + len(group) > max_records:
            batches.append(current)
            current = []
        while len(group) > max_records:
            batches.append(group[:max_records])
            group = group[max_records:]
        current.extend(group)
        if len(current) == max_records:
            batches.append(current)
            current = []
    if current:
        batches.append(current)
    return [WorkBatch(_batch_id(n, b), tuple(b)) for n, b in enumerate(batches, 1)]


def _payer_line(item: CostItem) -> str:
    p = item.payer
    if p.basis == "rule_field":
        return f"payer: {p.payer} (named in the record)"
    if p.basis == "rule_table":
        return f"payer: {p.payer} ({p.legal_basis})"
    return "payer: not identified (infer it from the span with a verbatim quote, or leave it null)"


def render_batch(batch: WorkBatch) -> str:
    """The cost step's user content: each record's ``full`` obligation view (dates withheld or
    labelled as in every view) plus its payer line. Nothing else."""
    blocks = [
        render_obligations([i.record], "full", superseded=i.superseded) + "\n" + _payer_line(i)
        for i in batch.items
    ]
    return (
        f"Obligation records ({len(batch.items)}). Return one entry per record.\n\n"
        + "\n\n".join(blocks)
    )


# --- validation --------------------------------------------------------------------------------


@dataclass
class BatchOutcome:
    records: list[CostRecord]
    invalid: int = 0


def _base(item: CostItem) -> dict:
    r = item.record
    delta = r.unit_delta
    payer = item.payer.payer
    return {
        "obligation_id": r.obligation_id,
        "unit_id": r.unit_id,
        "provision_key": item.key,
        "source_id": item.source_id,
        "records_version": item.records_version,
        "statement_type": r.statement_type,
        "payer": payer,
        "payer_basis": item.payer.basis,
        "payer_legal_basis": item.payer.legal_basis,
        "sector": sector_for(payer, r.public_sector),
        "applies_from": r.applies_from,
        "date_label": r.date_label,
        "date_withheld": r.date_withheld,
        "unit_delta": delta,
        "changed_after_proposal": delta in CHANGED_DELTAS,
        "late_added": delta == LATE_ADDED_DELTA,
    }


def not_estimated(item: CostItem, reason: str) -> CostRecord:
    return CostRecord(**_base(item), status="not_estimated", reason=reason)


def _verbatim(quote: str | None, span: str) -> bool:
    if not quote or len(normalize(quote).split()) < MIN_QUOTE_WORDS:
        return False
    return normalize(quote) in normalize(span)


def _valid(d: CostDraft) -> bool:
    if d.status == "not_costed":
        return bool((d.reason or "").strip())
    return d.effort_type is not None and (d.one_off is not None or d.recurring is not None)


def _record(item: CostItem, d: CostDraft) -> CostRecord:
    base = _base(item)
    inferable = item.payer.basis == "unknown" and d.inferred_payer in SECTOR
    if inferable and _verbatim(d.payer_quote, item.record.span):
        payer = d.inferred_payer
        base.update(
            payer=payer,
            payer_basis="inferred",
            payer_quote=d.payer_quote,
            sector=sector_for(payer, item.record.public_sector),
        )
    if d.status == "not_costed":
        return CostRecord(**base, status="not_costed", reason=d.reason, rationale=d.rationale)
    return CostRecord(
        **base,
        status="estimated",
        effort_type=d.effort_type,
        secondary_types=[t for t in dict.fromkeys(d.secondary_types) if t != d.effort_type],
        ia_category=ia_category(d.effort_type, base["sector"]),
        one_off=d.one_off,
        recurring=d.recurring,
        rationale=d.rationale or None,
    )


def validate_batch(batch: WorkBatch, answer: CostBatch) -> BatchOutcome:
    """One record per batch item, in batch order."""
    items = {i.record.obligation_id: i for i in batch.items}
    got: dict[str, CostRecord] = {}
    rejected: set[str] = set()
    invalid = 0
    for d in answer.records:
        oid = d.obligation_id.strip().strip("[]")
        item = items.get(oid)
        if item is None or oid in got or not _valid(d):
            invalid += 1
            if item is not None:
                rejected.add(oid)
            continue
        got[oid] = _record(item, d)
    records = [
        got.get(oid) or not_estimated(item, INVALID if oid in rejected else OMITTED)
        for oid, item in items.items()
    ]
    return BatchOutcome(records, invalid)


# --- running -----------------------------------------------------------------------------------


@dataclass
class CostEstimate:
    records: list[CostRecord]
    usage: list[CallUsage] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    invalid: int = 0


@dataclass
class BatchResult:
    batch: WorkBatch
    outcome: BatchOutcome
    usage: list[CallUsage]
    error: str | None = None


async def run_batch(
    batch: WorkBatch, *, backend: LLMBackend, role: CostConfig, prompt: str
) -> BatchResult:
    """One batch through the backend; failures become ``not_estimated`` records."""
    try:
        answer, usage = await backend.call("cost", prompt, render_batch(batch), CostBatch, role)
    except LLMError as exc:
        records = [not_estimated(i, exc.error_kind) for i in batch.items]
        usage_list = [exc.usage] if exc.usage else []
        return BatchResult(batch, BatchOutcome(records), usage_list, exc.error_kind)
    except Exception as exc:  # noqa: BLE001 - a cost bug must not abort the run
        records = [not_estimated(i, "process_error") for i in batch.items]
        return BatchResult(batch, BatchOutcome(records), [], f"process_error: {exc}"[:200])
    return BatchResult(batch, validate_batch(batch, answer), [usage])


def failure_note(result: BatchResult) -> str:
    return (
        f"cost step: batch {result.batch.batch_id} failed ({result.error}); "
        f"{len(result.batch.items)} records not estimated"
    )


async def estimate_costs(
    items: list[CostItem],
    *,
    backend: LLMBackend,
    role: CostConfig,
    prompt: str,
    max_parallel: int,
) -> CostEstimate:
    """Every item through the cost step, batches concurrently under ``max_parallel``."""
    batches = make_batches(items, role.max_records_per_call)
    gate = asyncio.Semaphore(max_parallel)

    async def one(batch: WorkBatch) -> BatchResult:
        async with gate:
            return await run_batch(batch, backend=backend, role=role, prompt=prompt)

    results = await asyncio.gather(*(one(b) for b in batches))
    est = CostEstimate(records=[])
    for res in results:
        est.records.extend(res.outcome.records)
        est.usage.extend(res.usage)
        est.invalid += res.outcome.invalid
        if res.error is not None:
            est.notes.append(failure_note(res))
    return est


def coverage(records: list[CostRecord], not_covered: list[str], invalid: int = 0) -> CostCoverage:
    status = Counter(r.status for r in records)
    return CostCoverage(
        relevant=len(records),
        estimated=status["estimated"],
        not_costed=status["not_costed"],
        not_estimated=status["not_estimated"],
        invalid_dropped=invalid,
        not_covered_keys=list(not_covered),
        payers_by_basis=dict(sorted(Counter(r.payer_basis for r in records).items())),
    )
