import importlib.util
import json
import sys

import pytest
import yaml

from womm.config import REPO_ROOT
from womm.data.fixtures import Crosswalk, CrosswalkEntry, FixtureError, load_fixture
from womm.models.regulation import Regulation

spec = importlib.util.spec_from_file_location("build_corpus", REPO_ROOT / "scripts/build_corpus.py")
build_corpus = importlib.util.module_from_spec(spec)
sys.modules["build_corpus"] = build_corpus  # dataclasses need the module registered
spec.loader.exec_module(build_corpus)

CORPUS = build_corpus.DEFAULT_CORPUS_DIR
PROPOSAL, FINAL, CONSOLIDATED = (v.version_id for v in build_corpus.VERSIONS)
Unit = build_corpus.Unit

# Regulation (EU) 2026/1744, Article 1: 42 articles (inserted ones included) and 3 annexes.
AMENDED_2026 = {
    *("1", "2", "3", "4", "4a", "5", "6", "10", "11", "17", "25", "27", "28", "29"),
    *("30", "40", "42", "43", "50", "56", "57", "58", "60", "60a", "63", "64", "69"),
    *("70", "72", "75", "75a", "75b", "75c", "75d", "76", "77", "95", "96", "97", "99"),
    *("111", "113"),
}
INSERTED_2026 = {"4a", "60a", "75a", "75b", "75c", "75d"}


# --- the committed corpus -----------------------------------------------------------------------


def _version(version_id: str):
    name = {v.version_id: v.out_file for v in build_corpus.VERSIONS}[version_id]
    reg = Regulation.model_validate(json.loads((CORPUS / name).read_text(encoding="utf-8")))
    (version,) = reg.versions
    assert version.version_id == version_id
    return version


@pytest.fixture(scope="module")
def versions():
    return {vid: _version(vid) for vid in (PROPOSAL, FINAL, CONSOLIDATED)}


@pytest.fixture(scope="module")
def index():
    return json.loads((CORPUS / "index.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def obligations():
    return json.loads((CORPUS / "obligations.json").read_text(encoding="utf-8"))


def _rows(index, version_id):
    return {r["key"]: r for r in index["rows"] if r["version"] == version_id}


def test_corpus_covers_every_article_and_annex(versions):
    def count(version_id):
        provisions = versions[version_id].provisions
        annexes = [p for p in provisions if p.article.startswith("Annex ")]
        return len(provisions) - len(annexes), len(annexes)

    assert count(PROPOSAL) == (85, 9)
    assert count(FINAL) == (113, 13)
    assert count(CONSOLIDATED) == (119, 14)


def test_crosswalk_articles_carry_their_semantic_keys_in_every_version(versions):
    key = "ai_act/penalties/penalties"
    assert versions[PROPOSAL].by_key()[key].article == "71"
    assert versions[FINAL].by_key()[key].article == "99"
    assert versions[CONSOLIDATED].by_key()[key].article == "99"
    assert versions[FINAL].by_key()[key].source_id == f"{FINAL}/art_99"


def test_aligned_article_shares_one_generated_key(versions, index):
    key = "ai_act/art/26"
    assert versions[FINAL].by_key()[key].article == "26"
    assert versions[PROPOSAL].by_key()[key].article == "29"  # obligations of users
    assert _rows(index, FINAL)[key]["delta"] == "modified"
    assert _rows(index, PROPOSAL)[key]["delta"] == "modified"


def test_final_only_article_is_added_and_absent_from_the_proposal(versions, index):
    key = "ai_act/art/4"  # AI literacy, new in the adopted text
    assert key in versions[FINAL].by_key()
    assert key not in versions[PROPOSAL].by_key()
    assert _rows(index, FINAL)[key]["delta"] == "added"
    # Proposal Art 4 (amendments to Annex I) has no adopted counterpart.
    assert _rows(index, PROPOSAL)["ai_act/proposal/art/4"]["delta"] == "removed"
    assert "ai_act/proposal/art/4" not in versions[FINAL].by_key()


def test_unnumbered_subparagraphs_render_as_separate_blocks(versions):
    text = versions[FINAL].by_key()["ai_act/art/113"].text
    assert text.startswith("This Regulation shall enter into force")
    assert "\n\nIt shall apply from 2 August 2026.\n\nHowever:\n(a) " in text


def test_inserted_articles_keep_their_number_in_the_key(versions, index):
    rows = _rows(index, CONSOLIDATED)
    for number in INSERTED_2026:
        key = f"ai_act/art/{number}"
        assert versions[CONSOLIDATED].by_key()[key].article == number
        assert versions[CONSOLIDATED].by_key()[key].source_id == f"{CONSOLIDATED}/art_{number}"
        assert rows[key]["delta"] == "added"
        assert key not in versions[FINAL].by_key()
    assert rows["ai_act/annex/XIV"]["delta"] == "added"


def test_consolidated_delta_is_exactly_the_2026_amendments(index):
    rows = index["rows"]
    changed = {
        r["number"]
        for r in rows
        if r["version"] == CONSOLIDATED and r["kind"] == "article" and r["delta"] != "unchanged"
    }
    assert changed == AMENDED_2026 and len(changed) == 42
    # Footnote and spacing noise only: never amended.
    assert not changed & {"78", "100", "101", "108", "110"}
    annexes = {
        r["number"]
        for r in rows
        if r["version"] == CONSOLIDATED and r["kind"] == "annex" and r["delta"] != "unchanged"
    }
    assert annexes == {"I", "VIII", "XIV"}


def test_unamended_consolidated_units_keep_the_adopted_text(versions):
    final, consolidated = versions[FINAL].by_key(), versions[CONSOLIDATED].by_key()
    assert consolidated["ai_act/art/26"].text == final["ai_act/art/26"].text
    assert (
        consolidated["ai_act/penalties/penalties"].text != final["ai_act/penalties/penalties"].text
    )


def test_index_resolves_and_carries_no_text(versions, index):
    for row in index["rows"]:
        assert "text" not in row
        assert row["key"] in versions[row["version"]].by_key(), row
    for vid, rows in ((v, _rows(index, v)) for v in versions):
        assert set(rows) == set(versions[vid].by_key())


def test_index_is_under_15k_characters_per_version(index):
    sizes = build_corpus.index_sizes(index)
    assert set(sizes) == {PROPOSAL, FINAL, CONSOLIDATED}
    print(f"index sizes: {sizes}")
    assert all(size < 15_000 for size in sizes.values()), sizes


def test_corpus_texts_match_the_fixture_for_scenario_keys(versions):
    fixture = load_fixture()
    for version in fixture.regulation.versions:
        corpus = versions[version.version_id].by_key()
        for p in version.provisions:
            assert corpus[p.provision_key].text == p.text, p.provision_key
            assert corpus[p.provision_key].source_id == p.source_id


def test_article_51_threshold_keeps_its_exponent_in_every_dated_version(versions, obligations):
    # Art 51(2): the systemic-risk threshold is 10^25 floating point operations; the upstream
    # units and records flattened the superscript to "1025".
    threshold = "floating point operations is greater than 10^25."
    for vid in (FINAL, CONSOLIDATED):
        text = versions[vid].by_key()["ai_act/art/51"].text
        assert threshold in text, vid
        assert "greater than 1025" not in text, vid
    (record,) = [r for r in obligations[FINAL]["ai_act/art/51"] if "10^25" in r["span"]]
    assert "10^25" in record["action"]
    assert "greater than 1025" not in json.dumps(obligations)


def test_footnote_calls_stay_dropped_in_the_consolidated_text(versions):
    # Art 40(3) cites Regulation (EU) No 1025/2012 with a footnote call; no "^1" may appear.
    text = versions[CONSOLIDATED].by_key()["ai_act/art/40"].text
    assert "Regulation (EU) No 1025/2012 of the European Parliament and of the Council" in text
    exponents = {p.provision_key for p in versions[CONSOLIDATED].provisions if "^" in p.text}
    assert exponents == {"ai_act/art/51"}


def test_known_fixup_rewrites_its_phrase_once():
    fixup = build_corpus.TextFixup("51", "greater than 1025.", "greater than 10^25.")
    units = [Unit("art", "51", "Classification", "is greater than 1025."), *_units("52")]
    fixed, other = build_corpus.apply_text_fixups(units, [fixup])
    assert fixed.text == "is greater than 10^25."
    assert other is units[1]


@pytest.mark.parametrize(
    "units",
    [
        [Unit("art", "51", "Classification", "is greater than 10^25.")],  # fixed upstream
        [Unit("art", "51", "x", "greater than 1025. and greater than 1025.")],  # ambiguous
        [Unit("art", "52", "x", "is greater than 1025.")],  # article gone
    ],
)
def test_stale_text_fixup_fails_the_build(units):
    fixup = build_corpus.TextFixup("51", "greater than 1025.", "greater than 10^25.")
    with pytest.raises(FixtureError, match=r"known fixup for Article 51 .*'greater than 1025\.'"):
        build_corpus.apply_text_fixups(units, [fixup])


def test_known_fixup_rewrites_the_article_records_and_fails_when_stale():
    fixup = build_corpus.TextFixup("51", "greater than 1025.", "greater than 10^25.")
    row = _row("51", division="chV.sec1") | {"action": "is greater than 1025.", "span": "1025."}
    other = _row("40") | {"action": "Regulation (EU) No 1025/2012"}
    fixed, kept = build_corpus.apply_record_fixups([row, other], [fixup])
    assert fixed["action"] == "is greater than 10^25." and fixed["span"] == "1025."
    assert kept is other
    with pytest.raises(FixtureError, match="known fixup for Article 51 .*no Article 51 record"):
        build_corpus.apply_record_fixups([fixed, other], [fixup])


def test_corpus_has_no_memorandum_or_impact_assessment(versions):
    for name in ("proposal.json", "final.json", "consolidated.json", "index.json"):
        raw = (CORPUS / name).read_text(encoding="utf-8")
        assert "memorandum" not in raw and "SWD(2021)" not in raw, name


# --- obligations and dates (P0) -----------------------------------------------------------------


def test_no_obligation_for_articles_6_to_27_carries_a_2026_08_02_date(obligations):
    records = [r for rs in obligations[FINAL].values() for r in rs]
    in_scope = [r for r in records if r["article"] and r["article"].isdigit()]
    in_scope = [r for r in in_scope if 6 <= int(r["article"]) <= 27]
    assert in_scope
    for r in in_scope:
        assert "2026-08-02" not in json.dumps(r), r["obligation_id"]
        assert r["applies_from"] is None


def test_every_shown_date_is_labelled_as_adopted(obligations):
    records = [r for rs in obligations[FINAL].values() for r in rs]
    dated = [r for r in records if r["applies_from"]]
    assert dated
    assert all(r["date_label"] == "as adopted (2024)" for r in dated)
    assert not any(r["article"] in AMENDED_2026 for r in dated)
    assert not any(r["article"] and 102 <= int(r["article"]) <= 110 for r in dated)


def test_article_113_records_are_left_out(obligations):
    assert "ai_act/art/113" not in obligations[FINAL]
    assert not any(r["article"] == "113" for rs in obligations[FINAL].values() for r in rs)


def test_proposal_records_carry_no_date_and_consolidated_has_none(obligations):
    assert set(obligations) == {PROPOSAL, FINAL}
    assert all(r["applies_from"] is None for rs in obligations[PROPOSAL].values() for r in rs)


def test_amended_articles_get_no_obligation_view_on_the_consolidated_text(index, obligations):
    rows = _rows(index, CONSOLIDATED)
    assert rows["ai_act/penalties/penalties"]["obligations"] == {}  # Art 99, amended
    assert obligations[FINAL]["ai_act/penalties/penalties"]
    adopted = _rows(index, FINAL)["ai_act/art/26"]["obligations"]
    assert adopted and rows["ai_act/art/26"]["obligations"] == adopted  # unamended


def _row(article, *, unit=None, division="chX", date="2026-08-02", actor="provider"):
    unit = unit or f"32024R1689:art{article}.par1"
    return {
        "obligation_id": f"{unit}#1",
        "unit_id": unit,
        "article": article,
        "division": division,
        "statement_type": "duty",
        "modal": "shall",
        "primary_actor": actor,
        "actors": None,
        "addressee_text": "x",
        "condition": None,
        "action": "y",
        "timing": None,
        "public_sector": False,
        "span": "z",
        "applies_from": date,
    }


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        (_row("26", division="chIII.sec3"), "date_moved_2026"),
        (_row("6", unit="32024R1689:art6.par3.sub1", division="chIII.sec1"), "date_moved_2026"),
        (_row("6", unit="32024R1689:art6.par5", division="chIII.sec1"), None),
        (_row("105", division="chXIII"), "date_moved_2026"),
        (_row("99"), "amended_2026"),
        (_row(None, unit="32024R1689:anxIV.pt1", division="anxIV"), "annex"),
        (_row("50", division="chIV"), None),
        (_row("31", division="chIII.sec4"), None),
    ],
)
def test_withheld_reason(row, reason):
    assert build_corpus.withheld_reason(row, {"99"}) == reason


def test_build_obligations_applies_the_date_rules():
    keys = {"art26": "ai_act/art/26", "art50": "ai_act/art/50", "art113": "ai_act/art/113"}
    rows = [
        _row("26", division="chIII.sec3"),
        _row("50"),
        _row("50", date=None),
        _row("113"),
    ]
    out, stats = build_corpus.build_obligations(rows, FINAL, keys, set())
    assert "ai_act/art/113" not in out
    (art26,) = out["ai_act/art/26"]
    assert art26["applies_from"] is None and art26["date_withheld"] == "date_moved_2026"
    dated, undated = out["ai_act/art/50"]
    assert (dated["applies_from"], dated["date_label"]) == ("2026-08-02", "as adopted (2024)")
    assert undated["applies_from"] is None and undated["date_label"] is None
    assert "delta_status" not in dated and "span_start" not in dated
    assert stats.excluded_dates_article == 1 and stats.dates_labelled == 1
    assert stats.dates_withheld == {"date_moved_2026": 1} and stats.no_date == 1


def test_obligation_on_an_unknown_unit_fails_naming_it():
    with pytest.raises(FixtureError, match=r"art7\.par1#1.*art7 is not a reg2024_1689 unit"):
        build_corpus.build_obligations([_row("7")], FINAL, {}, set())


def test_proposal_dates_are_never_shown():
    out, stats = build_corpus.build_obligations(
        [_row("29", unit="52021PC0206:art29.par1")], PROPOSAL, {"art29": "ai_act/art/26"}, set()
    )
    assert out["ai_act/art/26"][0]["applies_from"] is None
    assert stats.dates_withheld == {"proposal": 1}


# --- keys -----------------------------------------------------------------------------------


def test_generated_keys_accept_inserted_articles_and_roman_annexes():
    assert build_corpus.generated_key("art", "4a") == "ai_act/art/4a"
    assert build_corpus.generated_key("art", "75b") == "ai_act/art/75b"
    assert build_corpus.generated_key("annex", "XIV") == "ai_act/annex/XIV"
    assert build_corpus.generated_key("art", "57", proposal_only=True) == ("ai_act/proposal/art/57")
    for kind, bad in (("art", "4-a"), ("art", ""), ("annex", "3")):
        with pytest.raises(FixtureError, match="not a valid"):
            build_corpus.generated_key(kind, bad)


def _units(*numbers):
    return [
        Unit("art" if n[0].isdigit() else "annex", n, f"Title {n}", f"Text {n}") for n in numbers
    ]


def test_assign_keys_pairs_renumbered_units_and_prefixes_proposal_only_ones():
    units = {
        PROPOSAL: _units("1", "71", "4", "II"),
        FINAL: _units("1", "99", "5", "I"),
        CONSOLIDATED: _units("1", "4a", "99", "5", "I", "XIV"),
    }
    pairs = {("art1", "art1"), ("art71", "art99"), ("anxII", "anxI")}
    crosswalk = Crosswalk(
        (
            CrosswalkEntry(
                provision_key="ai_act/penalties/penalties",
                articles={PROPOSAL: "71", FINAL: "99"},
            ),
        )
    )
    keys = build_corpus.assign_keys(units, pairs, crosswalk)
    assert keys[PROPOSAL] == {
        "art1": "ai_act/art/1",
        "art71": "ai_act/penalties/penalties",
        "art4": "ai_act/proposal/art/4",
        "anxII": "ai_act/annex/I",
    }
    assert keys[FINAL]["art5"] == "ai_act/art/5"
    assert keys[CONSOLIDATED]["art4a"] == "ai_act/art/4a"
    assert keys[CONSOLIDATED]["art99"] == "ai_act/penalties/penalties"
    assert keys[CONSOLIDATED]["anxXIV"] == "ai_act/annex/XIV"


def test_two_units_resolving_to_one_key_fail_naming_both():
    units = {PROPOSAL: _units("1"), FINAL: _units("5", "6")}
    crosswalk = Crosswalk((CrosswalkEntry(provision_key="ai_act/art/6", articles={FINAL: "5"}),))
    with pytest.raises(FixtureError, match=r"reg2024_1689: art5 and art6 both resolve to key"):
        build_corpus.assign_keys(units, set(), crosswalk)


def test_alignment_naming_an_unknown_unit_fails():
    units = {PROPOSAL: _units("1"), FINAL: _units("1")}
    with pytest.raises(FixtureError, match="unknown units 'art2' -> 'art1'"):
        build_corpus.assign_keys(units, {("art2", "art1")}, Crosswalk(()))


# --- consolidated version -----------------------------------------------------------------------


def test_consolidated_units_keep_adopted_text_when_only_noise_differs():
    adopted = [Unit("art", "100", "Fines", "fines of up to EUR 1500000.\n'5.For systems")]
    parsed = [Unit("art", "100", "Fines", "fines of up to EUR 1 500 000 .\n'5. For systems")]
    (unit,) = build_corpus.consolidated_units(adopted, parsed, set())
    assert unit is adopted[0]


def test_consolidated_change_without_a_marker_fails():
    adopted = [Unit("art", "9", "Risk", "shall establish")]
    with pytest.raises(FixtureError, match="Article 9 differs .* no amendment marker"):
        build_corpus.consolidated_units(adopted, [Unit("art", "9", "Risk", "may establish")], set())
    with pytest.raises(FixtureError, match="Article 9a is new but carries no amendment marker"):
        build_corpus.consolidated_units([], [Unit("art", "9a", "New", "text")], set())
    with pytest.raises(FixtureError, match="missing from the consolidated text"):
        build_corpus.consolidated_units(adopted, [], set())


def test_amended_unit_takes_the_consolidated_text():
    adopted = [Unit("art", "9", "Risk", "shall establish")]
    parsed = [Unit("art", "9", "Risk", "may establish")]
    (unit,) = build_corpus.consolidated_units(adopted, parsed, {("art", "9")})
    assert unit.text == "may establish"


def test_markers_disagreeing_with_the_amending_act_fail():
    build_corpus.check_amended((["4", "4a"], ["I"]), (["4a", "4"], ["I"]))
    with pytest.raises(FixtureError, match=r"amended articles disagree: markers only \['78'\]"):
        build_corpus.check_amended((["4", "78"], []), (["4"], []))
    with pytest.raises(FixtureError, match=r"amended annexes disagree.*2026/1744 only \['XIV'\]"):
        build_corpus.check_amended(([], []), ([], ["XIV"]))


# --- downloads ----------------------------------------------------------------------------------


def test_downloads_are_https_and_pinned():
    pins = json.loads((CORPUS / "downloads.json").read_text(encoding="utf-8"))
    urls = [
        *build_corpus.PIPELINE_UNITS.values(),
        build_corpus.PIPELINE_CONTAINERS,
        *build_corpus.PIPELINE_OBLIGATIONS.values(),
        build_corpus.CONSOLIDATED_URL,
        build_corpus.AMENDING_URL,
    ]
    assert set(pins) == set(urls)
    for url in urls:
        assert url.startswith("https://")
    for url in build_corpus.PIPELINE_OBLIGATIONS.values():
        assert f"/{sys.modules['build_fixture'].PIPELINE_COMMIT}/" in url


def test_upstream_failures_become_fixture_errors(monkeypatch):
    from womm.data.cellar import CellarError

    def http_404(url, **_):
        raise CellarError(f"{url} returned HTTP 404")

    monkeypatch.setattr(build_corpus, "fetch", http_404)
    with pytest.raises(FixtureError, match="download failed for https://x/y.*404"):
        build_corpus.fetch_upstream("https://x/y", refresh=False)


def _pinned_bodies():
    from womm.data.cellar import XHTML, cached

    pins = json.loads((CORPUS / "downloads.json").read_text(encoding="utf-8"))
    bodies = {}
    for url in pins:
        accept = XHTML if url.startswith("https://publications.europa.eu/") else "*/*"
        body = cached(url, accept=accept)
        if body is None:
            return None
        bodies[url] = body
    return bodies


def test_build_reproduces_the_committed_corpus(tmp_path, monkeypatch, capsys):
    """Offline rebuild from the download cache; skipped where the pinned bodies are not cached
    (CI), where the tests above cover the committed corpus instead."""
    bodies = _pinned_bodies()
    if bodies is None:
        pytest.skip("pinned upstream bodies are not in .cache/cellar")
    (tmp_path / "downloads.json").write_bytes((CORPUS / "downloads.json").read_bytes())

    def from_cache(url, **_):
        if url not in bodies:
            raise AssertionError(f"unexpected download {url}")
        return bodies[url]

    monkeypatch.setattr(build_corpus, "fetch", from_cache)
    assert build_corpus.main(["--out", str(tmp_path)]) == 0
    for name in (
        "proposal.json",
        "final.json",
        "consolidated.json",
        "obligations.json",
        "index.json",
        "downloads.json",
    ):
        assert (tmp_path / name).read_bytes() == (CORPUS / name).read_bytes(), name
    out = capsys.readouterr().out
    assert "42 articles, annexes ['I', 'VIII', 'XIV'] (markers agree" in out


def test_crosswalk_used_by_the_corpus_is_the_fixture_crosswalk():
    raw = yaml.safe_load((REPO_ROOT / "data/fixtures/ai_act/crosswalk.yaml").read_text())
    keys = {e["provision_key"] for e in raw["entries"]}
    final = _version(FINAL).by_key()
    assert keys <= set(final)
