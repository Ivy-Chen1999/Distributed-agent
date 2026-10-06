"""Parse the Word-derived XHTML of a Commission proposal (e.g. COM(2021) 206, Cellar DOC_1).

The document has no stable structural ids. It is a flat sequence of ``<p>`` elements whose
class says what they are:

- explanatory memorandum headings: ``li ManualHeading1..4`` (a ``span.num`` holds "3.3.", or
  a bullet "•" in some proposals, whose number is then derived from the position)
- articles start at ``p.Titrearticle`` or a variant (``Titrearticle0``, ``Titrearticleb``, ...):
  "Article 9" + ``<br/>`` + title, or "Article 9" alone with the title in the next ``Normal``
  paragraph; a heading in that class that is not "Article N" is the title when it directly
  follows an untitled "Article N" (COM(2021) 202, COM(2021) 731), the body when it follows a
  titled one (COM(2026) 599), and otherwise a chapter heading. A next ``Normal`` paragraph that
  reads as a sentence (ends in ".", ":" or ";") is the body of an untitled article, not a title
- numbered paragraphs: ``li ManualNumPar1`` (or ``li Point0`` numbered "1."); points
  ``li Point0/1/2``, dashes ``li ListDash*``, running text ``Text1`` / ``Normal`` belong to the
  paragraph they follow
- ``SectionTitle`` / ``ChapterTitle`` (TITLE / Chapter headings), ``Applicationdirecte``,
  ``Fait`` and the legislative financial statement end an article

Tested on COM(2021) 206 (byte-for-byte characterization), COM(2022) 68 and COM(2022) 454.
A markup variant this module does not know yields missing articles or empty article texts,
not an exception, so callers that need completeness run ``check_article_headings``,
``check_article_sequence`` and ``check_article_texts`` on the result.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from lxml import etree

XHTML_NS = "http://www.w3.org/1999/xhtml"

# Article headings: ``Titrearticle`` and Word's variants (``Titrearticle0``, ``Titrearticleb``,
# ``Titrearticlef``, ``Titrearticlefa``, ...).
_ARTICLE_START = re.compile(r"Titrearticle[0-9a-z]*")
# Classes that terminate the current article without starting a new one.
_ARTICLE_END = {
    "ChapterTitle",
    "SectionTitle",
    "Applicationdirecte",
    "Fait",
    "Institutionquisigne",
    "Personnequisigne",
    "Fichefinanciretitre",
}
_PARAGRAPH_NUM = re.compile(r"\d+[a-z]?\.")
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


_HEADING_NUMBER = re.compile(r"^(?:\d+|[IVX]+|[A-Z]|•)(?:\.\d+)*\.?$")


def _heading_num(el: etree._Element) -> tuple[str | None, str]:
    """(number, spilled text) of a memorandum heading.

    Some sources put the previous section's last paragraph inside the heading's number span
    (``<span class="num"><span>Body text.</span><span>3.</span></span>``, COM(2021) 762). The
    last child span is then the number, and the text before it belongs to the previous section.
    """
    for node in el.iter(f"{{{XHTML_NS}}}span"):
        if node.get("class") != "num":
            continue
        parts = [_clean("".join(c.itertext())) for c in node if isinstance(c.tag, str)]
        parts = [p for p in parts if p]
        if len(parts) > 1 and _HEADING_NUMBER.match(parts[-1]):
            return parts[-1], " ".join(parts[:-1])
        return _clean("".join(node.itertext())) or None, ""
    return None, ""


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


_BR = " "  # stands in for <br/> while a heading's text is collected


def _raw_text(el: etree._Element) -> str:
    """Text of ``el`` exactly as laid out (span boundaries add nothing), ``<br/>`` as _BR."""
    pieces: list[str] = []

    def walk(node: etree._Element) -> None:
        cls = node.get("class", "") if isinstance(node.tag, str) else ""
        if cls in ("FootnoteReference", "footnoteRef"):
            pieces.append(node.tail or "")
            return
        is_br = isinstance(node.tag, str) and etree.QName(node).localname == "br"
        pieces.append(_BR if is_br else (node.text or ""))
        for child in node:
            walk(child)
        pieces.append(node.tail or "")

    walk(el)
    pieces[-1] = ""
    return "".join(pieces)


def _article_heading(el: etree._Element) -> tuple[str, str] | None:
    """(number, title) of an article heading; None for a chapter/section heading that some
    documents (e.g. COM(2022) 454) put in the same ``Titrearticle*`` class.

    The number and the title are separated by ``<br/>``; Word splits both across spans, even
    mid-word ("Article 3" + "1", "C" + "ompensation"), so the raw text is joined first. A
    heading without ``<br/>`` has its title in the next paragraph (title is then "")."""
    raw = _raw_text(el)
    head, _, title = raw.partition(_BR)
    head, title = _clean(head), _clean(title)
    if not head.startswith("Article"):
        return None
    match = re.fullmatch(r"Article\s*(\d+[a-z]?)", head)
    if not match:
        raise ValueError(f"unrecognised article heading at line {el.sourceline}: {raw!r}")
    return match.group(1), title


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
        if _ARTICLE_START.fullmatch(cls):
            heading = _article_heading(el)
            if heading is not None or current is None or paragraphs:
                # An article heading, or a chapter heading in the same class (which ends the
                # article before it: an article always has a body before the next chapter).
                close()
                current = heading
                continue
            text = _text(el)
            if not current[1]:
                # "Article N" and its title as two paragraphs in the heading class
                # (COM(2021) 202, COM(2021) 731).
                current = (current[0], text)
                continue
            # A titled heading followed by its body in the heading class (COM(2026) 599).
            paragraphs.append([None, [text]])
            continue
        if current is None:
            continue
        if cls in _ARTICLE_END or _HEADING_RE.search(cls):
            close()
            continue
        if not current[1] and not paragraphs and cls == "Normal" and _num(el) is None:
            text = _text(el)
            if not text.endswith((".", ":", ";")):
                # Heading without <br/>: the first plain paragraph is the article title, unless
                # it reads as a sentence, i.e. the body of an untitled article ("This Regulation
                # shall enter into force ...", "Regulation (EU) 910/2014 is amended as follows:"
                # in COM(2021) 281).
                current = (current[0], text)
                continue
        if etree.QName(el).localname == "table":
            text = _clean(" ".join(el.itertext()))
        elif (
            "ManualNumPar1" in cls.split()
            or cls == "NumPar1"
            # Some proposals (e.g. COM(2022) 197) number paragraphs as "li Point0" with "1."
            or (cls == "li Point0" and _PARAGRAPH_NUM.fullmatch(_num(el) or ""))
        ):
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


@dataclass(frozen=True)
class Cover:
    issued: dt.date | None  # "Brussels, 23.2.2022"
    subject: str  # "on harmonised rules on fair access to and use of data (Data Act)"

    @property
    def short_title(self) -> str | None:
        """The act's own name when the subject gives one, e.g. "Data Act"."""
        for name in re.findall(r"\(([^()]+)\)", self.subject):
            if name.strip().endswith("Act"):
                return name.strip()
        return None


def parse_cover(root: etree._Element) -> Cover:
    """Issue date and subject line from the cover page (``Emission``, ``Titreobjet_cp``)."""
    issued, subject = None, ""
    for el in _blocks(root):
        cls = el.get("class", "")
        if cls == "Emission" and issued is None:
            m = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", _text(el))
            if m:
                issued = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        elif cls == "Titreobjet_cp" and not subject:
            subject = _text(el)
        if issued and subject:
            break
    return Cover(issued, subject)


def check_article_sequence(articles: list[Article], label: str) -> None:
    """Fail naming ``label`` unless the articles are Article 1..N in order, each once.

    Lettered articles ("10a") may follow their base number. Any markup the parser missed shows
    up here as zero articles or a gap, so a partial parse is never imported silently."""
    if not articles:
        raise ValueError(f"{label}: no articles found")
    numbers = [a.number for a in articles]
    dupes = sorted({n for n in numbers if numbers.count(n) > 1}, key=numbers.index)
    if dupes:
        raise ValueError(f"{label}: duplicate article numbers {dupes}")
    expected = 1
    for n in numbers:
        base = int(re.match(r"\d+", n).group())
        if n.isdigit():
            if base != expected:
                raise ValueError(
                    f"{label}: article numbering jumps to {n} where {expected} was expected"
                )
            expected += 1
        elif base != expected - 1:
            raise ValueError(f"{label}: Article {n} does not follow Article {base}")


def check_article_texts(articles: list[Article], label: str) -> None:
    """Fail naming ``label`` and the articles whose text is empty or only whitespace.

    Article texts are what the experts read; a body in markup the parser does not know would
    otherwise reach them as an empty provision."""
    empty = [a.number for a in articles if not any(p.text.strip() for p in a.paragraphs)]
    if empty:
        raise ValueError(f"{label}: empty text in Articles {empty}")


# Enacting terms end here; annexes and the financial statement may cite "Article N" alone.
_ENACTING_END = {"Fait", "Fichefinanciretitre"}
# Indented quotations: the articles an amending article inserts or replaces.
_QUOTED = re.compile(r"Text\d*")


def article_headings(root: etree._Element) -> list[str]:
    """Numbers of the standalone "Article N" headings of the enacting terms, in any paragraph
    class except indented quotations (an amending article's inserted text)."""
    numbers: list[str] = []
    for el in _blocks(root):
        cls = el.get("class", "")
        if cls in _ENACTING_END:
            break
        if etree.QName(el).localname != "p" or _QUOTED.fullmatch(cls):
            continue
        head = _clean(_raw_text(el).partition(_BR)[0])
        match = re.fullmatch(r"Article\s*(\d+[a-z]?)", head)
        if match:
            numbers.append(match.group(1))
    return numbers


def check_article_headings(articles: list[Article], root: etree._Element, label: str) -> None:
    """Fail naming ``label`` and the numbers of "Article N" headings that did not become
    articles, e.g. a heading in a paragraph class the parser does not know."""
    parsed = {a.number for a in articles}
    missed = [n for n in dict.fromkeys(article_headings(root)) if n not in parsed]
    if missed:
        raise ValueError(f"{label}: article headings not parsed as articles: {missed}")


def parse_memorandum(root: etree._Element) -> list[MemorandumSection]:
    """Explanatory memorandum sections (every heading level), up to the legislative text."""
    sections: list[MemorandumSection] = []
    for el in _blocks(root):
        cls = el.get("class", "")
        heading = _HEADING_RE.search(cls)
        if heading:
            level = int(heading.group(1))
            number, spilled = _heading_num(el)
            number = (number or "").strip()
            if spilled and sections:
                sections[-1].blocks.append(spilled)
            if not any(ch.isdigit() for ch in number):
                # Some memoranda number subsections with a bullet ("•"): derive "2.3." from
                # the parent section and the position, so headings stay unique and readable.
                number = _derived_number(sections, level)
            number = number if number.endswith(".") else f"{number}."
            sections.append(MemorandumSection(number, _text(el, skip_num=True), level))
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


def _derived_number(sections: list[MemorandumSection], level: int) -> str:
    """Number of a new level-``level`` section from the ones before it ("2.", 3rd -> "2.3.")."""
    parent = ""
    count = 0
    for s in reversed(sections):
        if s.level < level:
            parent = s.number
            break
        if s.level == level:
            count += 1
    return f"{parent}{count + 1}."


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
