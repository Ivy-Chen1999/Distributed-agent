"""The AI's answer to the question the labelling page asks people (reference check, 2026-10-14).

The page asks one narrow question per card: is the point an accurate reading of its source, or,
for a review-board point, is its one-line effect or risk what the board asked to be assessed.
The golden decisions also rest on what the page leaves to code or to the AI (the law, repeats,
the item rules) and, for board points, on the whole text that the page now shows as context. So
people are not scored against those decisions: before any answer exists, the tie-break's model
answers the page's own question, on exactly the card the person sees, with the page's rules
(``prompts/tools/fidelity_check.md``), blind to the golden decision. That verdict becomes the
key's ``tool_kept``; the golden decision stays as ``golden_kept`` and ``golden_reason`` and is
shown next to it on the review page.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from womm.config import REPO_ROOT
from womm.eval import hosted_labels
from womm.eval.drafting import DraftingConfig
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel

PROMPT = REPO_ROOT / "prompts/tools/fidelity_check.md"
ROLE = "fidelity_check"
BATCH = 15  # cards per call
CONTEXT_ONLY = ("mechanism",)  # on report cards; on board cards the point's own text


class FidelityError(ValueError):
    """The key cannot be re-judged: nothing was written."""


class FidelityVerdict(StrictModel):
    item_id: str
    verdict: Literal["keep", "drop"]
    reason: str


class FidelityOutput(StrictModel):
    verdicts: list[FidelityVerdict]


def card_input(card: dict[str, Any]) -> dict[str, Any]:
    """What the model sees for one card: what the person sees, split as the page splits it."""
    claim = card["claim"]
    board = card["source"] != "report"
    if board:
        point = {"effect_or_risk": claim.get("concern") or claim.get("description", "")}
        context_only = {"ai_reasoning": claim.get("description", "")} if "concern" in claim else {}
    else:
        point = {k: v for k, v in claim.items() if k not in CONTEXT_ONLY}
        context_only = {k: v for k, v in claim.items() if k in CONTEXT_ONLY}
    passage = card.get("passage")
    text = (
        f"{passage['before']}[[{passage['quote']}]]{passage['after']}"
        if passage
        else f"[[{card['quote']}]]"
    )
    return {
        "item_id": card["id"],
        "source": "review board opinion" if board else "official report",
        "section": card.get("section", ""),
        "source_text": text,
        "point": point,
        "context_only": context_only,
    }


def _batches(cards: list[dict], size: int) -> list[list[dict]]:
    return [cards[i : i + size] for i in range(0, len(cards), size)]


async def rejudge(
    out_dir: Path, config: DraftingConfig, backends: dict[str, LLMBackend]
) -> dict[str, Any]:
    """Answer every card of the sample in ``out_dir`` and rewrite its key (see the module)."""
    key_path = out_dir / "key.private.json"
    key = json.loads(key_path.read_text(encoding="utf-8"))
    if key.get("comparison") == "fidelity":
        raise FidelityError("this sample was already re-judged")
    if key.get("reserve"):
        raise FidelityError("send the reserve (extend) first: every item must be on a page")
    pages = hosted_labels.read_pages(out_dir)
    cards = [c for cs in pages["annotators"].values() for c in cs]
    if {c["id"] for c in cards} != set(key["items"]):
        raise FidelityError("the pages and the key hold different items")
    role = config.roles.tiebreak
    if role is None:
        raise FidelityError("the drafting config has no tie-break role to answer with")
    prompt = PROMPT.read_text(encoding="utf-8")
    verdicts: dict[str, FidelityVerdict] = {}
    for batch in _batches(cards, BATCH):
        user = "## Cards\n\n" + json.dumps([card_input(c) for c in batch], indent=1,
                                            ensure_ascii=False)  # fmt: skip
        out, _ = await backends[role.backend].call(ROLE, prompt, user, FidelityOutput, role)
        verdicts |= {v.item_id: v for v in out.verdicts if v.item_id in {c["id"] for c in batch}}
    if missing := sorted(set(key["items"]) - set(verdicts)):
        raise FidelityError(f"no verdict for {', '.join(missing)}")
    changed = []
    for iid, it in key["items"].items():
        v = verdicts[iid]
        golden = "kept" if it["tool_kept"] else "dropped"
        it["golden_kept"], it["golden_reason"] = it["tool_kept"], it.get("tool_reason", "")
        it["tool_kept"] = v.verdict == "keep"
        it["tool_reason"] = (f"page question: {v.reason} | answer key: {golden}, "
                             f"{it['golden_reason']}")  # fmt: skip
        if it["tool_kept"] != it["golden_kept"]:
            changed.append(iid)
    key["comparison"] = "fidelity"
    key["fidelity_model"] = role.model
    key_path.write_text(json.dumps(key, indent=2), encoding="utf-8")
    return {"items": len(key["items"]), "differs_from_golden": sorted(changed)}
