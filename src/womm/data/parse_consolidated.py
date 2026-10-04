"""Parse the EUR-Lex consolidated XHTML of the AI Act (e.g. CELEX 02024R1689-20260727).

Returns ``Article`` objects in the same shape as ``womm.data.parse_regulation`` gives for
the Official Journal text, so the two versions diff cleanly. The consolidated markup differs
from the OJ markup:

- an article is ``div#art_N`` (inserted ones keep their printed number, e.g. ``art_4a``) with
  ``p.title-article-norm`` ("Article 4a") and ``div.eli-title > p.stitle-article-norm``;
- a numbered paragraph is ``div.norm`` holding ``span.no-parag`` ("1.  ") and a
  ``div.norm.inline-element`` with either inline text or ``p`` blocks and point lists; further
  subparagraphs of that paragraph may follow it as loose sibling ``p.norm`` blocks;
- a point is ``div.grid-container.grid-list`` with a label cell (``span`` "(a) ") and a body
  cell, nested for sub-points;
- amendment markers are ``p.modref`` blocks ("▼M1", "▼B"); a deletion is a marker followed by
  "—————". They are dropped, with whatever they carry;
- footnote calls are ``(<a href="#E0001">1</a>)``; they are dropped, as ``parse_regulation``
  drops the OJ's.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from lxml import etree

from womm.data.parse_proposal import Article, Paragraph, parse_document
from womm.data.parse_regulation import _lines as oj_lines

__all__ = [
    "ConsolidatedParseError",
    "amended_articles",
    "amending_act_articles",
    "parse_articles",
    "parse_consolidated",
    "parse_document",
]

_ARTICLE_ID = re.compile(r"art_\d+[a-z]*")
_HEADING = re.compile(r"Article\s+(\d+[a-z]*)")
_PARA_NUM = re.compile(r"(\d+[a-z]*)\.")
_BLOCK = {"p", "div", "table"}
_PUNCT_ONLY = re.compile(r"[;,.:]+")
# "▼M1", "▼M1 —————": text from an amending act ("▼B" is the basic act, "▼C1" a corrigendum).
_AMENDMENT_MARKER = re.compile(r"▼M\d+")


class ConsolidatedParseError(ValueError):
    """The consolidated document does not have the expected structure; names the source."""


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _classes(el: etree._Element) -> set[str]:
    return set((el.get("class") or "").split())


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _is_marker(el: etree._Element) -> bool:
    return "modref" in _classes(el)


def _is_footnote_call(el: etree._Element) -> bool:
    return _local(el) == "a" and (el.get("href") or "").startswith("#E")


def _inline(el: etree._Element, pieces: list[str]) -> None:
    """Append the visible text of inline element ``el`` (not its tail) to ``pieces``."""
    pieces.append(el.text or "")
    for child in el:
        if not isinstance(child.tag, str):
            pieces.append(child.tail or "")
            continue
        if _is_footnote_call(child):
            _drop_footnote_call(child, pieces)
            continue
        _inline(child, pieces)
        pieces.append(child.tail or "")


def _drop_footnote_call(anchor: etree._Element, pieces: list[str]) -> None:
    """Drop " (<a>1</a>)" with the space before it: "the Council (1), the" -> "the Council, the".

    This matches the OJ parse (``parse_regulation``) wherever the OJ puts the space inside the
    footnote anchor (Articles 102-109); where it puts it outside (Article 78, "Council ;"), the
    two differ by that one space.
    """
    if pieces:
        before = pieces[-1].rstrip()
        if before.endswith("("):
            pieces[-1] = before[:-1].rstrip()
    tail = anchor.tail or ""
    stripped = tail.lstrip()
    pieces.append(stripped[1:] if stripped.startswith(")") else tail)


def _text(el: etree._Element) -> str:
    pieces: list[str] = []
    _inline(el, pieces)
    return _clean("".join(pieces))


def _point(grid: etree._Element) -> list[str]:
    """'(a) first line' plus the remaining lines of the point body (sub-points included)."""
    cells = [c for c in grid if _local(c) == "div"]
    label = next((_text(c) for c in cells if "grid-list-column-1" in _classes(c)), "")
    body_cell = next((c for c in cells if "grid-list-column-2" in _classes(c)), None)
    body = _lines(body_cell) if body_cell is not None else []
    first = body[0] if body else ""
    return [_clean(f"{label} {first}"), *body[1:]]


def _lines(container: etree._Element) -> list[str]:
    """Text lines of ``container``'s content in order."""
    return _lines_of(container, container.text or "")


def _lines_of(children: Iterable[etree._Element], text: str = "") -> list[str]:
    """Text lines of ``children`` in order (``text`` is inline content before them). Runs of
    inline content between blocks (``div.norm.inline-element`` often holds bare text) form one
    line each; a block's tail is inline content after it."""
    out: list[str] = []
    run: list[str] = [text]

    def flush() -> None:
        text = _clean("".join(run))
        if text and out and _PUNCT_ONLY.fullmatch(text):
            out[-1] += text  # e.g. the ';' after a quoted block belongs to the line before
        elif text:
            out.append(text)
        run.clear()

    for child in children:
        if not isinstance(child.tag, str):
            run.append(child.tail or "")
            continue
        tag = _local(child)
        if _is_footnote_call(child):
            _drop_footnote_call(child, run)
            continue
        if tag not in _BLOCK:
            _inline(child, run)
            run.append(child.tail or "")
            continue
        flush()
        if _is_marker(child):
            pass
        elif tag == "p":
            text = _text(child)
            if text:
                out.append(text)
        elif tag == "div" and "grid-container" in _classes(child):
            out.extend(_point(child))
        elif (para := _paragraph_number(child, quoted=True)) is not None:
            # A numbered paragraph quoted inside an amending article ("‘3.  " + text).
            label, span = para
            body = _lines_of(c for c in child if c is not span)
            first = body[0] if body else ""
            out.extend([_clean(f"{label} {first}"), *body[1:]])
        elif tag == "table":
            for row in child.iter("{*}tr"):
                text = _clean(" ".join(_text(c) for c in row if _local(c) in {"td", "th"}))
                if text:
                    out.append(text)
        else:
            out.extend(_lines(child))
        run.append(child.tail or "")
    flush()
    return out


def _paragraph_number(
    div: etree._Element, *, quoted: bool = False
) -> tuple[str, etree._Element] | None:
    """(number, span) when ``div`` is a numbered paragraph (``div.norm > span.no-parag``).
    With ``quoted``, any label is accepted and returned as printed (e.g. "‘3.")."""
    if _local(div) != "div" or "norm" not in _classes(div):
        return None
    span = next((c for c in div if "no-parag" in _classes(c)), None)
    if span is None:
        return None
    label = _text(span)
    if quoted:
        return (label, span) if label else None
    m = _PARA_NUM.fullmatch(label)
    return (m.group(1), span) if m else None


def _article(div: etree._Element, source: str) -> Article:
    heading = next((c for c in div if "title-article-norm" in _classes(c)), None)
    match = _HEADING.fullmatch(_text(heading)) if heading is not None else None
    if not match:
        raise ConsolidatedParseError(f"{source}: unrecognised article heading in {div.get('id')}")
    title = div.find(".//{*}p[@class='stitle-article-norm']")

    intro: list[str] = []
    numbered: list[tuple[str, list[str]]] = []
    for child in div:
        if not isinstance(child.tag, str) or child is heading or _is_marker(child):
            continue
        if "eli-title" in _classes(child):
            continue
        para = _paragraph_number(child)
        if para is not None:
            number, span = para
            numbered.append((number, _lines_of(c for c in child if c is not span)))
            continue
        # Loose blocks: an unnumbered article's text, or further subparagraphs of the
        # numbered paragraph they follow.
        target = numbered[-1][1] if numbered else intro
        target.extend(_lines_of([child]))

    paragraphs = [Paragraph(n, "\n".join(lines)) for n, lines in numbered if lines]
    if intro:
        paragraphs.insert(0, Paragraph(None, "\n".join(intro)))
    return Article(match.group(1), _text(title) if title is not None else "", tuple(paragraphs))


def parse_articles(root: etree._Element, source: str = "<document>") -> list[Article]:
    """All articles (``div#art_N``, inserted ``art_Na`` included) in document order."""
    divs = [d for d in root.iter("{*}div") if _ARTICLE_ID.fullmatch(d.get("id", ""))]
    return [_article(d, source) for d in divs]


def amended_articles(data: bytes, *, source: str) -> list[str]:
    """Numbers of the articles an amending act changed, read off the consolidation markers.

    An article is amended when it holds a ``▼Mn`` marker, or when the last marker before it in
    document order is one (the article was replaced or inserted whole, e.g. Article 4 and 4a by
    2026/1744). This is the authoritative change set: comparing texts also flags articles whose
    consolidated text differs from the Official Journal only by footnotes or spacing.
    """
    try:
        root = parse_document(data)
    except etree.XMLSyntaxError as exc:
        raise ConsolidatedParseError(f"{source}: not well-formed XHTML: {exc}") from None
    amended: list[str] = []
    last_marker = ""
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if _is_marker(el):
            last_marker = _text(el)
        elif _local(el) == "div" and _ARTICLE_ID.fullmatch(el.get("id", "")):
            inner = (_text(m) for m in el.iter() if isinstance(m.tag, str) and _is_marker(m))
            if _AMENDMENT_MARKER.match(last_marker) or any(
                _AMENDMENT_MARKER.match(m) for m in inner
            ):
                amended.append(el.get("id").removeprefix("art_"))
    if not any(_ARTICLE_ID.fullmatch(d.get("id", "")) for d in root.iter("{*}div")):
        raise ConsolidatedParseError(f"{source}: no articles (div#art_N) found")
    return amended


def parse_consolidated(data: bytes, *, source: str) -> list[Article]:
    """Parse a consolidated XHTML body; ``source`` (a file name or URL) names it in errors."""
    try:
        root = parse_document(data)
    except etree.XMLSyntaxError as exc:
        raise ConsolidatedParseError(f"{source}: not well-formed XHTML: {exc}") from None
    articles = parse_articles(root, source)
    if not articles:
        raise ConsolidatedParseError(f"{source}: no articles (div#art_N) found")
    numbers = [a.number for a in articles]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        raise ConsolidatedParseError(f"{source}: duplicate articles {duplicates}")
    return articles


_POINT_LABEL = re.compile(r"\((\d+)\)\s")
_TARGET_ARTICLE = re.compile(r"\bArticle\s+(\d+[a-z]*)")
_INSERTED_HEADING = re.compile(r"‘?Article\s+(\d+[a-z]*)")


def amending_act_articles(data: bytes, *, source: str) -> list[str]:
    """Articles of the amended act that an amending act's Article 1 changes, in point order.

    Article 1 of an amending regulation (e.g. 2026/1744) is a list of numbered points, each a
    two-cell OJ table: "(5) Article 4 is replaced by the following:", "(8) in Article 6, the
    following paragraphs are inserted:". A point that inserts articles names them only in its
    quoted headings ("‘Article 4a"). Points that change annexes name no article and are skipped.
    This is the independent cross-check for ``amended_articles``.
    """
    try:
        root = parse_document(data)
    except etree.XMLSyntaxError as exc:
        raise ConsolidatedParseError(f"{source}: not well-formed XHTML: {exc}") from None
    art_1 = next((d for d in root.iter("{*}div") if d.get("id") == "art_1"), None)
    if art_1 is None:
        raise ConsolidatedParseError(f"{source}: no Article 1 (div#art_1)")
    out: dict[str, None] = {}
    numbers: list[int] = []
    for table in (c for c in art_1 if _local(c) == "table"):
        lines = oj_lines([table])  # the OJ point layout: '(5) text'
        label = _POINT_LABEL.match(lines[0]) if lines else None
        if not label:
            raise ConsolidatedParseError(f"{source}: Article 1 point without a '(N)' label")
        numbers.append(int(label.group(1)))
        first = lines[0]
        if "inserted" in first and _TARGET_ARTICLE.search(first) is None:
            targets = [m.group(1) for ln in lines[1:] if (m := _INSERTED_HEADING.fullmatch(ln))]
        else:
            m = _TARGET_ARTICLE.search(first)
            targets = [m.group(1)] if m else []
        out.update(dict.fromkeys(targets))
    if not numbers:
        raise ConsolidatedParseError(f"{source}: Article 1 has no numbered points")
    if numbers != list(range(1, len(numbers) + 1)):
        raise ConsolidatedParseError(f"{source}: Article 1 points are not numbered 1..N")
    return list(out)
