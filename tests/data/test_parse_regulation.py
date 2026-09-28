from pathlib import Path

import pytest

from womm.data.parse_regulation import parse_articles, parse_document

SAMPLE = Path(__file__).parents[1] / "fixtures" / "reg2024_1689_sample.xhtml"


@pytest.fixture(scope="module")
def articles():
    return {a.number: a for a in parse_articles(parse_document(SAMPLE.read_bytes()))}


def test_articles_numbers_and_titles(articles):
    assert list(articles) == ["7", "16", "99", "103"]
    assert articles["99"].title == "Penalties"
    assert articles["16"].title == "Obligations of providers of high-risk AI systems"


def test_paragraph_ids_give_ordered_numbers_and_text(articles):
    paras = articles["99"].paragraphs
    assert [p.number for p in paras] == [str(i) for i in range(1, 12)]
    assert paras[0].text.startswith("In accordance with the terms and conditions")
    assert paras[2].text.startswith("Non-compliance with the prohibition of the AI practices")
    assert "EUR 35 000 000" in paras[2].text


def test_point_tables_merged_into_paragraph(articles):
    para4 = articles["99"].paragraphs[3].text
    assert para4.endswith(
        "(g) transparency obligations for providers and deployers pursuant to Article 50."
    )
    assert "\n(a) obligations of providers pursuant to Article 16;" in para4


def test_article_without_numbered_paragraphs(articles):
    art16 = articles["16"]
    assert [p.number for p in art16.paragraphs] == [None]
    assert art16.text.startswith("Providers of high-risk AI systems shall:\n(a) ensure that")


def test_nested_points_kept(articles):
    text = articles["7"].text
    assert (
        "(k) the extent to which existing Union law provides for:\n(i) effective measures" in text
    )


def test_footnote_calls_removed(articles):
    text = articles["103"].text
    assert "European Parliament and of the Council, the requirements set out" in text
    assert "(*)" not in text
