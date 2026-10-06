"""Read the colleague's provision units (calderonsamuel/course-cs-project-fall-2026-data) into
the ``Article`` objects the fixture build consumes.

Each JSONL line is one unit (article, paragraph, subparagraph, point, ...) with ``unit_id``,
``parent_id``, ``seq``, ``type``, ``num`` ("1.", "(a)" or null), ``label`` ("1", "a"),
``heading`` and ``text`` (the unit's own text, without its children). Where a unit has its own
text both before and after its children, the text holds the marker `` […] `` at the gap. Their
``full_text`` appends all children after all own text, which reorders the law around points; it is
not used here.

Rendering reproduces the layout of our Cellar parse:

- each numbered paragraph is one ``Paragraph`` (``Article.text`` adds "N. " and joins with blank
  lines); an article whose own text introduces points directly ("Providers ... shall:") is one
  unnumbered ``Paragraph``;
- points and subparagraphs go on their own line under their parent, points prefixed by ``num``;
- children are inserted at the first gap whose preceding text ends with ":" (else the first gap);
  further gaps only separate blocks of own text.

The proposal's units merge unnumbered subparagraphs into their paragraph's text, so those
paragraphs come out as one line where the Cellar parse had several; the words are the same.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass

from womm.data.fixtures import CrosswalkEntry, FixtureError
from womm.data.parse_proposal import Article, Paragraph

GAP = " […] "


@dataclass(frozen=True)
class Unit:
    unit_id: str
    parent_id: str | None
    seq: int
    type: str
    num: str | None
    label: str
    heading: str | None
    text: str


def parse_units(data: bytes, name: str = "units") -> list[Unit]:
    units: list[Unit] = []
    for lineno, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            units.append(
                Unit(
                    unit_id=row["unit_id"],
                    parent_id=row["parent_id"],
                    seq=int(row["seq"]),
                    type=row["type"],
                    num=row["num"],
                    label=row["label"],
                    heading=row["heading"],
                    text=row["text"] or "",
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise FixtureError(f"{name} line {lineno}: malformed unit ({exc})") from None
    ids = [u.unit_id for u in units]
    if len(set(ids)) != len(ids):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise FixtureError(f"{name}: duplicate unit_id {dupes[:5]}")
    known = set(ids)
    for u in units:
        if u.parent_id is not None and u.parent_id not in known:
            raise FixtureError(f"{name}: {u.unit_id} has unknown parent_id {u.parent_id!r}")
    return units


class _Tree:
    def __init__(self, units: list[Unit]):
        self.children: dict[str, list[Unit]] = {}
        for u in sorted(units, key=lambda u: u.seq):
            if u.parent_id is not None:
                self.children.setdefault(u.parent_id, []).append(u)

    def kids(self, unit: Unit) -> list[Unit]:
        return self.children.get(unit.unit_id, [])

    def block(self, unit: Unit) -> str:
        """A unit with its descendants, one line per own-text block, point or subparagraph."""
        prefix = f"{unit.num} " if unit.type == "point" and unit.num else ""
        children = [self.block(k) for k in self.kids(unit)]
        # An annex section's heading ("Section A. ...", "1.Introduction") is a line of its own.
        heading = [unit.heading] if unit.type == "annex_section" and unit.heading else []
        if GAP in unit.text:
            pieces = unit.text.split(GAP)
            at = next((i for i, p in enumerate(pieces[:-1]) if p.rstrip().endswith(":")), 0)
            lines = [prefix + pieces[0], *pieces[1 : at + 1], *children, *pieces[at + 1 :]]
        elif unit.text:
            lines = [prefix + unit.text, *children]
        else:
            lines = [prefix.strip()] if prefix else []
            lines += children
        return "\n".join(heading + lines)

    def article(self, unit: Unit) -> Article:
        kids = self.kids(unit)
        paragraphs: list[Paragraph] = []
        if unit.text:
            # Own text introduces points directly (e.g. Art 16): one unnumbered block.
            lead = [k for k in kids if k.type != "paragraph"]
            lines = [unit.text, *(self.block(k) for k in lead)]
            paragraphs.append(Paragraph(None, "\n".join(lines)))
            kids = [k for k in kids if k.type == "paragraph"]
        for k in kids:
            number = k.label if k.type == "paragraph" and k.label else None
            paragraphs.append(Paragraph(number, self.block(k)))
        return Article(number=unit.label, title=unit.heading or "", paragraphs=tuple(paragraphs))


def parse_articles(units: list[Unit]) -> list[Article]:
    """Every article, in document order."""
    tree = _Tree(units)
    return [tree.article(u) for u in sorted(units, key=lambda u: u.seq) if u.type == "article"]


def parse_annexes(units: list[Unit]) -> list[Article]:
    """Every annex, in document order, as an ``Article`` numbered by its roman label with one
    unnumbered block: the annex's own text, then its sections and points on their own lines."""
    tree = _Tree(units)
    out = []
    for u in sorted(units, key=lambda u: u.seq):
        if u.type != "annex":
            continue
        lines = [u.text] if u.text else []
        lines += [tree.block(k) for k in tree.kids(u)]
        # Annex X of 2024/1689 carries its title in ``num`` ("ANNEX X Union legislative ...").
        title = u.heading or (u.num or "").removeprefix(f"ANNEX {u.label}").strip()
        out.append(
            Article(
                number=u.label,
                title=title,
                paragraphs=(Paragraph(None, "\n".join(lines)),),
            )
        )
    return out


def parse_containers(data: bytes, name: str = "containers") -> set[tuple[str, str]]:
    """(old container, new container) pairs, e.g. ("art71", "art99")."""
    try:
        rows = list(csv.DictReader(io.StringIO(data.decode("utf-8"))))
        return {(r["old_container"], r["new_container"]) for r in rows}
    except (KeyError, UnicodeDecodeError, csv.Error) as exc:
        raise FixtureError(f"{name}: malformed container alignment ({exc})") from None


def check_crosswalk(
    entries: Iterable[CrosswalkEntry],
    pairs: set[tuple[str, str]],
    old_version: str,
    new_version: str,
) -> None:
    """Every crosswalk entry present in both versions must be an aligned article pair."""
    for e in entries:
        old, new = e.articles.get(old_version), e.articles.get(new_version)
        if old is None or new is None:
            continue  # added or deleted between the versions
        if (f"art{old}", f"art{new}") not in pairs:
            raise FixtureError(
                f"crosswalk {e.provision_key!r} maps {old_version} Art {old} to "
                f"{new_version} Art {new}, which the upstream alignment does not pair"
            )
