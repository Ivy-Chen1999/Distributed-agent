"""Characterization: lock today's ``parse_proposal`` output on COM(2021) 206 byte for byte.

Hardening the parser for other proposals (Data Act, CRA, ...) must not move a single byte of
the AI Act parse. The committed sample is always checked; the full Cellar DOC_1 is checked too
when it is in the local download cache (``.cache/cellar``, filled by scripts/build_fixture.py).

Regenerate deliberately with ``WOMM_UPDATE_SNAPSHOTS=1 uv run pytest <this file>``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from womm.data.cellar import cached
from womm.data.parse_proposal import parse_articles, parse_document, parse_memorandum

FIXTURES = Path(__file__).parents[1] / "fixtures"
SAMPLE = FIXTURES / "com2021_206_sample.xhtml"
SNAPSHOT = FIXTURES / "snapshots" / "com2021_206_sample.parse.json"
DOC_1_URL = (
    "https://publications.europa.eu/resource/cellar/"
    "e0649735-a372-11eb-9585-01aa75ed71a1.0001.03/DOC_1"
)
# sha256 of the serialized parse of the full DOC_1 (85 articles, 27 memorandum sections).
DOC_1_PARSE_SHA256 = "9f30d62be98402ee87cbf093377ae38f40c98bc459c165bd600187419693231a"


def serialize(data: bytes) -> str:
    root = parse_document(data)
    payload = {
        "articles": [
            {
                "number": a.number,
                "title": a.title,
                "paragraphs": [[p.number, p.text] for p in a.paragraphs],
            }
            for a in parse_articles(root)
        ],
        "memorandum": [
            {"number": s.number, "heading": s.heading, "level": s.level, "blocks": s.blocks}
            for s in parse_memorandum(root)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=1) + "\n"


def test_sample_parse_is_unchanged() -> None:
    got = serialize(SAMPLE.read_bytes())
    if os.environ.get("WOMM_UPDATE_SNAPSHOTS"):
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(got, encoding="utf-8")
    assert got == SNAPSHOT.read_text(encoding="utf-8")


def test_full_doc_1_parse_is_unchanged() -> None:
    body = cached(DOC_1_URL)
    if body is None:
        pytest.skip("COM(2021) 206 DOC_1 is not in .cache/cellar")
    got = hashlib.sha256(serialize(body).encode("utf-8")).hexdigest()
    if os.environ.get("WOMM_UPDATE_SNAPSHOTS"):
        print(f"DOC_1_PARSE_SHA256 = {got!r}")
    assert got == DOC_1_PARSE_SHA256
