"""The committed corpus loads, resolves, and applies the obligation-view rules per version."""

import json
import shutil

import pytest

from womm.data.corpus import DEFAULT_CORPUS_DIR, Corpus, load_corpus
from womm.data.fixtures import FixtureError

PROPOSAL, FINAL, CONSOLIDATED = "com2021_206", "reg2024_1689", "reg2024_1689_c20260727"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return load_corpus()


def test_loads_three_versions(corpus):
    assert [v.version_id for v in corpus.regulation.versions] == [PROPOSAL, FINAL, CONSOLIDATED]
    assert corpus.version(CONSOLIDATED).status == "consolidated"
    with pytest.raises(FixtureError, match="unknown corpus version"):
        corpus.version("nope")


def test_sources_cover_every_unit_with_titles(corpus):
    for v in corpus.regulation.versions:
        for p in v.provisions:
            assert corpus.sources[p.source_id].text == p.text
    art99 = corpus.sources[f"{FINAL}/art_99"]
    assert art99.kind == "provision"
    assert art99.title == "Regulation (EU) 2024/1689, Article 99: Penalties"
    annex = corpus.sources[f"{FINAL}/annex_III"]
    assert annex.kind == "annex"
    assert annex.title.startswith("Regulation (EU) 2024/1689, Annex III: High-risk")


def test_inserted_articles_use_string_keys(corpus):
    by_key = corpus.version(CONSOLIDATED).by_key()
    assert "ai_act/art/4a" in by_key
    assert corpus.row(CONSOLIDATED, "ai_act/art/4a").delta == "added"
    assert "ai_act/art/4a" not in corpus.version(FINAL).by_key()


def test_index_rows_carry_no_text(corpus):
    assert all("text" not in r.model_dump() for r in corpus.index.rows)
    assert {r.version for r in corpus.index_rows(FINAL)} == {FINAL}


def test_proposal_and_adopted_use_their_own_records(corpus):
    src, records = corpus.obligation_records(PROPOSAL, "ai_act/penalties/penalties")
    assert src == PROPOSAL and records
    src, records = corpus.obligation_records(FINAL, "ai_act/penalties/penalties")
    assert src == FINAL and records


def test_consolidated_borrows_2024_records_for_unamended_units_only(corpus):
    assert corpus.row(CONSOLIDATED, "ai_act/art/36").delta == "unchanged"
    src, records = corpus.obligation_records(CONSOLIDATED, "ai_act/art/36")
    assert src == FINAL
    assert records == corpus.obligations[FINAL]["ai_act/art/36"]
    # Article 99 was amended by 2026/1744: no obligation view at all.
    assert corpus.row(CONSOLIDATED, "ai_act/penalties/penalties").delta == "modified"
    assert corpus.obligation_records(CONSOLIDATED, "ai_act/penalties/penalties")[1] == []
    # An inserted article has no 2024 records.
    assert corpus.obligation_records(CONSOLIDATED, "ai_act/art/4a")[1] == []


def test_no_obligation_records_for_the_consolidated_text_itself(corpus):
    assert CONSOLIDATED not in corpus.obligations


def test_article_113_records_are_left_out(corpus):
    assert not any(
        r.article == "113" for by in corpus.obligations.values() for rs in by.values() for r in rs
    )


def test_every_date_is_labelled(corpus):
    for by in corpus.obligations.values():
        for records in by.values():
            for r in records:
                assert (r.applies_from is None) == (r.date_label is None)


def test_missing_file_is_a_fixture_error(tmp_path):
    shutil.copytree(DEFAULT_CORPUS_DIR, tmp_path / "c")
    (tmp_path / "c" / "obligations.json").unlink()
    with pytest.raises(FixtureError, match="obligations.json"):
        load_corpus(tmp_path / "c")


def test_unresolved_index_key_is_a_fixture_error(tmp_path):
    shutil.copytree(DEFAULT_CORPUS_DIR, tmp_path / "c")
    path = tmp_path / "c" / "index.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["rows"][0]["key"] = "ai_act/art/999"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FixtureError, match="ai_act/art/999"):
        load_corpus(tmp_path / "c")


def test_records_carry_the_unit_delta(corpus):
    final = [r for recs in corpus.obligations[FINAL].values() for r in recs]
    assert {r.unit_delta for r in final} == {
        "added", "modified", "split_merge", "minor_edit", "unchanged",
    }  # fmt: skip
    assert all(r.unit_delta is None for recs in corpus.obligations[PROPOSAL].values() for r in recs)


def test_unknown_unit_delta_is_a_fixture_error(tmp_path):
    shutil.copytree(DEFAULT_CORPUS_DIR, tmp_path / "c")
    path = tmp_path / "c" / "obligations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[FINAL]["ai_act/art/36"][0]["unit_delta"] = "rewritten"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FixtureError, match="rewritten"):
        load_corpus(tmp_path / "c")


def test_malformed_record_is_a_fixture_error(tmp_path):
    shutil.copytree(DEFAULT_CORPUS_DIR, tmp_path / "c")
    path = tmp_path / "c" / "obligations.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data[FINAL]["ai_act/art/36"][0]["surprise"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(FixtureError, match="does not match the models"):
        load_corpus(tmp_path / "c")
