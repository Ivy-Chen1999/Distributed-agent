from pathlib import Path

import pytest

from womm.data.parse_proposal import (
    parse_articles,
    parse_document,
    parse_memorandum,
    strip_sections,
)

SAMPLE = Path(__file__).parents[1] / "fixtures" / "com2021_206_sample.xhtml"


@pytest.fixture(scope="module")
def root():
    return parse_document(SAMPLE.read_bytes())


@pytest.fixture(scope="module")
def articles(root):
    return {a.number: a for a in parse_articles(root)}


def test_articles_split_on_titrearticle(articles):
    assert list(articles) == ["8", "9", "71", "85"]
    assert articles["9"].title == "Risk management system"
    assert articles["71"].title == "Penalties"


def test_numbered_paragraphs_recognised_in_order(articles):
    art9 = articles["9"]
    assert [p.number for p in art9.paragraphs] == [str(i) for i in range(1, 10)]
    assert art9.paragraphs[0].text.startswith("A risk management system shall be established")


def test_points_are_merged_into_their_paragraph(articles):
    para2 = articles["9"].paragraphs[1]
    assert para2.text.startswith("The risk management system shall consist")
    assert "(a) identification and analysis of the known and foreseeable risks" in para2.text
    assert "(d) adoption of suitable risk management measures" in para2.text
    # Running text (Text1) after points stays in the same paragraph.
    para4 = articles["9"].paragraphs[3]
    assert "In identifying the most appropriate risk management measures" in para4.text
    assert "(c) provision of adequate information pursuant to Article 13" in para4.text


def test_article_text_numbers_paragraphs(articles):
    text = articles["71"].text
    assert text.startswith("1. In compliance with the terms and conditions")
    assert "\n\n3. The following infringements" in text
    assert text.endswith("whichever is higher:")


def test_article_stops_at_titles_and_closing_formulas(articles):
    assert "TITLE X" not in articles["9"].text
    assert "CONFIDENTIALITY AND PENALTIES" not in articles["9"].text
    last = articles["85"].text
    assert last.endswith("[twelve months following the entry into force of this Regulation].")
    assert "Done at Brussels" not in last
    assert "FRAMEWORK OF THE PROPOSAL" not in last


def test_memorandum_sections_and_footnotes(root):
    sections = parse_memorandum(root)
    headings = [s.full_heading for s in sections]
    assert headings[:2] == [
        "1. CONTEXT OF THE PROPOSAL",
        "1.1. Reasons for and objectives of the proposal",
    ]
    assert "3.3. Impact assessment" in headings
    # The memorandum ends at the legislative text; the financial statement is not included.
    assert headings[-1].startswith("5.1.")
    assert all("Proposal for a" not in b for s in sections for b in s.blocks)
    reasons = " ".join(sections[1].blocks)
    assert "“A Union that strives for more”, that the Commission" in reasons


def test_strip_sections_removes_subsections(root):
    kept, removed = strip_sections(
        parse_memorandum(root),
        {
            "Results of ex-post evaluations, stakeholder consultations and impact assessments",
            "Proportionality",
            "Budgetary implications",
        },
    )
    assert removed == [
        "2.3. Proportionality",
        "3. RESULTS OF EX-POST EVALUATIONS, STAKEHOLDER CONSULTATIONS AND IMPACT ASSESSMENTS",
        "3.1. Stakeholder consultation",
        "3.3. Impact assessment",
        "4. BUDGETARY IMPLICATIONS",
    ]
    kept_headings = [s.full_heading for s in kept]
    assert "2.4. Choice of the instrument" in kept_headings
    assert "5. OTHER ELEMENTS" in kept_headings
    assert not any("impact assessment" in b.casefold() for s in kept for b in s.blocks)


def test_strip_unknown_heading_fails(root):
    with pytest.raises(ValueError, match="not found"):
        strip_sections(parse_memorandum(root), {"No such heading"})
