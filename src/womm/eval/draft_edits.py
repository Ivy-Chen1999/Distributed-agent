"""In-place edits of a golden-case draft file: review links and reviewer decisions.

A draft under review may carry a reviewer's comments or hand edits, so these functions change
only the lines they must, the way feedback staging does (``womm.evolve.feedback``):

- ``set_review_links_in_place`` inserts (or replaces) the top-level ``review_links`` block
  before ``provenance:`` and swaps the legacy header comment for the current one;
- ``apply_decisions_in_place`` replaces, per decided item, its ``provenance.status`` line and its
  ``review`` block; an ``edited`` item is rewritten whole (its new field values), and is refused
  when it holds comment lines, which a rewrite would drop.

Every result is re-parsed and must equal the expected draft; the file's sha256 is compared with
what was read before an atomic replace, so a concurrent save is never lost. ``review_links`` and
the review blocks are outside the tool digests, so the digests stay valid.

``build_review_links`` makes the public EUR-Lex links of a train/val draft from its IA record;
it refuses a holdout draft (holdout identifiers never enter a tracked file).
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from womm.citations import match_quote
from womm.data.ia_index import IaRecord
from womm.eval.drafting import (
    DRAFT_HEADER,
    LEGACY_DRAFT_HEADER,
    GoldenDraft,
    ReviewLinks,
    _DraftItem,
    _reviewer_order,
    eurlex_url,
)
from womm.eval.golden_review import (
    USERNAME_HINT,
    ReviewError,
    _person,
    _valid_reviewer,
    decide_item,
)

_SECTIONS = ("expected_impacts", "important_omissions", "possibly_missing")
_ITEM_HEAD = re.compile(r"^- (?:expected_id|omission_id|candidate_id): ['\"]?([\w-]+)['\"]?[ \t]*$")
# A line that ends an item: the next list entry or a top-level key (comments and blanks do not).
_ITEM_END = re.compile(r"^(?:- |[^\s#])")


class DraftEditError(ValueError):
    pass


def _dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


# ----------------------------------------------------------------------------- links


def build_review_links(
    draft: GoldenDraft, record: IaRecord, ia_parts: Sequence[str] | None
) -> ReviewLinks:
    """EUR-Lex links to the draft's IA and proposal; with the IA's part texts (several parts),
    also the part that holds each item's anchor."""
    if draft.split == "holdout":
        raise DraftEditError(f"{draft.case_id}: a holdout draft never gets review links")
    if not record.ia_celex:
        raise DraftEditError(f"{draft.case_id}: the IA index has no IA for {draft.fixture}")
    parts = list(ia_parts or [])
    anchor_parts: dict[str, int] = {}
    if len(parts) > 1:
        for item in draft.items():
            hits = [
                n for n, text in enumerate(parts, 1) if match_quote(item.ia_anchor, text) == "ok"
            ]
            if hits:
                anchor_parts[item.item_id] = hits[0]
    return ReviewLinks(
        ia=eurlex_url(record.ia_celex),
        proposal=eurlex_url(record.celex),
        ia_parts=max(1, len(parts)),
        anchor_parts=anchor_parts,
    )


def set_review_links_in_place(path: Path, links: ReviewLinks) -> None:
    """Write ``links`` into the draft at ``path``, changing nothing else but the legacy header."""
    read = path.read_bytes()
    text = read.decode("utf-8")
    draft = _parse(text, path)
    if draft.split == "holdout":
        raise DraftEditError(f"{draft.case_id}: a holdout draft never gets review links")
    expected = draft.model_copy(update={"review_links": links})
    if text.startswith(LEGACY_DRAFT_HEADER):
        text = DRAFT_HEADER + text[len(LEGACY_DRAFT_HEADER) :]
    block = _dump({"review_links": links.model_dump(mode="json")})
    existing = re.search(r"^review_links:.*\n(?:[ \t]+.*\n|[ \t]*\n)*", text, re.MULTILINE)
    if existing:
        text = text[: existing.start()] + block + text[existing.end() :]
    else:
        anchor = re.search(r"^provenance:", text, re.MULTILINE)
        if anchor is None:
            raise DraftEditError(f"{path.name}: no top-level 'provenance:' line; not edited")
        text = text[: anchor.start()] + block + text[anchor.start() :]
    _check_and_write(path, read, text, expected)


# ----------------------------------------------------------------------------- decisions


def _item_span(lines: list[str], item_id: str) -> tuple[int, int]:
    """[start, end) line indices of an item, without trailing comment or blank lines."""
    heads = [n for n, ln in enumerate(lines) if (m := _ITEM_HEAD.match(ln)) and m[1] == item_id]
    if len(heads) != 1:
        raise DraftEditError(
            f"{item_id}: expected one '- ..._id: {item_id}' line, found {len(heads)}"
        )
    start = heads[0]
    end = next((n for n in range(start + 1, len(lines)) if _ITEM_END.match(lines[n])), len(lines))
    while end > start + 1 and (
        not lines[end - 1].strip() or lines[end - 1].lstrip().startswith("#")
    ):
        end -= 1
    return start, end


def _item_text(item: _DraftItem) -> list[str]:
    return _dump([_reviewer_order(item.model_dump(mode="json"))]).splitlines(keepends=True)


def _review_lines(item: _DraftItem) -> list[str]:
    block = _dump({"review": item.review.model_dump(mode="json")})
    return ["  " + ln for ln in block.splitlines(keepends=True)]


def _rewrite_item(lines: list[str], start: int, end: int, item: _DraftItem, edited: bool) -> None:
    span = lines[start:end]
    if edited:
        if any(ln.lstrip().startswith("#") for ln in span):
            raise DraftEditError(
                f"{item.item_id}: the item holds comment lines, which rewriting its edited "
                "fields would drop; move or delete them, then apply again"
            )
        lines[start:end] = _item_text(item)
        return
    review = [n for n, ln in enumerate(span) if re.match(r"^  review:[ \t]*$", ln)]
    prov = [n for n, ln in enumerate(span) if re.match(r"^  provenance:[ \t]*$", ln)]
    if len(review) != 1 or len(prov) != 1 or prov[0] > review[0]:
        raise DraftEditError(f"{item.item_id}: cannot find its provenance and review blocks")
    status = next(
        (
            n
            for n in range(prov[0] + 1, review[0])
            if re.match(r"^    status:", span[n]) or not span[n].startswith("    ")
        ),
        None,
    )
    if status is None or not span[status].startswith("    status:"):
        raise DraftEditError(f"{item.item_id}: cannot find provenance.status")
    span[status] = f"    status: {item.provenance.status}\n"
    span[review[0] :] = _review_lines(item)
    lines[start:end] = span


def apply_decisions_in_place(
    path: Path, decisions: Mapping[str, Mapping[str, Any]], reviewer: str
) -> GoldenDraft:
    """Decide items of the draft at ``path`` as ``reviewer``; returns the updated draft.

    ``decisions`` maps item ids to ``{decision, note, edit}`` (see ``decide_item``)."""
    reviewer = reviewer.strip().lstrip("@")
    if not _valid_reviewer(reviewer):
        raise DraftEditError(f"reviewer {reviewer!r} is not a GitHub username; {USERNAME_HINT}")
    read = path.read_bytes()
    text = read.decode("utf-8")
    draft = _parse(text, path)
    if _person(draft.provenance.drafted_by) == _person(reviewer):
        raise DraftEditError(f"{reviewer} drafted this case; the drafter cannot be the reviewer")
    by_id = {i.item_id: i for i in draft.items()}
    unknown = sorted(set(decisions) - set(by_id))
    if unknown:
        raise DraftEditError(f"{draft.case_id}: no item {', '.join(unknown)} in this draft")
    decided: dict[str, _DraftItem] = {}
    errors = []
    for item_id, entry in decisions.items():
        try:
            decided[item_id] = decide_item(by_id[item_id], entry, reviewer)
        except ReviewError as exc:
            errors.append(str(exc))
    if errors:  # all of them at once, so the reviewer fixes the file in one go
        raise DraftEditError("nothing written:\n  " + "\n  ".join(errors))
    expected = draft.model_copy(
        update={s: [decided.get(i.item_id, i) for i in getattr(draft, s)] for s in _SECTIONS}
    )
    lines = text.splitlines(keepends=True)
    for item_id, item in decided.items():
        start, end = _item_span(lines, item_id)
        edited = by_id[item_id].model_dump(exclude={"review", "provenance"}) != item.model_dump(
            exclude={"review", "provenance"}
        )
        _rewrite_item(lines, start, end, item, edited)
    _check_and_write(path, read, "".join(lines), expected)
    return expected


# ----------------------------------------------------------------------------- io


def _parse(text: str, path: Path) -> GoldenDraft:
    try:
        return GoldenDraft.model_validate(yaml.safe_load(text))
    except Exception as exc:  # noqa: BLE001 - reported to the reviewer as one line
        raise DraftEditError(
            f"{path.name}: cannot read the draft ({type(exc).__name__}); run "
            "scripts/review_draft.py --check for details"
        ) from None


def _replace_atomically(path: Path, read: bytes, out: str) -> None:
    if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(read).digest():
        raise DraftEditError(
            f"{path.name}: the file changed while it was being edited (saved elsewhere?); "
            "nothing written, run the command again"
        )
    tmp = path.with_name(f".{path.name}.editing")
    tmp.write_text(out, encoding="utf-8")
    os.replace(tmp, path)


def _check_and_write(path: Path, read: bytes, out: str, expected: GoldenDraft) -> None:
    try:
        result = GoldenDraft.model_validate(yaml.safe_load(out))
    except Exception:  # noqa: BLE001
        result = None
    if result != expected:
        raise DraftEditError(
            f"{path.name}: an in-place edit would not give the expected draft (hand-reordered "
            "keys?); nothing written"
        )
    _replace_atomically(path, read, out)
