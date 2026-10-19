"""The team guide and the solution notes name no IA or RSB document and no Cellar work: those
identifiers reveal which proposals back the sealed cases (plan 2026-10-12-001, U6)."""

import re

import pytest

from womm.config import REPO_ROOT

LEAKS = re.compile(r"\b(?:SEC|SWD)\s*\(\s*\d{4}\s*\)|resource/cellar/[0-9a-f]{8}-[0-9a-f-]{20,}")
DOCS = sorted((REPO_ROOT / "docs" / "guide").rglob("*.md")) + sorted(
    (REPO_ROOT / "docs" / "solutions").rglob("*.md")
)


@pytest.mark.parametrize("path", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_doc_names_no_ia_or_rsb_document(path):
    hits = LEAKS.findall(path.read_text(encoding="utf-8"))
    assert not hits, f"{path.relative_to(REPO_ROOT)} names {hits}"


def test_the_scan_catches_an_identifier():
    assert LEAKS.search("the opinion SEC(2099) 7 says") and LEAKS.search("SWD (2099)12")
    assert not LEAKS.search("pi_com:SEC(YYYY)N")
