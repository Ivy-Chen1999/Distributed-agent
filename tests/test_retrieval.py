"""Layer 1 retrieval: keys resolved through data scopes into sources plus retrieval records."""

import datetime as dt
import re

import pytest

from womm.config import REPO_ROOT
from womm.data.corpus import SUPERSEDED_MARK, Corpus, load_corpus
from womm.data.fixtures import Fixture, load_fixture
from womm.models.system_version import DataScope, load_system_version
from womm.retrieval import memorandum_sources, obligations_source, retrieve

PROPOSAL, FINAL, CONSOLIDATED = "com2021_206", "reg2024_1689", "reg2024_1689_c20260727"
ART26, ART99, ART36 = "ai_act/art/26", "ai_act/penalties/penalties", "ai_act/art/36"
ANNEX3 = "ai_act/annex/III"
HYP = "test"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return load_corpus()


@pytest.fixture(scope="module")
def fixture() -> Fixture:
    return load_fixture()


@pytest.fixture(scope="module")
def scopes() -> dict[str, DataScope]:
    sv = load_system_version(REPO_ROOT / "system_versions" / "v1.0-scoped.yaml", REPO_ROOT)
    out = {e.id: e.scope for e in sv.spec.experts}
    assert all(s is not None for s in out.values())
    return out


def _run(scope, keys, corpus, before=PROPOSAL, after=FINAL, store=None, agent="x"):
    return retrieve(scope, keys, before, after, store or corpus, corpus, agent=agent)


def _scope(**kw) -> DataScope:
    return DataScope.model_validate({"text": [], "obligations": "none", "hypothesis": HYP, **kw})


def test_default_scopes_resolved_keys(scopes, corpus, capsys):
    """Prints each default scope's text keys and what they resolve to, so gaps are reviewable."""
    for agent, scope in scopes.items():
        text = scope.text if scope.text == "all" else list(scope.text)
        print(
            f"{agent}: text={text} obligations={scope.obligations} "
            f"delta={scope.sees_delta} memorandum={scope.sees_memorandum}"
        )
        if isinstance(scope.text, list):
            for key in scope.text:
                found = [v.version_id for v in corpus.regulation.versions if key in v.by_key()]
                print(f"  {key}: {found}")
                assert found == [PROPOSAL, FINAL, CONSOLIDATED], f"{agent} scope key {key}"
    assert "stakeholder" in capsys.readouterr().out


def test_legal_default_scope_grants_both_texts_of_modified_keys(scopes, corpus):
    r = _run(scopes["legal"], [ART26, ART99], corpus, agent="legal")
    ids = [s.source_id for s in r.sources]
    assert ids == [
        f"{PROPOSAL}/art_29",
        f"{PROPOSAL}/art_71",
        f"{FINAL}/art_26",
        f"{FINAL}/art_99",
    ]
    assert all(s.kind == "provision" for s in r.sources)
    assert [(x.agent, x.layer, x.key, x.status) for x in r.records] == [
        ("legal", 1, ART26, "granted_text"),
        ("legal", 1, ART99, "granted_text"),
    ]
    assert r.records[1].source_ids == [f"{PROPOSAL}/art_71", f"{FINAL}/art_99"]
    assert r.granted_keys == [ART26, ART99] and r.refused_keys == []


def test_ae1_stakeholder_gets_actor_view_and_fiscal_is_refused_annex_iii(scopes, corpus):
    r = _run(scopes["stakeholder"], [ART99], corpus, agent="stakeholder")
    assert [x.status for x in r.records] == ["granted_obligations"]
    assert [s.source_id for s in r.sources] == [
        f"{PROPOSAL}/obligations/art_71",
        f"{FINAL}/obligations/art_99",
    ]
    assert all(s.kind == "obligations" for s in r.sources)
    final_view = r.sources[1].text
    records = corpus.obligations[FINAL][ART99]
    assert "action:" not in final_view
    blocks = {b.split("\n", 1)[0]: b for b in final_view.split("\n\n")}
    assert set(blocks) == {f"[{rec.obligation_id}]" for rec in records}
    for rec in records:
        if not rec.actor_unspecified:  # actor fields only: no action, no span
            block = blocks[f"[{rec.obligation_id}]"]
            assert rec.span not in block
            assert not rec.action or rec.action not in block

    # Fiscal: no text at all, and the proposal's Annex III has no obligation records.
    f = _run(scopes["fiscal"], [ANNEX3], corpus, before=None, after=PROPOSAL, agent="fiscal")
    assert f.sources == []
    assert [(x.agent, x.key, x.status, x.source_ids) for x in f.records] == [
        ("fiscal", ANNEX3, "out_of_scope", [])
    ]


def test_fiscal_never_gets_article_text(scopes, corpus):
    r = _run(scopes["fiscal"], [ART26, ART99, ART36], corpus, agent="fiscal")
    assert {s.kind for s in r.sources} == {"obligations"}
    assert {x.status for x in r.records} == {"granted_obligations"}


def test_empty_key_list(scopes, corpus):
    r = _run(scopes["legal"], [], corpus)
    assert r.sources == [] and r.records == []


def test_unknown_key_is_a_record_not_an_exception(scopes, corpus):
    r = _run(scopes["legal"], ["ai_act/art/999", ART26], corpus, agent="legal")
    assert [(x.key, x.status) for x in r.records] == [
        ("ai_act/art/999", "unknown_key"),
        (ART26, "granted_text"),
    ]
    assert r.refused_keys == ["ai_act/art/999"]


def test_duplicate_keys_give_one_record(scopes, corpus):
    r = _run(scopes["legal"], [ART26, ART26], corpus)
    assert [x.key for x in r.records] == [ART26]


def test_obligation_views_actors_full_none(corpus):
    actors = _run(_scope(obligations="actors"), [ART36], corpus, before=None).sources[0].text
    full = _run(_scope(obligations="full"), [ART36], corpus, before=None).sources[0].text
    none = _run(_scope(obligations="none"), [ART36], corpus, before=None)
    records = corpus.obligations[FINAL][ART36]
    named = next(r for r in records if not r.actor_unspecified)
    assert named.action and named.action not in actors and "action:" not in actors
    assert named.span not in actors
    assert named.action in full and named.span in full
    assert all(r.span in full for r in records)
    assert none.sources == [] and none.records[0].status == "out_of_scope"


def test_actor_view_span_fallback_for_unspecified_actors(corpus):
    text = _run(_scope(obligations="actors"), [ART36], corpus, before=None).sources[0].text
    records = corpus.obligations[FINAL][ART36]
    unspecified = [r for r in records if r.actor_unspecified]
    named = [r for r in records if not r.actor_unspecified]
    assert unspecified and named
    blocks = {b.split("\n", 1)[0]: b for b in text.split("\n\n")}
    for r in unspecified:
        assert f"span: {r.span}" in blocks[f"[{r.obligation_id}]"]
    for r in named:
        assert "span:" not in blocks[f"[{r.obligation_id}]"]


@pytest.mark.parametrize("view", ["actors", "full"])
def test_views_label_every_date_as_adopted_2024(corpus, view):
    text = _run(_scope(obligations=view), [ART36], corpus, before=None).sources[0].text
    dated = [r for r in corpus.obligations[FINAL][ART36] if r.applies_from]
    assert dated
    for r in dated:
        assert f"applies from: {r.applies_from} (as adopted (2024))" in text
    assert "applies from:" in text
    for line in text.splitlines():
        if line.startswith("applies from:"):
            assert line.endswith("(as adopted (2024))")


def test_withheld_dates_never_appear(corpus):
    """Article 99 was amended in 2026: its 2024 records carry no date in any view."""
    text = _run(_scope(obligations="full"), [ART99], corpus, before=None).sources[0].text
    assert "applies from" not in text
    assert "2026-08-02" not in text


def test_obligation_sources_are_citable_verbatim(corpus):
    """Every span is quotable from the full-view source text."""
    src = obligations_source(corpus, FINAL, ART99, "full")
    assert src.source_id == f"{FINAL}/obligations/art_99"
    assert "obligation records" in src.title
    assert all(r.span in src.text for r in corpus.obligations[FINAL][ART99])


def test_consolidated_obligations_only_for_unamended_articles(corpus):
    scope = _scope(obligations="full")
    amended = _run(scope, [ART99], corpus, before=None, after=CONSOLIDATED)
    assert amended.sources == [] and amended.records[0].status == "out_of_scope"
    inserted = _run(scope, ["ai_act/art/4a"], corpus, before=None, after=CONSOLIDATED)
    assert inserted.records[0].status == "out_of_scope"
    unamended = _run(scope, [ART36], corpus, before=None, after=CONSOLIDATED)
    assert [s.source_id for s in unamended.sources] == [f"{FINAL}/obligations/art_36"]
    # 2024 -> consolidated: the unamended article's one record set is one source.
    both = _run(scope, [ART36], corpus, before=FINAL, after=CONSOLIDATED)
    assert [s.source_id for s in both.sources] == [f"{FINAL}/obligations/art_36"]


def test_explore_text_resolves_against_the_corpus(corpus):
    r = _run(None, ["ai_act/art/4a"], corpus, before=FINAL, after=CONSOLIDATED)
    assert [s.source_id for s in r.sources] == [f"{CONSOLIDATED}/art_4a"]
    assert r.sources[0].text == corpus.version(CONSOLIDATED).by_key()["ai_act/art/4a"].text


def test_preset_text_resolves_against_the_fixture(fixture, corpus, scopes):
    s = fixture.scenario("eval_sme_impacts")
    r = retrieve(
        None, s.provision_keys, s.before_version, s.after_version, fixture, corpus, agent="legal"
    )
    expected = [x for x in fixture.scenario_sources("eval_sme_impacts") if x.kind != "memorandum"]
    assert r.sources == expected  # same objects, same order: v0 texts stay byte-stable
    assert [x.source_id for x in r.sources] == [
        f"{PROPOSAL}/art_53",
        f"{PROPOSAL}/art_54",
        f"{PROPOSAL}/art_55",
        f"{PROPOSAL}/art_71",
    ]
    # Obligation views still come from the corpus in preset mode.
    st = retrieve(
        scopes["stakeholder"],
        [ART99],
        s.before_version,
        s.after_version,
        fixture,
        corpus,
        agent="stakeholder",
    )
    assert [x.source_id for x in st.sources] == [f"{PROPOSAL}/obligations/art_71"]
    # A key outside the preset fixture is unknown there.
    assert (
        retrieve(None, [ART36], None, PROPOSAL, fixture, corpus, agent="a").records[0].status
        == "unknown_key"
    )


def test_preset_demo_order_matches_scenario_sources(fixture, corpus):
    s = fixture.scenario("demo_penalties_amended")
    r = retrieve(
        None,
        list(reversed(s.provision_keys)),
        s.before_version,
        s.after_version,
        fixture,
        corpus,
        agent="legal",
    )
    expected = [x for x in fixture.scenario_sources(s.scenario_id) if x.kind != "memorandum"]
    assert r.sources == expected
    assert [x.key for x in r.records] == list(reversed(s.provision_keys))


def test_memorandum_sources(fixture, scopes):
    unscoped = memorandum_sources(None, fixture.sources)
    assert [m.source_id for m in unscoped] == [
        f"{PROPOSAL}/memorandum/context",
        f"{PROPOSAL}/memorandum/legal_basis",
        f"{PROPOSAL}/memorandum/other_elements",
    ]
    assert memorandum_sources(scopes["legal"], fixture.sources) == unscoped
    assert memorandum_sources(scopes["fiscal"], fixture.sources) == []
    assert memorandum_sources(scopes["stakeholder"], fixture.sources) == []


def test_records_carry_utc_timestamps(scopes, corpus):
    before = dt.datetime.now(dt.UTC)
    r = _run(scopes["legal"], [ART26], corpus)
    at = r.records[0].at
    assert at.utcoffset() == dt.timedelta(0) and before <= at <= dt.datetime.now(dt.UTC)


def test_non_utc_clock_is_rejected(scopes, corpus):
    local = dt.timezone(dt.timedelta(hours=2))
    with pytest.raises(ValueError, match="UTC"):
        retrieve(
            scopes["legal"],
            [ART26],
            None,
            FINAL,
            corpus,
            corpus,
            agent="a",
            clock=lambda: dt.datetime.now(local),
        )


@pytest.mark.parametrize("view", ["actors", "full"])
def test_no_unlabelled_or_moved_date_in_any_obligation_view(corpus, view):
    """Sweep every key of every version: each shown date carries the 2024 label, and no view of
    Articles 6-27 shows a 2026-08-02 application date."""
    for v in corpus.regulation.versions:
        for key in v.by_key():
            if not corpus.obligation_records(v.version_id, key)[1]:
                continue
            src = obligations_source(corpus, v.version_id, key, view)
            for line in src.text.splitlines():
                if line.startswith("applies from:"):
                    assert line.endswith("(as adopted (2024))"), (key, line)
            article = v.by_key()[key].article
            if article.isdigit() and 6 <= int(article) <= 27 and v.version_id != PROPOSAL:
                assert "applies from: 2026-08-02" not in src.text, key


# ---------- consolidated-target runs never serve superseded obligation records ----------

DATE = re.compile(
    r"\b\d{1,2} (?:January|February|March|April|May|June|July|August|September|October|"
    r"November|December) \d{4}\b|\b\d{4}-\d{2}-\d{2}\b"
)


def _amended(corpus) -> list[str]:
    return [r.key for r in corpus.index_rows(CONSOLIDATED) if r.delta != "unchanged"]


@pytest.mark.parametrize("view", ["actors", "full"])
def test_omnibus_textless_scope_gets_no_2024_view_for_amended_keys(corpus, view):
    """2024 -> consolidated: an amended or inserted key gets no obligation view at all, not the
    2024 records (they hold superseded duties and deadlines)."""
    amended = _amended(corpus)
    with_2024_records = [k for k in amended if corpus.obligations[FINAL].get(k)]
    assert with_2024_records  # the bug needs 2024 records to exist for amended keys
    r = _run(_scope(obligations=view), amended, corpus, before=FINAL, after=CONSOLIDATED)
    assert not [s.source_id for s in r.sources if s.source_id.startswith(f"{FINAL}/obligations/")]
    assert {rec.status for rec in r.records} == {"out_of_scope"}


def test_omnibus_unamended_key_still_borrows_2024_records(corpus):
    r = _run(_scope(obligations="full"), [ART36], corpus, before=FINAL, after=CONSOLIDATED)
    assert [s.source_id for s in r.sources] == [f"{FINAL}/obligations/art_36"]
    assert SUPERSEDED_MARK not in r.sources[0].text


def test_superseded_keys_are_the_consolidated_amendments(corpus):
    assert corpus.is_superseded(FINAL, ART99)
    assert not corpus.is_superseded(FINAL, ART36)
    assert not corpus.is_superseded(PROPOSAL, ART99)  # only the adopted text was amended


@pytest.mark.parametrize("view", ["actors", "full"])
def test_no_unlabelled_date_in_any_view_of_an_amended_article(corpus, view):
    """A 2024 view of an article 2026/1744 amended (pre-Omnibus runs only) never shows timing,
    marks every record superseded, and so carries no unlabelled date."""
    dated = 0
    for key in _amended(corpus):
        if not corpus.obligation_records(FINAL, key)[1]:
            continue
        src = obligations_source(corpus, FINAL, key, view)
        assert SUPERSEDED_MARK in src.title
        for block in src.text.split("\n\n"):
            assert "\ntiming:" not in block, key
            if DATE.search(block):
                dated += 1
                assert f"status: {SUPERSEDED_MARK}" in block, (key, block[:200])
    assert dated  # the sweep saw dated records


def test_unamended_2024_view_is_not_marked(corpus):
    src = obligations_source(corpus, FINAL, ART36, "full")
    assert SUPERSEDED_MARK not in src.text and SUPERSEDED_MARK not in src.title
