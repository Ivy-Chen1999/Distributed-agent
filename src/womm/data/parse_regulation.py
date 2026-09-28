"""Parse the Official Journal XHTML of an adopted act (e.g. Regulation (EU) 2024/1689).

Unlike the proposal, the OJ markup has stable ids: an article is ``div#art_N`` with
``p.oj-ti-art`` ("Article 99") and ``p.oj-sti-art`` (its title); numbered paragraphs are child
``div#NNN.NNN`` whose text starts with "1.   ". Points are two-cell tables ("(a)" | text), which
may nest for sub-points. Footnote calls are ``<a href="#ntr...">``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from lxml import etree

from womm.data.parse_proposal import Article, Paragraph, parse_document

__all__ = ["parse_articles", "parse_document"]

_PARA_ID = re.compile(r"\d{3}\.\d{3}")
_PARA_NUM = re.compile(r"^(\d+[a-z]?)\.\s+")


def _local(el: etree._Element) -> str:
    return etree.QName(el).localname if isinstance(el.tag, str) else ""


def _clean(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _text(el: etree._Element) -> str:
    pieces: list[str] = []

    def walk(node: etree._Element, top: bool) -> None:
        if _local(node) == "a" and (node.get("href") or "").startswith("#ntr"):
            pieces.append(node.tail or "")
            return
        pieces.append(node.text or "")
        for child in node:
            walk(child, False)
        if not top:
            pieces.append(node.tail or "")

    walk(el, True)
    return _clean("".join(pieces))


def _lines(blocks: Iterable[etree._Element]) -> list[str]:
    """Text lines of block elements (an element iterates its children) in order; each point
    row becomes '(a) text'."""
    out: list[str] = []
    for child in blocks:
        tag = _local(child)
        if tag == "p":
            text = _text(child)
            if text:
                out.append(text)
        elif tag == "table":
            for row in child.iter("{*}tr"):
                if next(row.iterancestors("{*}table")) is not child:
                    continue  # nested rows are handled by their own table
                cells = [c for c in row if _local(c) == "td"]
                if not cells:
                    continue
                label = _text(cells[0])
                body = _lines(cells[1]) if len(cells) > 1 else []
                first = body[0] if body else ""
                out.append(_clean(f"{label} {first}"))
                out.extend(body[1:])
        elif tag == "div":
            out.extend(_lines(child))
    return out


def _article(div: etree._Element) -> Article:
    heading = div.find("{*}p[@class='oj-ti-art']")
    title = div.find(".//{*}p[@class='oj-sti-art']")
    match = re.fullmatch(r"Article\s+(\d+[a-z]?)", _text(heading) if heading is not None else "")
    if not match:
        raise ValueError(f"unrecognised article heading in {div.get('id')}")

    paragraphs: list[Paragraph] = []
    loose: list[etree._Element] = []
    for child in div:
        if child is heading or child.get("class") == "eli-title":
            continue
        if _local(child) == "div" and _PARA_ID.fullmatch(child.get("id", "")):
            lines = _lines(child)
            if not lines:
                continue
            m = _PARA_NUM.match(lines[0])
            number = m.group(1) if m else None
            lines[0] = lines[0][m.end() :] if m else lines[0]
            paragraphs.append(Paragraph(number, "\n".join(lines)))
        else:
            loose.append(child)
    if loose:
        text = "\n".join(_lines(loose))
        if text:
            paragraphs.insert(0, Paragraph(None, text))
    return Article(match.group(1), _text(title) if title is not None else "", tuple(paragraphs))


def parse_articles(root: etree._Element) -> list[Article]:
    """All articles (``div#art_N``) in document order."""
    divs = [d for d in root.iter("{*}div") if re.fullmatch(r"art_\d+[a-z]?", d.get("id", ""))]
    return [_article(d) for d in divs]
