import re
from pathlib import Path

import pytest

from womm.citations import normalize
from womm.data import parse_regulation
from womm.data.parse_consolidated import (
    ConsolidatedParseError,
    amended_articles,
    amending_act_articles,
    parse_consolidated,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
SAMPLE = FIXTURES / "consolidated_sample.xhtml"
OJ_ARTICLE_9 = FIXTURES / "reg2024_1689_article9_sample.xhtml"

# Consolidation labels (▼B, ▼M1, ►M1, ▲, ◄), their amending-act codes and the deletion rule.
MARKER = re.compile(r"[▼▲►◄]|\bM\d+\b|—{3,}")


@pytest.fixture(scope="module")
def articles():
    return {a.number: a for a in parse_consolidated(SAMPLE.read_bytes(), source=SAMPLE.name)}


def test_sample_articles_in_document_order(articles):
    assert list(articles) == ["4", "4a", "9", "10", "113"]


def test_article_113_has_number_title_and_moved_dates(articles):
    art = articles["113"]
    assert art.title == "Entry into force and application"
    assert len(art.paragraphs) == 1 and art.paragraphs[0].number is None
    text = art.text
    assert text.startswith("This Regulation shall enter into force on the twentieth day")
    assert "2 December 2027" in text
    annex_i = "2 August 2028 as regards AI systems classified as high-risk pursuant to Article 6(1)"
    assert annex_i in text
    # Points sit on their own lines, nested points too, in WOMM's '(a) text' layout.
    lines = text.split("\n")
    assert "However:" in lines
    assert any(
        line.startswith("(a) Chapters I and II shall apply from 2 February 2025") for line in lines
    )
    assert any(line.startswith("(i) 2 December 2027 as regards") for line in lines)
    assert lines[-1] == "(d) Articles 102 to 110 shall apply from 27 July 2026."


def test_numbered_paragraphs_follow_womm_numbering(articles):
    art = articles["4"]
    assert art.title == "AI literacy"
    assert [p.number for p in art.paragraphs] == ["1", "2", "3"]
    assert art.paragraphs[0].text.startswith("Providers and deployers of AI systems shall take")
    assert art.text.startswith("1. Providers and deployers")


def test_unamended_article_equals_the_2024_official_journal_text(articles):
    oj = parse_regulation.parse_articles(parse_regulation.parse_document(OJ_ARTICLE_9.read_bytes()))
    (art_9_2024,) = oj
    ours = articles["9"]
    assert ours.title == art_9_2024.title
    assert [p.number for p in ours.paragraphs] == [p.number for p in art_9_2024.paragraphs]
    assert normalize(ours.text) == normalize(art_9_2024.text)


def test_amendment_markers_never_leak_into_text(articles):
    raw = SAMPLE.read_text(encoding="utf-8")
    assert "▼M1" in raw and "—————" in raw  # the excerpt really carries markers
    for art in articles.values():
        assert not MARKER.search(art.title), art.number
        assert not MARKER.search(art.text), (art.number, MARKER.search(art.text))


def test_deleted_paragraph_disappears(articles):
    # 2026/1744 deleted Article 10(5) (moved to the new Article 4a); its '—————' slot is dropped.
    assert [p.number for p in articles["10"].paragraphs] == ["1", "2", "3", "4", "6"]


def test_inserted_article_keeps_its_number_and_does_not_merge(articles):
    art = articles["4a"]
    assert (
        art.title
        == "Processing of special categories of personal data for bias detection and correction"
    )
    assert [p.number for p in art.paragraphs] == ["1", "2"]
    assert "bias detection" not in articles["4"].text
    # The trailing subparagraph belongs to paragraph 2, as in the Official Journal layout.
    assert art.paragraphs[1].text.endswith(
        "This paragraph does not create any obligation to conduct such bias detection "
        "and correction."
    )
    assert "\n(f) the records of processing activities" in art.paragraphs[0].text


def test_document_without_articles_raises_naming_the_file():
    data = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p class="norm">x</p></body></html>'
    with pytest.raises(ConsolidatedParseError, match="empty_doc.xhtml"):
        parse_consolidated(data, source="empty_doc.xhtml")


def test_unrecognised_heading_raises_naming_the_file_and_article():
    data = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<div class="eli-subdivision" id="art_7"><p class="title-article-norm">Annex 7</p></div>'
        b"</body></html>"
    )
    with pytest.raises(ConsolidatedParseError, match=r"odd\.xhtml.*art_7"):
        parse_consolidated(data, source="odd.xhtml")


def test_footnote_calls_are_dropped_with_their_brackets():
    # Real markup of Article 40(3), inserted by 2026/1744.
    data = (
        b'<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        b'<div class="eli-subdivision" id="art_40">'
        b'<p class="title-article-norm">Article 40</p>'
        b'<div class="eli-title"><p class="stitle-article-norm">Harmonised standards</p></div>'
        b'<p class="norm">Regulation (EU) No 1025/2012 of the European Parliament and of the '
        b'Council (<a href="#E0001" id="src.E0001">\n  <span class="superscript">1</span>\n'
        b"</a>) and without undue delay, the European standardisation organisations</p>"
        b"</div></body></html>"
    )
    (art,) = parse_consolidated(data, source="inline.xhtml")
    assert art.text == (
        "Regulation (EU) No 1025/2012 of the European Parliament and of the Council and without "
        "undue delay, the European standardisation organisations"
    )


def test_amended_articles_come_from_the_markers():
    # 4 is replaced whole and 4a inserted (marker before them), 10 and 113 carry markers
    # inside; 9 is unamended.
    assert amended_articles(SAMPLE.read_bytes(), source=SAMPLE.name) == ["4", "4a", "10", "113"]


def test_amended_articles_ignore_corrigendum_markers_and_follow_the_last_marker():
    data = (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
        '<p class="modref">▼C1</p>'
        '<div id="art_1"><p class="title-article-norm">Article 1</p></div>'
        '<p class="modref">▼M2</p>'
        '<div id="art_2"><p class="title-article-norm">Article 2</p></div>'
        '<p class="modref">▼B</p>'
        '<div id="art_3"><p class="title-article-norm">Article 3</p></div>'
        "</body></html>"
    ).encode()
    assert amended_articles(data, source="m.xhtml") == ["2"]


def test_amended_articles_without_articles_raises_naming_the_file():
    data = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body></html>'
    with pytest.raises(ConsolidatedParseError, match="none.xhtml"):
        amended_articles(data, source="none.xhtml")


def _amending_act(*points: tuple[str, list[str]]) -> bytes:
    """An OJ amending act whose Article 1 holds one two-cell table per point."""
    rows = []
    for label, lines in points:
        body = "".join(f'<p class="oj-normal">{ln}</p>' for ln in lines)
        rows.append(
            f'<table><tbody><tr><td><p class="oj-normal">({label})</p></td>'
            f"<td>{body}</td></tr></tbody></table>"
        )
    return (
        '<html xmlns="http://www.w3.org/1999/xhtml"><body><div id="art_1">'
        '<p class="oj-ti-art">Article 1</p>' + "".join(rows) + "</div></body></html>"
    ).encode()


def test_amending_act_articles_reads_targets_and_inserted_headings():
    data = _amending_act(
        ("1", ["in Article 1(2), point (g) is replaced by the following:", "‘(g) text"]),
        ("2", ["Article 4 is replaced by the following:", "‘Article 4", "AI literacy"]),
        ("3", ["the following Article is inserted:", "‘Article 4a", "Processing"]),
        ("4", ["the following articles are inserted:", "‘Article 75a", "x", "Article 75b"]),
        ("5", ["in Article 6, the following paragraphs are inserted:", "‘1a. text"]),
        ("6", ["Annex I is amended as follows:", "(a) in Section A, point 1 is deleted;"]),
        ("7", ["Article 4 is amended as follows:", "(a) text"]),
    )
    assert amending_act_articles(data, source="act.xhtml") == ["1", "4", "4a", "75a", "75b", "6"]


def test_amending_act_articles_rejects_gaps_in_point_numbers():
    data = _amending_act(("1", ["Article 4 is replaced:"]), ("3", ["Article 5 is replaced:"]))
    with pytest.raises(ConsolidatedParseError, match=r"gap\.xhtml.*1\.\.N"):
        amending_act_articles(data, source="gap.xhtml")


def test_amending_act_without_article_1_raises_naming_the_file():
    data = b'<html xmlns="http://www.w3.org/1999/xhtml"><body><p>x</p></body></html>'
    with pytest.raises(ConsolidatedParseError, match="none.xhtml"):
        amending_act_articles(data, source="none.xhtml")
