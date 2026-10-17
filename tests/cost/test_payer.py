"""Payer resolution for cost records: the colleague's field, then our rule table, else unknown."""

import re
from collections import Counter

import pytest

from womm.cost.payer import (
    PAYER_RULES,
    SECTOR,
    payer_annex,
    resolve_payer,
    sector_for,
)
from womm.data.corpus import Corpus, load_corpus

PROPOSAL, FINAL = "com2021_206", "reg2024_1689"


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return load_corpus()


def _records(corpus, version, article=None, annex=None):
    out = []
    for recs in corpus.obligations[version].values():
        for r in recs:
            by_article = article is not None and r.article == article
            if by_article or (annex is not None and payer_annex(r) == annex):
                out.append(r)
    return out


def test_unspecified_proposal_article_10_duty_resolves_by_rule_table(corpus):
    rec = next(r for r in _records(corpus, PROPOSAL, "10") if r.actor_unspecified)
    got = resolve_payer(rec, PROPOSAL)
    assert (got.payer, got.basis) == ("provider", "rule_table")
    assert "Art 16(a)" in got.legal_basis


def test_final_article_26_deployer_resolves_by_the_record_field(corpus):
    rec = next(r for r in _records(corpus, FINAL, "26") if r.primary_actor == "deployer")
    got = resolve_payer(rec, FINAL)
    assert (got.payer, got.basis, got.legal_basis) == ("deployer", "rule_field", None)


def test_unspecified_article_5_record_matches_no_rule(corpus):
    rec = next(r for r in _records(corpus, PROPOSAL, "5") if r.actor_unspecified)
    got = resolve_payer(rec, PROPOSAL)
    assert (got.payer, got.basis) == (None, "unknown")


def test_rule_table_covers_all_65_unspecified_proposal_duties_in_articles_8_to_15(corpus):
    duties = [
        r
        for a in map(str, range(8, 16))
        for r in _records(corpus, PROPOSAL, a)
        if r.actor_unspecified and r.statement_type in ("duty", "prohibition")
    ]
    assert len(duties) == 65  # measured 2026-10-07
    assert {resolve_payer(r, PROPOSAL).basis for r in duties} == {"rule_table"}


def test_rule_table_rows_name_a_version_a_payer_and_a_legal_basis():
    for rule in PAYER_RULES:
        assert rule.version_id in (PROPOSAL, FINAL)
        assert rule.payer in SECTOR
        assert re.search(r"Art \d+", rule.legal_basis)
        assert rule.articles or rule.annexes


def test_annex_records_resolve_by_annex(corpus):
    rec = next(r for r in _records(corpus, FINAL, annex="IV") if r.actor_unspecified)
    assert resolve_payer(rec, FINAL).payer == "provider"


def test_public_sector_flag_makes_a_deployer_public():
    assert sector_for("deployer", False) == "private"
    assert sector_for("deployer", True) == "public"
    assert sector_for("provider", False) == "private"
    assert sector_for("commission", False) == "public"
    assert sector_for(None, False) is None


def test_every_primary_actor_in_the_corpus_is_in_the_sector_map(corpus):
    """Fails on a new upstream actor category, so one cannot slip through unclassified."""
    actors = Counter(
        r.primary_actor
        for by in corpus.obligations.values()
        for recs in by.values()
        for r in recs
        if not r.actor_unspecified
    )
    assert set(actors) <= set(SECTOR), sorted(set(actors) - set(SECTOR))


def test_unknown_actor_has_no_sector():
    with pytest.raises(KeyError, match="martian"):
        sector_for("martian", False)


def test_payer_basis_coverage_is_printed(corpus, capsys):
    """The deterministic half of every duty in both versions, without an LLM call."""
    for version in (PROPOSAL, FINAL):
        bases = Counter(
            resolve_payer(r, version).basis
            for recs in corpus.obligations[version].values()
            for r in recs
            if r.statement_type in ("duty", "prohibition")
        )
        print(version, dict(sorted(bases.items())))
        assert bases["rule_field"] and bases["rule_table"] and bases["unknown"]
    assert "rule_table" in capsys.readouterr().out
