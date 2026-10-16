"""Tie-break judge: settles what the three drafting judges left open, so people only audit.

The three isolated judges (``womm.eval.drafting``) accept an item when all agree. Everything else
used to go to a person: a judge disagreeing or unsure, a deterministic flag, and every
``possibly_missing`` candidate. Most of those are judgement calls an LLM can make with the full
evidence in front of it, so a fourth call (role ``tiebreak`` in ``evals/drafting.yaml``) now sees
each open item with its IA context, the scenario provisions, the three judges' reasons and the
items already kept, and rules ``keep`` (with the category to publish), ``drop`` or ``unsure``.

- ``keep`` -> ``review.decision: llm_kept``; the item's ``category`` becomes the ruled one and
  the drafted one is kept in ``tiebreak.original_category``.
- ``drop`` and ``unsure`` -> ``llm_dropped``. An unsure item is left out of the yardstick, not
  sent to a person: a missing reference item costs a little coverage, a wrong one teaches the
  system the wrong lesson.
- A failed deterministic check (anchor not found in the IA, a provision key outside the scenario)
  is dropped without an LLM call (``model: deterministic``).

A random 20% (``AUDIT_RATE``) of the tie-break-kept items is marked ``audit: true``, drawn with a
seed derived from the case id (``tiebreak_audit_seed_for``), alongside the judges' own audit
sample. With ``AUDIT_BLOCKS`` off these are optional spot checks: any a person decides count
towards the per-proposal error rate, and a proposal over 10% is escalated to full human review.

Train/val only: a holdout draft is refused, since every holdout item is verified by its owner.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from womm.data.fixtures import Fixture
from womm.eval.drafting import (
    AUDIT_BLOCKS,
    AUDIT_RATE,
    CATEGORY_GUIDE,
    DIMENSIONS,
    AuditSample,
    Category,
    DraftCandidate,
    DraftingConfig,
    DraftingError,
    DraftStats,
    GoldenDraft,
    TiebreakBlock,
    _DraftItem,
    _dump,
    _provisions_block,
    anchor_context,
    audit_seed_for,
    auto_status,
    draw_audit,
    item_digest,
)
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel

ROLE = "tiebreak"
# Flags from a deterministic check that no judgement can overrule.
HARD_FLAGS = ("anchor_not_found", "too_short", "too_fragmented", "unknown_provision_keys")


class TiebreakItemVerdict(StrictModel):
    item_id: str
    verdict: Literal["keep", "drop", "unsure"]
    category: Category = Field(description="The category to publish the item under, if kept.")
    reason: str


class TiebreakOutput(StrictModel):
    verdicts: list[TiebreakItemVerdict]


def tiebreak_audit_seed_for(case_id: str) -> int:
    return audit_seed_for(f"{case_id}#tiebreak")


def needs_tiebreak(item: _DraftItem) -> bool:
    """An open, tool-written item no person has decided: a judge disagreed or was unsure, a check
    flagged it, or it is a ``possibly_missing`` candidate. Audit-sample items stay with people."""
    if item.tiebreak is not None or item.provenance.origin == "human_added" or item.review.audit:
        return False
    if item.review.decision not in ("pending", "auto_accepted"):
        return False
    return isinstance(item, DraftCandidate) or item.judge.overall != "agree" or bool(item.flags)


def hard_flags(item: _DraftItem) -> list[str]:
    return [f for f in item.flags if f.startswith(HARD_FLAGS)]


def _claim(item: _DraftItem) -> dict:
    keys = ("affected_actor", "mechanism", "impact", "description", "why_missing")
    return {k: getattr(item, k) for k in keys if hasattr(item, k)}


def tiebreak_input(
    draft: GoldenDraft, items: list[_DraftItem], fixture: Fixture, ia_text: str, chars: int
) -> str:
    scenario = fixture.scenario(draft.scenario_id)
    kept = [
        {"item_id": i.item_id} | _claim(i)
        for i in draft.items()
        if i.review.decision in ("auto_accepted", "verified", "edited")
        and not isinstance(i, DraftCandidate)
    ]
    payload = []
    for item in items:
        context = (
            anchor_context(item.ia_anchor, ia_text, chars) if item.anchor.against == "ia" else None
        )
        payload.append(
            {
                "item_id": item.item_id,
                "kind": "possibly_missing candidate" if isinstance(item, DraftCandidate)
                else "omission" if hasattr(item, "omission_id") else "expected impact",
                "claim": _claim(item),
                "provision_keys": item.provision_keys,
                "proposed_category": item.category,
                "ia_section": item.ia_section,
                "anchor_quote": item.ia_anchor,
                "anchor_in_context": context or "(context not available; rely on the quote)",
                "judges": {
                    d: {"verdict": getattr(item.judge, d).verdict,
                        "reason": getattr(item.judge, d).reason}
                    for d in DIMENSIONS
                },
                "flags": item.flags,
            }
        )  # fmt: skip
    return (
        f"Case: {draft.case_id}\nScenario: {scenario.scenario_id}: {scenario.description}\n\n"
        f"## Categories\n\n{_dump(CATEGORY_GUIDE)}\n\n"
        f"## Scenario provisions\n\n{_provisions_block(fixture, scenario)}\n\n"
        f"## Items already kept (a candidate that repeats one of these is a duplicate)\n\n"
        f"{_dump(kept)}\n\n"
        f"## Items to rule on\n\n{_dump(payload)}\n"
    )


def _ruled(item: _DraftItem, block: TiebreakBlock, category: str) -> _DraftItem:
    data = item.model_dump(mode="json")
    data["tiebreak"] = block.model_dump(mode="json")
    if block.verdict == "keep":
        data["category"] = category
    data["review"] = {**data["review"], "decision": "llm_kept" if block.verdict == "keep"
                      else "llm_dropped"}  # fmt: skip
    data["provenance"] = {**data["provenance"], "status": "llm_tiebroken"}
    return type(item).model_validate(data)


def _audit(item: _DraftItem, sampled: bool) -> _DraftItem:
    """Mark a tie-break-sampled item; and, with ``AUDIT_BLOCKS`` off, hand an untouched judges'
    audit item drafted while it was on back to ``auto_accepted`` (it is an optional check now)."""
    review = item.review
    if sampled:
        review = review.model_copy(
            update={"audit": True, "decision": "pending" if AUDIT_BLOCKS else review.decision}
        )
    elif (
        not AUDIT_BLOCKS
        and review.audit
        and review.decision == "pending"
        and item.tiebreak is None
        and auto_status(item) == "auto_accepted"
    ):
        review = review.model_copy(update={"decision": "auto_accepted"})
        item = item.model_copy(
            update={"provenance": item.provenance.model_copy(update={"status": "llm_judged"})}
        )
    return item.model_copy(update={"review": review})


def _restats(draft: GoldenDraft) -> DraftStats:
    items = draft.items()
    decisions = [i.review.decision for i in items]
    return draft.stats.model_copy(
        update={
            "auto_accepted": decisions.count("auto_accepted"),
            "pending": decisions.count("pending"),
            "audited": sum(i.review.audit for i in items),
            "human_decisions_needed": sum(
                i.review.decision == "pending"
                or (isinstance(i, DraftCandidate) and i.review.decision == "auto_accepted")
                for i in items
            ),
            "llm_kept": decisions.count("llm_kept"),
            "llm_dropped": decisions.count("llm_dropped"),
        }
    )


def apply_rulings(
    draft: GoldenDraft, rulings: dict[str, TiebreakBlock], categories: dict[str, str]
) -> GoldenDraft:
    """``draft`` with each ruled item decided, the tie-break audit sample drawn over every
    tie-break-kept item and the digests of the ruled items rewritten."""
    if draft.provenance.tiebreak_audit is not None:
        raise DraftingError(f"{draft.case_id}: already tie-broken; re-draft to run it again")

    def rule(item: _DraftItem) -> _DraftItem:
        block = rulings.get(item.item_id)
        return (
            item
            if block is None
            else _ruled(item, block, categories.get(item.item_id, item.category))
        )

    sections = {
        s: [rule(i) for i in getattr(draft, s)]
        for s in ("expected_impacts", "important_omissions", "possibly_missing")
    }
    kept = sorted(
        i.item_id for s in sections.values() for i in s if i.review.decision == "llm_kept"
    )
    seed = tiebreak_audit_seed_for(draft.case_id)
    sampled = draw_audit(kept, AUDIT_RATE, seed)
    for name, items in sections.items():
        sections[name] = [_audit(i, i.item_id in sampled) for i in items]
    ruled = {i.item_id: i for items in sections.values() for i in items if i.item_id in rulings}
    digests = dict(draft.provenance.item_digests) | {k: item_digest(v) for k, v in ruled.items()}
    provenance = draft.provenance.model_copy(
        update={
            "item_digests": digests,
            "tiebreak_audit": AuditSample(
                seed=seed, rate=AUDIT_RATE, eligible=len(kept), eligible_ids=kept, sampled=sampled
            ),
        }
    )
    out = draft.model_copy(update={**sections, "provenance": provenance})
    return out.model_copy(update={"stats": _restats(out)})


async def tiebreak_draft(
    draft: GoldenDraft,
    fixture: Fixture,
    ia_text: str,
    config: DraftingConfig,
    backends: dict[str, LLMBackend],
) -> GoldenDraft:
    """Rule on every open item of a train/val draft (one LLM call) and apply the rulings."""
    if draft.split == "holdout":
        raise DraftingError(
            f"{draft.case_id}: a holdout draft is verified item by item by its owner; no tie-break"
        )
    role = config.roles.tiebreak
    if role is None:
        raise DraftingError("no tiebreak role in the drafting config (evals/drafting.yaml)")
    open_items = [i for i in draft.items() if needs_tiebreak(i)]
    rulings: dict[str, TiebreakBlock] = {}
    categories: dict[str, str] = {}
    for item in open_items:
        failed = hard_flags(item)
        if failed:
            rulings[item.item_id] = TiebreakBlock(
                verdict="drop",
                original_category=item.category,
                reason="a deterministic check failed: " + "; ".join(failed),
                model="deterministic",
            )
    asked = [i for i in open_items if i.item_id not in rulings]
    if asked:
        user = tiebreak_input(draft, asked, fixture, ia_text, config.context_chars)
        backend = backends[role.backend]
        out, _ = await backend.call(ROLE, config.prompts[role.prompt], user, TiebreakOutput, role)
        got = {v.item_id: v for v in out.verdicts}
        for item in asked:
            v = got.get(item.item_id)
            rulings[item.item_id] = TiebreakBlock(
                verdict=v.verdict if v else "unsure",
                original_category=item.category,
                reason=v.reason if v else "the tie-break judge returned no ruling",
                model=role.model,
            )
            if v and v.verdict == "keep":
                categories[item.item_id] = v.category
    return apply_rulings(draft, rulings, categories)
