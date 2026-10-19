"""Check the IA cost reference against the cached impact assessment text (no LLM, no network).

    uv run python scripts/check_ia_cost_reference.py
    uv run python scripts/check_ia_cost_reference.py --write --fte-basis section_6_2 --by <name>

For every item of ``evals/cost_reference/ai_act_swd2021_84.yaml`` it finds each ``ia_anchors``
phrase in the cached IA (``.cache/ia/<fixture>/ia_full.txt``, written by
``womm.eval.ia_sources.cache_ia``; normalised with ``womm.citations.normalize``) and looks for
the item's figures (``eur_low`` / ``eur_high`` / ``fte_low`` / ``fte_high`` / ``fte_options``)
in the text right after it, on the same line: EUR figures need a currency mark (or follow one in
a range), FTE figures an "FTE" after them, and per-year / per-day items the period word. It
accepts 2 763, 2,763, 2.763, 2763, decimals with a comma, and "million" / "m" / "bn".

One line per item: OK, MISMATCH (expected figures and the numbers found after the anchor) or
NOT FOUND (no anchor phrase in the IA). A person looks at those lines only.

``--write`` sets ``status: verified``, ``verified_by`` (the script, and ``--by`` when given) and
``verified_on`` (today), and records ``--fte-basis`` (which of the IA's two EU-level FTE figures
is used) in the item's ``fte_basis``; it refuses unless every item is OK and the basis is given.
Exit codes: 0 every item OK, 1 a mismatch or missing anchor, 2 bad input or no IA cache.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from womm.citations import normalize
from womm.eval.cost_check import REFERENCE_PATH, CostReference, ReferenceItem
from womm.eval.ia_sources import IaSourceError, load_cached_ia

FIXTURE = "ai_act"
WINDOW = 200  # characters after the anchor, on the anchor's line
SCRIPT = "scripts/check_ia_cost_reference.py"

# A grouped number (2 763, 3,000, 10 733.33, 1.000) or a plain one (2763, 0,1, 7.5).
_NUMBER = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:[ .,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)(?!\d)")
_CURRENCY_BEFORE = re.compile(r"(?:€|\beur|\beuros?)\s*$")
_CURRENCY_AFTER = re.compile(r"^\s*(?:€|eur\b|euros?\b)")
_RANGE_BEFORE = re.compile(r"(?:-|\bto|\band|/)\s*$")
_MULTIPLIER = re.compile(r"^\s*(million|mn|m|billion|bn)\b")
_FTE_AFTER = re.compile(r"^[^.;|]{0,40}?(?:\bfte\b|full[ -]time equivalent)")
UNIT_WORDS: dict[str, tuple[str, ...]] = {
    "per_year": ("year", "annual"),
    "per_day": ("day",),
    "fte_per_member_state": ("member state", " ms"),
    "fte_eu": ("eu level", "at eu"),
}


class CheckError(ValueError):
    pass


@dataclass
class Number:
    primary: float  # the main reading, shown in MISMATCH lines
    values: set[float]  # every reading, compared with the expected figure
    currency: bool
    fte: bool

    def shown(self) -> str:
        v = self.primary
        mark = "EUR " if self.currency else ""
        return f"{mark}{v:g}{' FTE' if self.fte else ''}"


@dataclass
class ItemResult:
    item_id: str
    status: str  # OK, MISMATCH, NOT FOUND
    detail: str
    found: list[str] = field(default_factory=list)


def _candidates(raw: str) -> set[float]:
    """Every reading of a number: thousands separators dropped, and a lone ',' or '.' group
    read either way. Pieces are never read alone, so EUR 763 never matches EUR 2 763 (two
    numbers run together, like '2025 100', give a MISMATCH for a person to look at instead)."""
    out = {_primary(raw)}
    if re.fullmatch(r"\d{1,3}[.,]\d{3}", raw):
        out.add(float(raw.replace(",", ".")))  # 2,763 or 1.500 read as a decimal too
    return out


def _primary(raw: str) -> float:
    grouped = re.fullmatch(r"(\d{1,3}(?:[ .,]\d{3})+)(?:[.,](\d+))?", raw)
    if grouped:
        whole = re.sub(r"[ .,]", "", grouped.group(1))
        return float(whole + (f".{grouped.group(2)}" if grouped.group(2) else ""))
    return float(raw.replace(",", "."))


def numbers_in(text: str) -> list[Number]:
    """The numbers of a normalised text window with their currency, multiplier and FTE marks."""
    found: list[Number] = []
    prev_currency_end = -10
    for m in _NUMBER.finditer(text):
        before, after = text[max(0, m.start() - 8) : m.start()], text[m.end() :]
        values = _candidates(m.group())
        primary = _primary(m.group())
        mult = _MULTIPLIER.match(after)
        if mult:
            factor = 1e9 if mult.group(1) in ("billion", "bn") else 1e6
            values |= {v * factor for v in values}
            primary *= factor
        currency = bool(_CURRENCY_BEFORE.search(before) or _CURRENCY_AFTER.match(after))
        if not currency and _RANGE_BEFORE.search(before) and m.start() - prev_currency_end < 8:
            currency = True  # the second figure of a range like EUR 5 000 - 8 000
        if currency:
            prev_currency_end = m.end()
        found.append(Number(primary, values, currency, bool(_FTE_AFTER.match(after))))
    return found


def _same(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9 or (b == int(b) and round(a) == b and abs(a - b) < 1)


def expected_figures(item: ReferenceItem) -> list[tuple[float, str]]:
    out: list[tuple[float, str]] = []
    for value in (item.eur_low, item.eur_high):
        if value is not None and (value, "eur") not in out:
            out.append((value, "eur"))
    for value in (item.fte_low, item.fte_high, *item.fte_options.values()):
        if value is not None and (value, "fte") not in out:
            out.append((value, "fte"))
    return out


def _windows(anchor: str, lines: list[str], window: int) -> list[tuple[int, str]]:
    """(line number, text after the anchor) for every occurrence of ``anchor``."""
    needle = normalize(anchor)
    out = []
    for n, line in enumerate(lines, start=1):
        start = line.find(needle)
        while start >= 0:
            end = start + len(needle)
            out.append((n, line[end : end + window]))
            start = line.find(needle, end)
    return out


def check_item(item: ReferenceItem, lines: list[str], window: int = WINDOW) -> ItemResult:
    if not item.ia_anchors:
        return ItemResult(item.item_id, "NOT FOUND", "no ia_anchors in the reference")
    hits = {a: _windows(a, lines, window) for a in item.ia_anchors}
    missing = [a for a, w in hits.items() if not w]
    if missing:
        return ItemResult(item.item_id, "NOT FOUND", f"anchor not in the IA: {missing}")
    figures = expected_figures(item)
    words = UNIT_WORDS.get(item.unit, ())
    unmet: list[str] = []
    where: list[str] = []
    seen: list[str] = []
    for value, kind in figures:
        place = None
        for wins in hits.values():
            for line_no, text in wins:
                nums = numbers_in(text)
                seen.extend(n.shown() for n in nums)
                if words and not any(w in text for w in words):
                    continue
                if any(
                    any(_same(v, value) for v in n.values)
                    and (n.currency if kind == "eur" else n.fte)
                    for n in nums
                ):
                    place = f"IA line {line_no}"
                    break
            if place:
                break
        label = f"{'EUR ' if kind == 'eur' else ''}{value:g}{' FTE' if kind == 'fte' else ''}"
        if place:
            where.append(f"{label} ({place})")
        else:
            unmet.append(label)
    if item.negligible and not figures:
        text = " ".join(t for wins in hits.values() for _, t in wins)
        anchors = " ".join(normalize(a) for a in item.ia_anchors)
        if "negligible" not in text + anchors:
            return ItemResult(item.item_id, "MISMATCH", "negligible: the IA does not say so")
        return ItemResult(item.item_id, "OK", "negligible, as the IA says")
    if unmet:
        unit = f", {item.unit}" if words else ""
        found = list(dict.fromkeys(seen)) or ["no numbers"]
        return ItemResult(
            item.item_id,
            "MISMATCH",
            f"expected {', '.join(unmet)}{unit}; after the anchors: {', '.join(found)}",
            found,
        )
    return ItemResult(item.item_id, "OK", "; ".join(where))


def load(path: Path) -> tuple[CostReference, str]:
    try:
        text = path.read_text(encoding="utf-8")
        raw = yaml.safe_load(text)
        return CostReference.model_validate(raw), text
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise CheckError(f"cannot read {path}: {exc}") from None


def ia_lines(fixture: str, ia_root: Path | None) -> list[str]:
    try:
        cached = load_cached_ia(fixture, ia_root)
    except IaSourceError as exc:
        raise CheckError(str(exc)) from None
    if cached is None or not cached.full_text.strip():
        raise CheckError(
            f"no cached IA for {fixture}: fetch it first (scripts/draft_golden_case.py caches it "
            "through womm.eval.ia_sources.cache_ia)"
        )
    return [normalize(line) for line in cached.full_text.splitlines()]


def _set_once(text: str, pattern: str, value: str) -> str:
    new, n = re.subn(pattern, lambda m: m.group(1) + value, text, flags=re.M)
    if n != 1:
        raise CheckError(f"expected one line matching {pattern!r} in the reference, found {n}")
    return new


def write_verified(
    path: Path, text: str, ref: CostReference, basis: str, by: str | None, today: dt.date
) -> None:
    for item in ref.items:
        if item.fte_options and basis not in item.fte_options:
            raise CheckError(
                f"--fte-basis {basis!r} is not one of {sorted(item.fte_options)} ({item.item_id})"
            )
    who = f"{SCRIPT} (machine check)" + (f", run by {by}" if by else "")
    text = _set_once(text, r"^(status: )\S+$", "verified")
    text = _set_once(text, r"^(verified_by: ).*$", yaml.safe_dump(who).splitlines()[0])
    text = _set_once(text, r"^(verified_on: ).*$", f'"{today.isoformat()}"')
    if any(i.fte_options for i in ref.items):
        text = _set_once(text, r"^(    fte_basis: ).*$", basis)
    path.write_text(text, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", type=Path, default=REFERENCE_PATH)
    parser.add_argument("--fixture", default=FIXTURE, help="the IA cache under .cache/ia/")
    parser.add_argument("--ia-root", type=Path, default=None, help="default .cache/ia")
    parser.add_argument("--window", type=int, default=WINDOW, help=argparse.SUPPRESS)
    parser.add_argument("--write", action="store_true", help="mark verified when all are OK")
    parser.add_argument(
        "--fte-basis", default=None, help="the EU-level FTE figure used, e.g. section_6_2"
    )
    parser.add_argument("--by", default=None, help="who ran the check (recorded with --write)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, today: dt.date | None = None) -> int:
    args = parse_args(argv)
    try:
        ref, text = load(args.reference)
        options = sorted({k for i in ref.items for k in i.fte_options})
        if args.write and options and args.fte_basis is None:
            raise CheckError(
                f"--write needs --fte-basis: the IA gives two EU-level FTE figures {options}"
            )
        if args.fte_basis is not None and args.fte_basis not in options:
            raise CheckError(f"--fte-basis must be one of {options}")
        lines = ia_lines(args.fixture, args.ia_root)
    except CheckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    results = [check_item(i, lines, args.window) for i in ref.items]
    for r in results:
        print(f"{r.status:<9} {r.item_id}: {r.detail}")
    bad = [r for r in results if r.status != "OK"]
    print(f"{len(results) - len(bad)}/{len(results)} OK")
    if bad:
        if args.write:
            print("not written: look at the lines above first", file=sys.stderr)
        return 1
    if args.write:
        try:
            write_verified(args.reference, text, ref, args.fte_basis, args.by,
                           today or dt.date.today())  # fmt: skip
        except CheckError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote status: verified to {args.reference}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
