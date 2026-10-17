"""Data checks of the acceptance cases from the failed reference check (evals/regression/): well
formed, and where a case quotes its source in double quotes, the first words of that quote are on
the first line it cites (when the texts are cached here). This does not run any checker or
compare a verdict with ``expected``; that acceptance is separate (plan 2026-10-16-001)."""

import re
from pathlib import Path

import pytest
import yaml

from womm.config import REPO_ROOT

CASES = REPO_ROOT / "evals/regression/key_fidelity_2026-10-17.yaml"
KINDS = {
    "window",
    "law_context",
    "over_inference",
    "group_mismatch",
    "amount_mismatch",
    "modality",
    "same_group_kind",
}
IA_ROOTS = [REPO_ROOT / ".cache/ia", REPO_ROOT.parent.parent.parent / ".cache/ia"]


def _cases() -> list[dict]:
    return yaml.safe_load(CASES.read_text(encoding="utf-8"))["cases"]


def test_every_case_is_well_formed():
    cases = _cases()
    assert len(cases) == 31 and len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["kind"] in KINDS and c["expected"] in {"supported", "not_supported"}
        if c["kind"] in ("window", "law_context"):
            assert c["expected"] == "supported"
        elif c["kind"] in ("over_inference", "group_mismatch", "amount_mismatch"):
            assert c["expected"] == "not_supported"
        assert c["claim"].strip() and c["why"].strip() and re.search(r":\d", c["source"])


def _root() -> Path | None:
    return next((r for r in IA_ROOTS if (r / "dsa/ia_full.txt").exists()), None)


@pytest.mark.skipif(_root() is None, reason="the IA texts are not cached on this machine")
def test_each_quoted_passage_is_on_its_line():
    root = _root()
    checked = 0
    for c in _cases():
        quote = re.search(r'"(.{12,}?)"', c["why"])
        first = re.match(r"([a-z_]+/[a-z_]+\.txt):(\d+)", c["source"])
        if not (quote and first):
            continue
        lines = (root / first.group(1)).read_text(encoding="utf-8").splitlines()
        line = lines[int(first.group(2)) - 1]
        words = quote.group(1).replace("...", " ").split()[:4]
        assert _norm(" ".join(words)) in _norm(line), c["id"]
        checked += 1
    assert checked >= 10  # the quotes are actually checked, not skipped


def _norm(text: str) -> str:
    return " ".join(text.replace("\u2019", "'").replace("\u2018", "'").split()).lower()


def test_every_case_freezes_its_input():
    for c in _cases():
        inp = c["input"]
        assert inp["point"] and inp["anchor"] and inp["fixture"], c["id"]
        assert set(inp["sources"]) >= {"ia"} and all(len(d) == 64 for d in inp["sources"].values())


@pytest.mark.skipif(_root() is None, reason="the IA texts are not cached on this machine")
def test_the_frozen_sources_are_the_texts_on_this_machine():
    """A changed source text invalidates the acceptance run: freeze and run it again."""
    from womm.eval.key_sources import load_sources

    for fixture in sorted({c["input"]["fixture"] for c in _cases()}):
        now = load_sources(fixture, ia_root=_root()).digests
        for c in (c for c in _cases() if c["input"]["fixture"] == fixture):
            assert c["input"]["sources"] == now, c["id"]
