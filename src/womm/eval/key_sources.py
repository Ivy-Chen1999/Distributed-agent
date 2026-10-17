"""The texts a full-text key check reads (plan 2026-10-16-001, F1).

Per fixture: the impact assessment with its annex volumes (``ia_full.txt``), the Regulatory
Scrutiny Board's opinion (``rsb_opinion.txt``; when it is missing, the IA's own procedural annex
``rsb.txt`` stands in and every result says so) and the proposal's articles
(``data/fixtures/<f>/proposal.json``). Each source carries its sha256, so a verdict names the
exact text it read, and is cut into passages a verdict can cite: ``ia_full.txt:<line>``,
``rsb_opinion.txt:<line>``, ``proposal Art <n>(<para>)``.

The texts are public documents; holdout fixtures load the same way and stay on this machine.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from womm.config import REPO_ROOT
from womm.eval.ia_sources import ia_cache_dir

FIXTURES_ROOT = REPO_ROOT / "data" / "fixtures"
MAX_PASSAGE = 1500  # characters; a longer paragraph is cut at sentence ends

SourceName = Literal["ia", "rsb_opinion", "rsb_annex", "law"]


class KeySourceError(ValueError):
    """A fixture's sources are missing: nothing can be checked against them."""


@dataclass(frozen=True)
class Passage:
    source: SourceName
    where: str  # what a verdict cites: "ia_full.txt:610", "proposal Art 8(3)"
    text: str


@dataclass(frozen=True)
class Source:
    name: SourceName
    path: Path
    sha256: str
    passages: tuple[Passage, ...]


@dataclass(frozen=True)
class Sources:
    fixture: str
    items: tuple[Source, ...]

    @property
    def digests(self) -> dict[str, str]:
        return {s.name: s.sha256 for s in self.items}

    @property
    def opinion_missing(self) -> bool:
        """The board's opinion is not cached; the IA's procedural annex stands in."""
        return not any(s.name == "rsb_opinion" for s in self.items)

    @property
    def passages(self) -> list[Passage]:
        return [p for s in self.items for p in s.passages]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cut(text: str) -> list[str]:
    """A paragraph in pieces of at most ``MAX_PASSAGE`` characters, cut after a sentence."""
    if len(text) <= MAX_PASSAGE:
        return [text]
    pieces, rest = [], text
    while len(rest) > MAX_PASSAGE:
        cut = max(rest.rfind(". ", 0, MAX_PASSAGE), rest.rfind("; ", 0, MAX_PASSAGE))
        cut = cut + 1 if cut > MAX_PASSAGE // 3 else MAX_PASSAGE
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [*pieces, rest] if rest else pieces


def _line_passages(name: SourceName, path: Path, text: str, *, wrapped: bool) -> list[Passage]:
    """One passage per paragraph, cited by its first line. ``ia_full.txt`` holds a paragraph per
    line; the opinion is wrapped, so its paragraphs are runs of non-blank lines."""
    lines = text.splitlines()
    out: list[Passage] = []
    if not wrapped:
        for n, line in enumerate(lines, 1):
            if line.strip():
                out += [Passage(name, f"{path.name}:{n}", piece) for piece in _cut(line.strip())]
        return out
    start, buf = 0, []
    for n, line in enumerate([*lines, ""], 1):
        if line.strip():
            start = start or n
            buf.append(line.strip())
        elif buf:
            para = " ".join(buf)
            out += [Passage(name, f"{path.name}:{start}", piece) for piece in _cut(para)]
            start, buf = 0, []
    return out


def _law_passages(path: Path) -> list[Passage]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Passage] = []
    for version in data["versions"][:1]:  # the proposal as tabled
        for prov in version["provisions"]:
            art = prov.get("article") or prov["provision_key"].rsplit("/", 1)[-1]
            for para in re.split(r"\n\s*\n", prov.get("text") or ""):
                para = " ".join(para.split())
                if not para:
                    continue
                num = re.match(r"(\d+)\.\s", para)
                where = f"proposal Art {art}" + (f"({num.group(1)})" if num else "")
                out += [Passage("law", where, piece) for piece in _cut(para)]
    return out


def load_sources(
    fixture: str, ia_root: Path | None = None, fixtures_root: Path | None = None
) -> Sources:
    """The fixture's sources, each with its sha256 and passages; the IA is required."""
    directory = ia_cache_dir(fixture, ia_root)
    items: list[Source] = []
    ia = directory / "ia_full.txt"
    if not ia.exists() or not ia.stat().st_size:
        raise KeySourceError(f"{fixture}: no cached IA text at {ia}")
    raw = ia.read_bytes()
    ia_passages = _line_passages("ia", ia, raw.decode("utf-8"), wrapped=False)
    items.append(Source("ia", ia, _sha(raw), tuple(ia_passages)))
    opinion, annex = directory / "rsb_opinion.txt", directory / "rsb.txt"
    for name, path in (("rsb_opinion", opinion), ("rsb_annex", annex)):
        if path.exists() and path.stat().st_size:
            raw = path.read_bytes()
            found = _line_passages(name, path, raw.decode("utf-8"), wrapped=True)  # type: ignore[arg-type]
            items.append(Source(name, path, _sha(raw), tuple(found)))  # type: ignore[arg-type]
            break
    law = (fixtures_root or FIXTURES_ROOT) / fixture / "proposal.json"
    if law.exists():
        items.append(Source("law", law, _sha(law.read_bytes()), tuple(_law_passages(law))))
    return Sources(fixture, tuple(items))
