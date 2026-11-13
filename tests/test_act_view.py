"""Whole-act comparison for the Act screen (womm.act_view), on the committed corpus."""

import re

import pytest

from womm.act_view import compare, comparisons, paragraphs, regulation_entry
from womm.data.corpus import load_default_corpus
from womm.data.fixtures import FixtureError

PAIRS = [("com2021_206", "reg2024_1689"), ("reg2024_1689", "reg2024_1689_c20260727")]


@pytest.fixture(scope="module")
def corpus():
    return load_default_corpus()


@pytest.fixture(scope="module")
def omnibus(corpus):
    return {a.provision_key: a for a in compare(corpus, *PAIRS[1]).articles}


def _ws(text):
    return re.sub(r"\s+", " ", text).strip()


def test_paragraphs_key_by_number_and_occurrence():
    text = "Intro line\n1. First.\n(a) point;\n2. Second.\nSection B\n1. Again first."
    blocks = paragraphs(text)
    assert [(b.label, b.key) for b in blocks] == [
        ("", "#1"),
        ("1", "1#1"),
        ("2", "2#1"),
        ("1", "1#2"),
    ]
    assert blocks[1].text == "1. First.\n(a) point;"
    assert blocks[2].text == "2. Second.\nSection B"


def test_comparisons_follow_the_index_delta_basis(corpus):
    assert [(c.before.version_id, c.after.version_id) for c in comparisons(corpus)] == PAIRS


def test_regulation_entry_lists_versions_and_comparisons(corpus):
    e = regulation_entry(corpus)
    assert e.regulation_id == "ai_act"
    assert [v.version_id for v in e.versions] == [
        "com2021_206",
        "reg2024_1689",
        "reg2024_1689_c20260727",
    ]
    assert [(c.before.version_id, c.after.version_id) for c in e.comparisons] == PAIRS


def test_unknown_pair_is_refused(corpus):
    with pytest.raises(FixtureError):
        compare(corpus, "reg2024_1689", "com2021_206")


@pytest.mark.parametrize("pair", PAIRS)
def test_article_changes_agree_with_the_index(corpus, pair):
    view = compare(corpus, *pair)
    rows = {r.key: r.delta for r in corpus.index_rows(pair[1])}
    for a in view.articles:
        if a.provision_key in rows:
            assert a.change == rows[a.provision_key], a.provision_key


@pytest.mark.parametrize("pair", PAIRS)
def test_paragraphs_lose_no_text(corpus, pair):
    """Splitting keeps every word, and pairing uses each paragraph of each side exactly once."""
    before, after = (corpus.version(v).by_key() for v in pair)
    for a in compare(corpus, *pair).articles:
        for side, texts in (("before_text", before), ("after_text", after)):
            if a.provision_key not in texts:
                continue
            text = texts[a.provision_key].text
            blocks = [b.text for b in paragraphs(text)]
            assert _ws(" ".join(blocks)) == _ws(text), a.provision_key
            paired = [getattr(p, side) for p in a.paragraphs if getattr(p, side) is not None]
            assert sorted(paired) == sorted(blocks), a.provision_key


def test_unchanged_article_keeps_its_records_and_moved_dates(omnibus):
    """Art 26 is not amended: the consolidated side borrows the 2024 deployer duties, whose
    application dates moved (withheld, not invented)."""
    art = omnibus["ai_act/art/26"]
    assert art.change == "unchanged" and not art.superseded_before
    after = [p.after_obligations for p in art.paragraphs]
    deployer = sum(s.by_actor.get("deployer", 0) for s in after)
    moved = sum(s.date_withheld.get("date_moved_2026", 0) for s in after)
    assert deployer > 0 and moved > 0


def test_amended_article_has_no_borrowed_records(omnibus):
    """Art 27 is amended: 2024 duties stay on the before side only (Corpus.obligation_records)."""
    art = omnibus["ai_act/art/27"]
    assert art.change == "modified" and art.superseded_before
    assert sum(p.before_obligations.duties for p in art.paragraphs if p.before_obligations) > 0
    assert all(p.after_obligations.duties == 0 for p in art.paragraphs if p.after_obligations)
    assert {p.change for p in art.paragraphs} >= {"unchanged", "modified"}


def test_inserted_article_is_all_added(omnibus):
    art = omnibus["ai_act/art/4a"]
    assert art.change == "added" and art.before_number is None
    assert all(p.change == "added" and p.before_text is None for p in art.paragraphs)


def test_annex_sections_do_not_collide(omnibus):
    """Annex XI restarts its numbering per section; matching by occurrence keeps it unchanged."""
    art = omnibus["ai_act/annex/XI"]
    assert art.kind == "annex" and art.change == "unchanged"
    assert all(p.change == "unchanged" for p in art.paragraphs)
