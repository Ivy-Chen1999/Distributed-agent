"""womm.eval.key_sources: each fixture's IA, board opinion (or its stand-in) and law, with
digests and citable passages (plan 2026-10-16-001, F1)."""

import json

import pytest

from womm.eval import key_sources as ks


def _fixture(
    tmp_path, opinion: str | None = "Board text line one\nwraps here.\n\n(2) Second point.\n"
):
    ia = tmp_path / "ia" / "fx"
    ia.mkdir(parents=True)
    (ia / "ia_full.txt").write_text("First paragraph.\n\nSecond paragraph about costs.\n")
    if opinion is not None:
        (ia / "rsb_opinion.txt").write_text(opinion)
    (ia / "rsb.txt").write_text("Procedural annex.\n")
    law = tmp_path / "fixtures" / "fx"
    law.mkdir(parents=True)
    text = "1. Courts may order disclosure.\n\n3. Trade secrets are protected."
    provisions = [{"provision_key": "fx/proposal/art/8", "article": "8", "text": text}]
    (law / "proposal.json").write_text(json.dumps({"versions": [{"provisions": provisions}]}))
    return ks.load_sources("fx", ia_root=tmp_path / "ia", fixtures_root=tmp_path / "fixtures")


def test_passages_are_cited_by_line_and_article(tmp_path):
    s = _fixture(tmp_path)
    where = {p.where: p.text for p in s.passages}
    assert where["ia_full.txt:3"] == "Second paragraph about costs."
    assert where["rsb_opinion.txt:1"] == "Board text line one wraps here."
    assert where["rsb_opinion.txt:4"] == "(2) Second point."
    assert where["proposal Art 8(3)"] == "3. Trade secrets are protected."
    assert set(s.digests) == {"ia", "rsb_opinion", "law"} and not s.opinion_missing


def test_a_missing_opinion_falls_back_to_the_annex_and_says_so(tmp_path):
    s = _fixture(tmp_path, opinion=None)
    assert s.opinion_missing and "rsb_annex" in s.digests


def test_long_paragraphs_are_cut_after_a_sentence():
    text = ("A sentence of moderate length. " * 120).strip()
    pieces = ks._cut(text)
    assert all(len(p) <= ks.MAX_PASSAGE for p in pieces) and " ".join(pieces) == text
    assert all(p.endswith(".") for p in pieces)


def test_no_ia_text_is_refused(tmp_path):
    (tmp_path / "ia" / "fx").mkdir(parents=True)
    with pytest.raises(ks.KeySourceError, match="no cached IA text"):
        ks.load_sources("fx", ia_root=tmp_path / "ia", fixtures_root=tmp_path)
