"""The on-demand memorandum: section 5 split into subsections, the proposal Title table, and the
per-expert selection."""

import pytest

from womm.data.corpus import load_default_corpus
from womm.data.fixtures import load_fixture
from womm.on_demand_memorandum import (
    load_titles,
    on_demand,
    on_demand_sources,
    split_other_elements,
    subsections_for,
)

OTHER = "com2021_206/memorandum/other_elements"


@pytest.fixture(scope="module")
def corpus():
    return load_default_corpus()


@pytest.fixture(scope="module")
def fixture():
    return load_fixture()


@pytest.fixture(scope="module")
def other(fixture):
    return fixture.sources[OTHER]


def test_split_yields_5_1_and_every_5_2_subsection_verbatim(other):
    parts = split_other_elements(other)
    numbers = [p.source_id.rsplit("/", 1)[1] for p in parts]
    assert numbers == ["5.1", *(f"5.2.{i}" for i in range(1, 9))]
    for p in parts:
        assert p.kind == "memorandum" and p.text in other.text
        assert p.title.startswith("COM(2021) 206, Explanatory memorandum, 5.")
    high_risk = parts[numbers.index("5.2.3")]
    assert high_risk.text.startswith("5.2.3. HIGH-RISK AI SYSTEMS (TITLE III)")
    assert "5.2.4." not in high_risk.text


def test_a_sentence_inside_a_subsection_is_found_verbatim_in_that_subsection(other):
    parts = {p.source_id.rsplit("/", 1)[1]: p for p in split_other_elements(other)}
    body = [line for line in parts["5.2.2"].text.split("\n")[1:] if line.strip()]
    sentence = body[0][:120]
    assert sentence and sentence in parts["5.2.2"].text and sentence in other.text


def test_every_proposal_article_maps_to_exactly_one_title(corpus):
    table = load_titles()
    articles = [r for r in corpus.index_rows(table.version) if r.kind == "article"]
    assert articles
    for row in articles:
        n = int(row.number.rstrip("abcdefghijklmnopqrstuvwxyz"))
        owners = [t for t, (lo, hi) in table.articles.items() if lo <= n <= hi]
        assert len(owners) == 1, row.number


def test_every_title_and_annex_has_a_subsection(corpus):
    table = load_titles()
    for title in table.articles:
        assert table.subsection_of(title) is not None, title
    annexes = [r for r in corpus.index_rows(table.version) if r.kind == "annex"]
    for row in annexes:
        title = table.title_of("annex", row.number)
        assert title is not None and table.subsection_of(title) is not None, row.number


def test_provisions_map_through_their_proposal_article(corpus):
    subs, unmapped = subsections_for(["ai_act/art/26"], corpus)  # proposal Art 29, Title III
    assert subs == ["5.2.3"] and unmapped == []
    assert subsections_for(["ai_act/art/5"], corpus)[0] == ["5.2.2"]
    # Penalties: final Art 99, proposal Art 71 (Title X).
    assert subsections_for(["ai_act/penalties/penalties"], corpus)[0] == ["5.2.8"]


def test_provisions_added_in_the_final_act_map_to_nothing(corpus):
    subs, unmapped = subsections_for(["ai_act/art/53", "ai_act/art/55"], corpus)
    assert subs == [] and unmapped == ["ai_act/art/53", "ai_act/art/55"]


def test_on_demand_sources_split_section_5_and_drop_section_1(fixture):
    memos = {k: s for k, s in fixture.sources.items() if s.kind == "memorandum"}
    out = on_demand_sources(memos)
    assert "com2021_206/memorandum/context" not in out and OTHER not in out
    assert "com2021_206/memorandum/legal_basis" in out
    assert f"{OTHER}/5.2.3" in out


def test_runs_without_the_memorandum_are_unchanged(fixture):
    provision = {k: s for k, s in fixture.sources.items() if s.kind != "memorandum"}
    assert on_demand_sources(provision) == provision


def test_only_the_legal_expert_gets_the_legal_basis(fixture, corpus):
    memos = list(on_demand_sources(dict(fixture.sources)).values())
    memos = [m for m in memos if m.kind == "memorandum"]
    keys = ["ai_act/art/26"]
    legal = {m.source_id for m in on_demand(memos, "legal", keys, corpus)}
    fiscal = {m.source_id for m in on_demand(memos, "fiscal", keys, corpus)}
    assert legal == {f"{OTHER}/5.2.3", "com2021_206/memorandum/legal_basis"}
    assert fiscal == {f"{OTHER}/5.2.3"}
