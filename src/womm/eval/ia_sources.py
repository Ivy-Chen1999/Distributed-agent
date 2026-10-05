"""Fetch, cut and cache a proposal's impact assessment (IA) and RSB opinion (R22, R24).

The IA is reference material for golden-case drafting only. It is never agent input: its text
lives only under ``.cache/ia/<fixture>/`` (gitignored), and ``ia_cache_dir`` refuses any
location inside ``data/fixtures/`` or ``evals/``. Its identifiers come from the gitignored local
index (``womm.data.ia_index``) and are never written to tracked files.

Cellar serves IAs as Word-derived XHTML (``text/html`` since 2025), as one document or as
several parts behind an HTTP 300 listing (``1_EN_impact_assessment_part<n>``). Headings are
``p.li Heading1`` / ``p.li Heading2`` / ``p.Heading3`` (the ``li`` variants are list items whose
level-1 numbering is often wrong, e.g. "1." for every chapter, so it is repaired from the first
level-2 child). ``extract_sections`` keeps the impact-relevant cut:

- ``impacts``: "What are the impacts of the policy options?"
- ``preferred_option``: "Preferred option"
- ``who_is_affected``: the "Who is affected and how?" annex
- ``costs``: other headings about costs (summary/overview tables), when not already inside a
  kept section
- ``procedural``: the "Procedural information" annex, which summarises the RSB opinion and how
  it was addressed; used as the RSB source when the opinion itself is not in Cellar (usual)

It fails, listing every heading it saw, when neither an impacts nor a preferred-option section
exists.

Layout of ``.cache/ia/<fixture>/``: ``raw/`` (Cellar bodies), ``ia_full.txt`` (all parts, for
anchor verification), ``cut.txt`` (the kept sections), ``rsb.txt`` (RSB source text) and
``manifest.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from lxml import etree

from womm.config import REPO_ROOT
from womm.data.cellar import (
    CellarError,
    CellarMultipleChoice,
    celex_url,
    fetch_document,
    parse_listing,
    sha256,
)
from womm.data.parse_proposal import _clean, _text, parse_document

IA_CACHE_ROOT = REPO_ROOT / ".cache" / "ia"
FORBIDDEN_ROOTS = (REPO_ROOT / "data", REPO_ROOT / "evals")

_HEADING_CLASS = re.compile(r"^Heading([1-4])$")
_SKIP_CLASSES = {"FootnoteText", "TOCHeading", "TOC1", "TOC2", "TOC3", "TOC4"}
_NUMBER = re.compile(r"^((?:\d+\.)+\d*)\s*")

SECTION_PATTERNS: dict[str, re.Pattern[str]] = {
    "impacts": re.compile(r"impacts? of the (?:policy |retained |different )?options", re.I),
    "preferred_option": re.compile(r"^(?:[\d.]+\s*)?(?:the )?preferred (?:policy )?option", re.I),
    "who_is_affected": re.compile(r"who is affected", re.I),
    "procedural": re.compile(r"procedural information", re.I),
    "costs": re.compile(
        r"(?:overview|summary) of (?:the )?(?:costs|benefits)|costs and benefits|cost[- ]benefit",
        re.I,
    ),
}
REQUIRED_ANY = ("impacts", "preferred_option")


class IaSourceError(RuntimeError):
    """The IA cannot be fetched, cut or cached."""


@dataclass
class Heading:
    level: int
    title: str
    part: int
    index: int  # position in the part's block list


@dataclass
class Section:
    kind: str
    title: str
    level: int
    part: int
    text: str

    @property
    def chars(self) -> int:
        return len(self.text)


@dataclass
class IaDocument:
    """One IA part as an ordered list of text blocks; headings point into it."""

    part: int
    blocks: list[str]
    headings: list[Heading]

    def full_text(self) -> str:
        return "\n\n".join(b for b in self.blocks if b)


@dataclass
class IaExtract:
    parts: list[IaDocument]
    sections: list[Section]
    headings_seen: list[str]
    missing: list[str] = field(default_factory=list)
    kept: list[Heading] = field(default_factory=list)

    def full_text(self) -> str:
        return "\n\n".join(p.full_text() for p in self.parts)

    def cut_text(self) -> str:
        return "\n\n".join(f"## {s.title}\n\n{s.text}" for s in self.sections)


# ----------------------------------------------------------------------------- parsing


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _classes(el: etree._Element) -> list[str]:
    return (el.get("class") or "").split()


def _heading_level(el: etree._Element) -> int | None:
    if _local(el) != "p":
        return None
    for cls in _classes(el):
        m = _HEADING_CLASS.match(cls)
        if m:
            return int(m.group(1))
    return None


def _table_text(table: etree._Element) -> str:
    rows = []
    for tr in table.iter():
        if _local(tr) != "tr":
            continue
        cells = [_text(td) for td in tr if _local(td) in ("td", "th")]
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _tidy_number(title: str) -> str:
    """'6.1.Unleashing' -> '6.1. Unleashing'."""
    return re.sub(r"^((?:\d+\.)+)(?=[^\d\s])", r"\1 ", title)


def parse_ia(body: bytes, part: int = 1) -> IaDocument:
    """Blocks (paragraphs, tables) and headings of one IA part, in document order."""
    try:
        root = parse_document(body)
    except etree.LxmlError as exc:
        raise IaSourceError(f"IA part {part} is not well-formed XHTML: {exc}") from None
    blocks: list[str] = []
    headings: list[Heading] = []
    for el in root.iter():
        name = _local(el)
        if name not in ("p", "table"):
            continue
        if any(_local(a) == "table" for a in el.iterancestors()):
            continue  # rendered with its table
        if name == "table":
            blocks.append(_table_text(el))
            continue
        if _SKIP_CLASSES.intersection(_classes(el)):
            continue
        text = _text(el)
        level = _heading_level(el)
        if level is not None and text:
            headings.append(Heading(level, _clean(_tidy_number(text)), part, len(blocks)))
            blocks.append(_clean(_tidy_number(text)))
        else:
            blocks.append(text)
    _repair_chapter_numbers(headings)
    return IaDocument(part, blocks, headings)


def _repair_chapter_numbers(headings: list[Heading]) -> None:
    """Level-1 list numbering is often lost ('1.' for every chapter): take the chapter number
    from its first numbered level-2 child ('6.1.' -> '6.')."""
    for i, h in enumerate(headings):
        if h.level != 1 or not _NUMBER.match(h.title):
            continue
        for child in headings[i + 1 :]:
            if child.level == 1:
                break
            m = re.match(r"^(\d+)\.\d", child.title)
            if child.level == 2 and m:
                h.title = _NUMBER.sub(f"{m.group(1)}. ", h.title, count=1)
                break


def _span(doc: IaDocument, h: Heading) -> str:
    """Text under ``h`` up to the next heading of the same or a higher level."""
    pos = doc.headings.index(h)
    end = len(doc.blocks)
    for nxt in doc.headings[pos + 1 :]:
        if nxt.level <= h.level:
            end = nxt.index
            break
    return "\n\n".join(b for b in doc.blocks[h.index + 1 : end] if b)


def _inside(doc: IaDocument, h: Heading, kept: list[Heading]) -> bool:
    """Is ``h`` within the span of an already kept heading of the same part?"""
    for k in kept:
        if k.part != h.part or k.index >= h.index:
            continue
        pos = doc.headings.index(k)
        end = next(
            (n.index for n in doc.headings[pos + 1 :] if n.level <= k.level), len(doc.blocks)
        )
        if h.index < end:
            return True
    return False


def extract_sections(parts: list[IaDocument]) -> IaExtract:
    """The impact-relevant cut of an IA; raises IaSourceError listing the headings seen."""
    found: dict[str, list[tuple[Heading, IaDocument]]] = {}
    kept: list[Heading] = []
    seen = [f"[part {h.part}] {'#' * h.level} {h.title}" for p in parts for h in p.headings]
    for kind in ("impacts", "preferred_option", "who_is_affected", "procedural", "costs"):
        pattern = SECTION_PATTERNS[kind]
        for doc in parts:
            for h in doc.headings:
                if not pattern.search(h.title) or _inside(doc, h, kept):
                    continue
                if kind != "costs" and h.level > 2:
                    continue  # chapter-level sections only; deeper matches are sub-topics
                kept.append(h)
                found.setdefault(kind, []).append((h, doc))
    if not set(found).intersection(REQUIRED_ANY):
        listing = "\n  ".join(seen) or "(no headings: not a Word-derived IA document?)"
        raise IaSourceError(
            "no 'impacts of the policy options' or 'preferred option' section found; "
            f"headings seen:\n  {listing}"
        )
    ordered = sorted(
        ((kind, h, doc) for kind, hits in found.items() for h, doc in hits),
        key=lambda t: (t[1].part, t[1].index),
    )
    sections = [Section(k, h.title, h.level, h.part, _span(doc, h)) for k, h, doc in ordered]
    missing = [k for k in SECTION_PATTERNS if k not in found and k != "costs"]
    return IaExtract(parts, sections, seen, missing, kept=[h for _, h, _ in ordered])


def select_sections(extract: IaExtract, wanted: list[str]) -> list[Section]:
    """Sub-sections of the cut whose heading starts with (or contains) one of ``wanted``.

    ``wanted`` entries are matched case-insensitively against heading titles, e.g. '6.2.3.' or
    'Annex 3'. A heading must lie inside the cut; otherwise the available headings are listed."""
    out: list[Section] = []
    available: list[str] = []
    for doc in extract.parts:
        cut = [h for h in extract.kept if h.part == doc.part]
        for h in doc.headings:
            if not (cut and (h in cut or _inside(doc, h, cut))):
                continue
            available.append(h.title)
            if any(_matches(h.title, w) for w in wanted):
                out.append(Section("selected", h.title, h.level, doc.part, _span(doc, h)))
    unmatched = [w for w in wanted if not any(_matches(s.title, w) for s in out)]
    if unmatched:
        raise IaSourceError(
            f"IA section(s) {unmatched} not found in the impact-relevant cut; available:\n  "
            + "\n  ".join(available)
        )
    return out


def _matches(title: str, wanted: str) -> bool:
    t, w = title.casefold(), wanted.casefold().strip()
    return t.startswith(w) or (not re.match(r"^[\d.]+$", w) and w in t)


# ----------------------------------------------------------------------------- fetching


def ia_cache_dir(fixture: str, root: Path | None = None) -> Path:
    """``.cache/ia/<fixture>``; refuses any location under data/ or evals/ (agent-visible or
    tracked), so IA text can never land in a fixture."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9_]*", fixture):
        raise IaSourceError(f"invalid fixture name {fixture!r}")
    path = ((root or IA_CACHE_ROOT) / fixture).resolve()
    assert_not_forbidden(path)
    return path


def assert_not_forbidden(path: Path) -> None:
    resolved = path.resolve()
    for forbidden in FORBIDDEN_ROOTS:
        if resolved == forbidden.resolve() or resolved.is_relative_to(forbidden.resolve()):
            raise IaSourceError(
                f"refusing to write IA material under {forbidden}: {resolved} "
                "(IA text lives only in .cache/ia/)"
            )


def _is_ia_part(stream_name: str) -> bool:
    name = stream_name.lower()
    return "impact_assessment" in name and not re.search(r"summary|resume|fiche", name)


def fetch_ia_parts(ia_celex: str, cache_dir: Path, **kwargs) -> list[tuple[str, bytes]]:
    """(URL, body) per IA part, in stream order; a single document is one part."""
    raw = cache_dir / "raw"
    assert_not_forbidden(raw)
    url = celex_url(ia_celex)
    try:
        return [(url, fetch_document(url, cache_dir=raw, **kwargs))]
    except CellarMultipleChoice as exc:
        items = [i for i in parse_listing(exc.listing) if _is_ia_part(i.stream_name)]
        if not items:
            names = [i.stream_name for i in parse_listing(exc.listing)]
            raise IaSourceError(
                f"{ia_celex}: no IA part in the HTTP 300 listing ({names})"
            ) from None
        items.sort(key=lambda i: (i.order is None, i.order or 0, i.stream_name))
        return [(i.url, fetch_document(i.url, cache_dir=raw, **kwargs)) for i in items]
    except (CellarError, httpx.HTTPError) as exc:
        raise IaSourceError(f"{ia_celex}: download failed: {exc}") from None


def rsb_url(rsb_ref: str) -> str | None:
    """Cellar ``comnat`` resource for an RSB reference such as 'SEC(2099) 7'."""
    m = re.fullmatch(r"\s*SEC\s*\(\s*(\d{4})\s*\)\s*(\d+)\s*(?:final)?\s*", rsb_ref, re.I)
    if not m:
        return None
    return (
        f"https://publications.europa.eu/resource/comnat/SEC_{m.group(1)}_{int(m.group(2)):04d}_FIN"
    )


def fetch_rsb(rsb_ref: str | None, cache_dir: Path, **kwargs) -> tuple[str, str | None]:
    """(status, text). RSB opinions are rarely in Cellar; 'none found' is a normal outcome."""
    if not rsb_ref:
        return "no RSB reference in the local IA index", None
    url = rsb_url(rsb_ref)
    if url is None:
        return "RSB reference not in SEC(YYYY) N form", None
    try:
        body = fetch_document(url, cache_dir=cache_dir / "raw", **kwargs)
    except (CellarError, httpx.HTTPError):
        return "opinion none found in Cellar", None
    try:
        return "opinion found in Cellar", parse_ia(body).full_text()
    except IaSourceError:
        return "opinion found in Cellar but not parseable", None


@dataclass
class CachedIa:
    """What drafting reads: never the identifiers, only text and section metadata."""

    fixture: str
    directory: Path
    full_text: str
    cut_text: str
    rsb_text: str
    rsb_status: str
    sections: list[dict]
    extract: IaExtract | None = None


def cache_ia(
    fixture: str,
    ia_celex: str,
    rsb_ref: str | None = None,
    *,
    root: Path | None = None,
    refresh: bool = False,
    client: httpx.Client | None = None,
) -> CachedIa:
    """Fetch every IA part (and the RSB opinion when Cellar has it), cut the impact-relevant
    sections and write them under ``.cache/ia/<fixture>/``."""
    directory = ia_cache_dir(fixture, root)
    kwargs = {"refresh": refresh} | ({"client": client} if client else {})
    fetched = fetch_ia_parts(ia_celex, directory, **kwargs)
    parts = [parse_ia(body, n) for n, (_, body) in enumerate(fetched, start=1)]
    extract = extract_sections(parts)
    rsb_status, rsb_text = fetch_rsb(rsb_ref, directory, **kwargs)
    if rsb_text is None:
        procedural = [s for s in extract.sections if s.kind == "procedural"]
        if procedural:
            rsb_status += "; using the IA's procedural-information annex"
            rsb_text = "\n\n".join(s.text for s in procedural)
    directory.mkdir(parents=True, exist_ok=True)
    full, cut = extract.full_text(), extract.cut_text()
    (directory / "ia_full.txt").write_text(full, encoding="utf-8")
    (directory / "cut.txt").write_text(cut, encoding="utf-8")
    (directory / "rsb.txt").write_text(rsb_text or "", encoding="utf-8")
    sections = [
        {k: v for k, v in asdict(s).items() if k != "text"} | {"chars": s.chars}
        for s in extract.sections
    ]
    manifest = {
        "fixture": fixture,
        "parts": [{"url": u, "sha256": sha256(b)} for u, b in fetched],
        "sections": sections,
        "missing_sections": extract.missing,
        "rsb_status": rsb_status,
        "headings_seen": extract.headings_seen,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return CachedIa(fixture, directory, full, cut, rsb_text or "", rsb_status, sections, extract)


def load_cached_ia(fixture: str, root: Path | None = None) -> CachedIa | None:
    """The cached IA text for ``fixture``, or None when it has not been fetched; no network."""
    directory = ia_cache_dir(fixture, root)
    manifest = directory / "manifest.json"
    if not manifest.exists():
        return None
    meta = json.loads(manifest.read_text(encoding="utf-8"))

    def read(name: str) -> str:
        path = directory / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return CachedIa(
        fixture,
        directory,
        read("ia_full.txt"),
        read("cut.txt"),
        read("rsb.txt"),
        meta.get("rsb_status", ""),
        meta.get("sections", []),
    )


def reparse_cached(fixture: str, root: Path | None = None) -> IaExtract:
    """Re-parse the cached raw bodies (no network), e.g. to select sections for drafting."""
    directory = ia_cache_dir(fixture, root)
    meta = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    parts = []
    for n, part in enumerate(meta["parts"], start=1):
        body = fetch_document(
            part["url"], cache_dir=directory / "raw", expected_sha256=part["sha256"]
        )
        parts.append(parse_ia(body, n))
    return extract_sections(parts)


# ----------------------------------------------------------------------------- overlap


def _words(text: str) -> list[str]:
    from womm.citations import normalize

    return re.findall(r"[a-z0-9]+", normalize(text))


# A fixture source restates its IA when it shares more than this share of its 12-grams with the
# IA's impact cut, or a run of this many words (measured 2026-10-04: at most 0.4% and 21 words).
RESTATE_MAX_SHARE, RESTATE_MAX_RUN = 0.02, 30


def restates_ia(text: str, cut_text: str) -> tuple[float, int] | None:
    """(share, run) when ``text`` restates the IA cut beyond the thresholds, else None."""
    share, run = ngram_overlap(text, cut_text)
    return (share, run) if share > RESTATE_MAX_SHARE or run >= RESTATE_MAX_RUN else None


def ngram_overlap(source: str, reference: str, n: int = 12) -> tuple[float, int]:
    """(share of ``source``'s word n-grams also in ``reference``, longest shared run in words).

    Used to prove no fixture source restates its IA: legal text quoted in both is short, a
    copied IA passage is not."""
    src, ref = _words(source), _words(reference)
    if len(src) < n:
        return 0.0, 0
    ref_grams = {tuple(ref[i : i + n]) for i in range(len(ref) - n + 1)}
    hits = [tuple(src[i : i + n]) in ref_grams for i in range(len(src) - n + 1)]
    longest = run = 0
    for hit in hits:
        run = run + 1 if hit else 0
        longest = max(longest, run)
    return sum(hits) / len(hits), (longest + n - 1 if longest else 0)
