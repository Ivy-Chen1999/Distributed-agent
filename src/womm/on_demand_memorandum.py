"""The on-demand memorandum (system versions with ``memorandum: {mode: on_demand}``).

Run-time selection only: ``womm.data.memorandum`` builds and strips the memorandum sources at
import time, and the fixture and v1.0 inputs stay as they are.

The COM(2021) 206 explanatory memorandum reaches explore runs as three sources: section 1
(context), section 2 (legal basis) and section 5 (other elements). Section 5 explains the
proposal Title by Title in subsections 5.2.1 to 5.2.8. With the on-demand mode:

- ``split_other_elements`` replaces section 5 with one source per subsection (5.1, 5.2.1 …
  5.2.8). Each subsection's text is a verbatim slice of the original, so a quote inside one
  subsection still validates against it.
- ``on_demand`` gives an expert the 5.2.x subsections whose proposal Title covers a selected
  provision, section 2 only if it is the Legal expert, and never section 1 or 5.1.

A provision maps to a Title through its proposal-side row in the corpus index (provision keys
are stable across versions) and the committed table ``data/fixtures/ai_act/memorandum_titles.yaml``.
A provision with no proposal row (added in the final act) maps to no subsection.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import yaml

from womm.data.corpus import Corpus
from womm.data.fixtures import DEFAULT_FIXTURE_DIR
from womm.models.regulation import Source

TITLES_FILE = DEFAULT_FIXTURE_DIR / "memorandum_titles.yaml"
LEGAL_EXPERT = "legal"
_HEADING = re.compile(r"(?m)^(5\.\d(?:\.\d)?)\. ")


@dataclass(frozen=True)
class TitleTable:
    version: str
    source: str
    subsections: dict[str, tuple[str, ...]]
    articles: dict[str, tuple[int, int]]
    annexes: dict[str, str]

    def title_of(self, kind: str, number: str) -> str | None:
        if kind == "annex":
            return self.annexes.get(number)
        digits = re.match(r"\d+", number)
        if digits is None:
            return None
        n = int(digits.group(0))
        for title, (low, high) in self.articles.items():
            if low <= n <= high:
                return title
        return None

    def subsection_of(self, title: str) -> str | None:
        for sub, titles in self.subsections.items():
            if title in titles:
                return sub
        return None


@cache
def load_titles(path: Path = TITLES_FILE) -> TitleTable:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return TitleTable(
        version=data["version"],
        source=data["source"],
        subsections={k: tuple(v) for k, v in data["subsections"].items()},
        articles={k: (int(v[0]), int(v[1])) for k, v in data["articles"].items()},
        annexes={str(k): str(v) for k, v in data["annexes"].items()},
    )


def subsection_id(source_id: str, number: str) -> str:
    return f"{source_id}/{number}"


def split_other_elements(source: Source) -> list[Source]:
    """Section 5 as one source per numbered subsection (the 5.2 preamble, which only introduces
    5.2.1–5.2.8, is not a source). Each text runs from its heading to the next heading."""
    marks = [(m.start(), m.group(1)) for m in _HEADING.finditer(source.text)]
    out: list[Source] = []
    for i, (start, number) in enumerate(marks):
        if number == "5.2":
            continue
        end = marks[i + 1][0] if i + 1 < len(marks) else len(source.text)
        text = source.text[start:end].strip()
        heading = text.split("\n", 1)[0]
        out.append(
            Source(
                source_id=subsection_id(source.source_id, number),
                title=f"COM(2021) 206, Explanatory memorandum, {heading}",
                kind="memorandum",
                text=text,
            )
        )
    return out


def on_demand_sources(
    sources: dict[str, Source], table: TitleTable | None = None
) -> dict[str, Source]:
    """An explore run's sources with section 5 split into subsections and section 1 removed.
    Runs that do not read the proposal carry no memorandum and are returned unchanged."""
    table = table or load_titles()
    if table.source not in sources:
        return sources
    out: dict[str, Source] = {}
    for sid, src in sources.items():
        if sid == table.source:
            out.update({s.source_id: s for s in split_other_elements(src)})
        elif src.kind == "memorandum" and sid.endswith("/context"):
            continue
        else:
            out[sid] = src
    return out


def subsections_for(
    keys: Iterable[str], corpus: Corpus, table: TitleTable | None = None
) -> tuple[list[str], list[str]]:
    """(subsection numbers covering ``keys``, in 5.2.x order; keys that map to none)."""
    table = table or load_titles()
    found: set[str] = set()
    unmapped: list[str] = []
    for key in keys:
        row = corpus.row(table.version, key)
        title = table.title_of(row.kind, row.number) if row is not None else None
        sub = table.subsection_of(title) if title is not None else None
        if sub is None:
            unmapped.append(key)
        else:
            found.add(sub)
    return [s for s in table.subsections if s in found], unmapped


def on_demand(
    memos: list[Source], expert_id: str, keys: Iterable[str], corpus: Corpus,
    table: TitleTable | None = None,
) -> list[Source]:  # fmt: skip
    """The memorandum sources one expert gets: the subsections of the selected provisions'
    Titles, plus the legal-basis section for the Legal expert."""
    table = table or load_titles()
    subs, _ = subsections_for(keys, corpus, table)
    wanted = {subsection_id(table.source, s) for s in subs}
    return [
        m for m in memos
        if m.source_id in wanted
        or (expert_id == LEGAL_EXPERT and m.source_id.endswith("/legal_basis"))
    ]  # fmt: skip
