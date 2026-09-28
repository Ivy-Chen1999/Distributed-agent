"""Parse the Word-derived XHTML of a Commission proposal (e.g. COM(2021) 206, Cellar DOC_1).

The document has no stable structural ids. It is a flat sequence of ``<p>`` elements whose
class says what they are:

- explanatory memorandum headings: ``li ManualHeading1..4`` (a ``span.num`` holds "3.3.")
- articles start at ``p.Titrearticle`` ("Article 9" + ``<br/>`` + title)
- numbered paragraphs: ``li ManualNumPar1``; points ``li Point0/1/2``, dashes ``li ListDash*``,
  running text ``Text1`` / ``Normal`` belong to the paragraph they follow
- ``SectionTitle`` (TITLE / Chapter headings), ``Applicationdirecte``, ``Fait`` and the
  legislative financial statement end an article
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lxml import etree

XHTML_NS = "http://www.w3.org/1999/xhtml"

# Classes that terminate the current article without starting a new one.
_ARTICLE_END = {
    "SectionTitle",
    "Applicationdirecte",
    "Fait",
    "Institutionquisigne",
    "Personnequisigne",
    "Fichefinanciretitre",
}
_MEMO_END = {"Statut", "Typedudocument", "Titreobjet"}
_HEADING_RE = re.compile(r"\bManualHeading(\d)\b")


@dataclass(frozen=True)
class Paragraph:
    number: str | None
    text: str


@dataclass(frozen=True)
class Article:
    number: str
    title: str
    paragraphs: tuple[Paragraph, ...]

    @property
    def text(self) -> str:
        parts = []
        for p in self.paragraphs:
            parts.append(f"{p.number}. {p.text}" if p.number else p.text)
        return "\n\n".join(parts)


@dataclass
class MemorandumSection:
    number: str
    heading: str
    level: int
    blocks: list[str] = field(default_factory=list)

    @property
    def full_heading(self) -> str:
        return f"{self.number} {self.heading}"


def parse_document(data: bytes) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    return etree.fromstring(data, parser)


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _text(el: etree._Element, *, skip_num: bool = False) -> str:
    """Visible text of ``el`` with footnote markers (and optionally the number span) removed."""
    pieces: list[str] = []

    def walk(node: etree._Element) -> None:
        cls = node.get("class", "") if isinstance(node.tag, str) else ""
        if cls in ("FootnoteReference", "footnoteRef") or (skip_num and cls == "num"):
            pieces.append(node.tail or "")
            return
        if isinstance(node.tag, str) and etree.QName(node).localname == "br":
            pieces.append(" ")
        else:
            pieces.append(node.text or "")
        for child in node:
            walk(child)
        pieces.append(node.tail or "")

    walk(el)
    pieces[-1] = ""  # the element's own tail is outside it
    return _clean("".join(pieces))


def _num(el: etree._Element) -> str | None:
    for node in el.iter(f"{{{XHTML_NS}}}span"):
        if node.get("class") == "num":
            return _clean("".join(node.itertext())) or None
    return None


def _blocks(root: etree._Element):
    body = root.find(f"{{{XHTML_NS}}}body")
    if body is None:
        raise ValueError("document has no XHTML body")
    for el in body.iter(f"{{{XHTML_NS}}}p", f"{{{XHTML_NS}}}table"):
        # Skip paragraphs nested in tables and footnote bodies: the table / footnote is handled
        # (or ignored) as a whole.
        if any(
            etree.QName(a).localname == "table" or a.get("class") == "footnote"
            for a in el.iterancestors()
        ):
            continue
        yield el


def _article_heading(el: etree._Element) -> tuple[str, str]:
    spans = [_clean("".join(s.itertext())) for s in el if etree.QName(s).localname == "span"]
    spans = [s for s in spans if s]
    match = re.fullmatch(r"Article\s+(\d+[a-z]?)", spans[0] if spans else "")
    if not match:
        raise ValueError(f"unrecognised article heading at line {el.sourceline}: {spans!r}")
    return match.group(1), " ".join(spans[1:])


def parse_articles(root: etree._Element) -> list[Article]:
    """All articles of the enacting terms, in document order.

    Points, dashes and running text are appended to the paragraph they follow, so no text of
    an article is lost; text before the first numbered paragraph forms an unnumbered one.
    """
    articles: list[Article] = []
    current: tuple[str, str] | None = None
    paragraphs: list[list] = []  # [number, [segments]]

    def close() -> None:
        nonlocal current, paragraphs
        if current is not None:
            paras = tuple(Paragraph(n, "\n".join(segs)) for n, segs in paragraphs if segs)
            articles.append(Article(current[0], current[1], paras))
        current, paragraphs = None, []

    for el in _blocks(root):
        cls = el.get("class", "")
        if cls == "Titrearticle":
            close()
            current = _article_heading(el)
            continue
        if current is None:
            continue
        if cls in _ARTICLE_END or _HEADING_RE.search(cls):
            close()
            continue
        if etree.QName(el).localname == "table":
            text = _clean(" ".join(el.itertext()))
        elif "ManualNumPar1" in cls.split() or cls == "NumPar1":
            number = (_num(el) or "").rstrip(".") or None
            paragraphs.append([number, [_text(el, skip_num=True)]])
            continue
        else:
            number = _num(el)
            body = _text(el, skip_num=True)
            text = f"{number} {body}" if number else body
        if not text:
            continue
        if not paragraphs:
            paragraphs.append([None, []])
        paragraphs[-1][1].append(text)
    close()
    return articles


def parse_memorandum(root: etree._Element) -> list[MemorandumSection]:
    """Explanatory memorandum sections (every heading level), up to the legislative text."""
    sections: list[MemorandumSection] = []
    for el in _blocks(root):
        cls = el.get("class", "")
        heading = _HEADING_RE.search(cls)
        if heading:
            number = (_num(el) or "").strip()
            number = number if number.endswith(".") else f"{number}."
            sections.append(
                MemorandumSection(number, _text(el, skip_num=True), int(heading.group(1)))
            )
            continue
        if cls in _MEMO_END and sections:
            break
        if not sections:
            continue
        if etree.QName(el).localname == "table":
            text = _clean(" ".join(el.itertext()))
        else:
            number = _num(el)
            body = _text(el, skip_num=True)
            text = f"{number} {body}" if number else body
        if text:
            sections[-1].blocks.append(text)
    return sections


def strip_sections(
    sections: list[MemorandumSection], headings: set[str]
) -> tuple[list[MemorandumSection], list[str]]:
    """Drop every section whose heading (case-insensitive) is in ``headings``, with its
    subsections. Returns the kept sections and the full headings removed, in order."""
    wanted = {h.casefold() for h in headings}
    kept: list[MemorandumSection] = []
    removed: list[str] = []
    drop_level: int | None = None
    for s in sections:
        if drop_level is not None and s.level > drop_level:
            removed.append(s.full_heading)
            continue
        drop_level = None
        if s.heading.casefold() in wanted:
            drop_level = s.level
            removed.append(s.full_heading)
            continue
        kept.append(s)
    missing = wanted - {s.heading.casefold() for s in sections}
    if missing:
        raise ValueError(f"memorandum headings to strip not found: {sorted(missing)}")
    return kept, removed
