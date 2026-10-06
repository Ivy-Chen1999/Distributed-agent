"""Proposals whose Word-derived markup differs from COM(2021) 206.

- COM(2022) 68 (Data Act), cut from Cellar celex/52022PC0068 DOC_1: ``Titrearticle0/b/f/fa``
  heading variants, ``ChapterTitle`` between chapters, headings split mid-word across spans
  ("Article 3" + "1", "C" + "ompensation") and bullet-numbered memorandum subsections ("•").
- COM(2022) 454 (Cyber Resilience Act), DOC_1 of the HTTP 300 listing: chapter headings in the
  same ``Titrearticle0`` class as articles, and article titles in the next ``Normal`` paragraph.
"""

from pathlib import Path

import pytest

from womm.data.parse_proposal import (
    Article,
    check_article_sequence,
    parse_articles,
    parse_document,
    parse_memorandum,
    strip_sections,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


@pytest.fixture(scope="module")
def data_act():
    return parse_document((FIXTURES / "com2022_68_sample.xhtml").read_bytes())


@pytest.fixture(scope="module")
def cra():
    return parse_document((FIXTURES / "com2022_454_sample.xhtml").read_bytes())


def test_heading_variants_are_articles(data_act):
    articles = parse_articles(data_act)
    assert [a.number for a in articles] == [str(n) for n in range(1, 43)]
    check_article_sequence(articles, "52022PC0068")


def test_titles_split_mid_word_are_joined(data_act):
    titles = {a.number: a.title for a in parse_articles(data_act)}
    assert titles["9"] == "Compensation for making data available"
    assert titles["31"] == "Competent authorities"
    assert titles["29"] == "Interoperability for data processing services"
    assert titles["13"].startswith("Unfair contractual terms unilaterally imposed on a micro")


def test_chapter_title_ends_an_article(data_act):
    for a in parse_articles(data_act):
        assert "CHAPTER" not in a.text, a.number
    art42 = parse_articles(data_act)[-1]
    assert "Done at Brussels" not in art42.text


def test_numbered_paragraphs(data_act):
    art9 = next(a for a in parse_articles(data_act) if a.number == "9")
    assert [p.number for p in art9.paragraphs] == ["1", "2"]
    assert art9.text.startswith("1. Any compensation agreed between a data holder")


def test_bullet_subsections_get_derived_numbers(data_act):
    headings = [s.full_heading for s in parse_memorandum(data_act)]
    assert "2.1. Proportionality" in headings
    assert "3.2. Impact assessment" in headings
    assert "5.1. Detailed explanation of the specific provisions of the proposal" in headings
    assert len(set(headings)) == len(headings)


def test_bullet_subsections_strip_with_their_parent(data_act):
    _, removed = strip_sections(
        parse_memorandum(data_act),
        {"Results of ex-post evaluations, stakeholder consultations and impact assessments"},
    )
    assert removed == [
        "3. RESULTS OF EX-POST EVALUATIONS, STAKEHOLDER CONSULTATIONS AND IMPACT ASSESSMENTS",
        "3.1. Stakeholder consultations",
        "3.2. Impact assessment",
    ]


def test_chapter_headings_in_article_class_are_not_articles(cra):
    articles = parse_articles(cra)
    assert [a.number for a in articles] == ["1", "2", "17", "18", "45"]
    for a in articles:
        assert "CHAPTER" not in a.text and "OBLIGATIONS OF ECONOMIC OPERATORS" not in a.text


def test_title_from_next_paragraph(cra):
    titles = {a.number: a.title for a in parse_articles(cra)}
    assert titles["1"] == "Subject matter"
    assert titles["18"] == "Presumption of conformity"
    art1 = parse_articles(cra)[0]
    assert art1.text.startswith("This Regulation lays down:")
    assert "Subject matter" not in art1.text


def _arts(*numbers: str) -> list[Article]:
    return [Article(n, "t", ()) for n in numbers]


def test_article_sequence_accepts_complete_numbering() -> None:
    check_article_sequence(_arts("1", "2", "2a", "3"), "52099PC0001")


@pytest.mark.parametrize(
    ("numbers", "message"),
    [
        ((), "52099PC0001: no articles found"),
        (("1", "2", "4"), "52099PC0001: article numbering jumps to 4 where 3 was expected"),
        (("2", "3"), "jumps to 2 where 1 was expected"),
        (("1", "2", "2"), r"52099PC0001: duplicate article numbers \['2'\]"),
        (("1", "3a", "2"), "Article 3a does not follow Article 3"),
    ],
)
def test_article_sequence_fails_naming_the_document(numbers, message) -> None:
    with pytest.raises(ValueError, match=message):
        check_article_sequence(_arts(*numbers), "52099PC0001")


def test_sample_with_a_missed_article_fails(data_act) -> None:
    articles = [a for a in parse_articles(data_act) if a.number != "29"]
    with pytest.raises(ValueError, match="52022PC0068: article numbering jumps to 30"):
        check_article_sequence(articles, "52022PC0068")


def test_paragraph_spilled_into_a_heading_number_goes_back_to_its_section():
    # COM(2021) 762: the last paragraph of "Choice of the instrument" sits inside the number
    # span of the next heading.
    doc = parse_document(
        b"""<html xmlns="http://www.w3.org/1999/xhtml"><body>
        <p class="li ManualHeading2"><span class="num"><span>2.4.</span></span>
          <span>Choice of the instrument</span></p>
        <p class="li ManualHeading1"><span class="num"><span>Directives may be used.</span>
          <span>3.</span></span><span>RESULTS OF STAKEHOLDER CONSULTATIONS</span></p>
        <p class="Normal"><span>Consulted.</span></p>
        </body></html>"""
    )
    sections = parse_memorandum(doc)
    assert [(s.full_heading, s.blocks) for s in sections] == [
        ("2.4. Choice of the instrument", ["Directives may be used."]),
        ("3. RESULTS OF STAKEHOLDER CONSULTATIONS", ["Consulted."]),
    ]
