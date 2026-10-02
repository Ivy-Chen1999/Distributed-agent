import json
import re
from pathlib import Path

import pytest

from womm.config import REPO_ROOT
from womm.data.fixtures import CrosswalkEntry, FixtureError, load_crosswalk
from womm.data.parse_units import (
    check_crosswalk,
    parse_articles,
    parse_containers,
    parse_units,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
COMMITTED = REPO_ROOT / "data" / "fixtures" / "ai_act"


def _articles(name: str) -> dict:
    units = parse_units((FIXTURES / name).read_bytes(), name)
    return {a.number: a for a in parse_articles(units)}


@pytest.fixture(scope="module")
def proposal():
    return _articles("units_proposal_sample.jsonl")


@pytest.fixture(scope="module")
def final():
    return _articles("units_final_sample.jsonl")


@pytest.fixture(scope="module")
def committed():
    out = {}
    for name in ("proposal.json", "final.json"):
        for v in json.loads((COMMITTED / name).read_text(encoding="utf-8"))["versions"]:
            for p in v["provisions"]:
                out[(v["version_id"], p["article"])] = p["text"]
    return out


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def test_plain_article_is_byte_identical(proposal, committed):
    assert proposal["8"].text == committed[("com2021_206", "8")]
    assert proposal["8"].title == "Compliance with the requirements"


def test_article_level_points_form_one_block(proposal, committed):
    text = proposal["16"].text
    assert text == committed[("com2021_206", "16")]
    assert "\n\n" not in text
    assert text.startswith("Providers of high-risk AI systems shall:\n(a) ensure")


def test_points_are_restored_at_the_gap(proposal, committed):
    text = proposal["9"].text
    assert "[…]" not in text
    para4 = text.split("\n\n4. ", 1)[1].split("\n\n5. ", 1)[0]
    assert para4.index("\n(a) elimination") < para4.index("In eliminating or reducing risks")
    assert _ws(text) == _ws(committed[("com2021_206", "9")])


def test_two_gaps_insert_points_once(proposal, committed):
    text = proposal["43"].text
    assert "[…]" not in text
    assert text.count("(a) the conformity assessment procedure based on internal control") == 1
    first = text.split("\n\n2. ", 1)[0]
    assert first.index("following procedures:\n(a)") < first.index("\nWhere, in demonstrating")
    assert _ws(text) == _ws(committed[("com2021_206", "43")])


def test_final_subparagraphs_get_their_own_line(final):
    text = final["9"].text
    assert "[…]" not in text
    # Art 9(5) is three unnumbered subparagraphs, the second introducing points (a)-(c).
    para5 = text.split("\n\n5. ", 1)[1].split("\n\n6. ", 1)[0]
    lines = para5.split("\n")
    assert lines[0].startswith("The risk management measures referred to in paragraph 2")
    assert lines[1].startswith("In identifying the most appropriate risk management measures")
    assert lines[2].startswith("(a) ")
    assert lines[-1].startswith("With a view to eliminating or reducing risks")


def test_final_penalties_match_up_to_digit_grouping(final, committed):
    # The Formex source writes "35000000"; our XHTML parse kept "35 000 000".
    ours = re.sub(r"(\d) (?=\d{3}\b)", r"\1", committed[("reg2024_1689", "99")])
    assert _ws(final["99"].text) == _ws(ours)
    assert final["99"].title == "Penalties"


def test_duplicate_unit_id_fails():
    line = '{"unit_id":"X:art1","parent_id":null,"seq":1,"type":"article","num":"Article 1",'
    line += '"label":"1","heading":"h","text":"t"}\n'
    with pytest.raises(FixtureError, match="duplicate unit_id.*X:art1"):
        parse_units((line + line).encode(), "dup.jsonl")


def test_unknown_parent_fails():
    line = '{"unit_id":"X:art1.par1","parent_id":"X:art1","seq":1,"type":"paragraph",'
    line += '"num":"1.","label":"1","heading":null,"text":"t"}\n'
    with pytest.raises(FixtureError, match="unknown parent_id"):
        parse_units(line.encode(), "orphan.jsonl")


def test_malformed_line_names_the_file_and_line():
    with pytest.raises(FixtureError, match="bad.jsonl line 2"):
        parse_units(
            b'{"unit_id":"X:art1","parent_id":null,"seq":1,"type":"article",'
            b'"num":null,"label":"1","heading":null,"text":""}\n{not json\n',
            "bad.jsonl",
        )


def test_committed_crosswalk_agrees_with_alignment():
    pairs = parse_containers((FIXTURES / "containers_sample.csv").read_bytes())
    crosswalk = load_crosswalk(COMMITTED / "crosswalk.yaml")
    check_crosswalk(crosswalk.entries, pairs, "com2021_206", "reg2024_1689")


def test_disagreeing_crosswalk_fails():
    pairs = parse_containers((FIXTURES / "containers_sample.csv").read_bytes())
    wrong = CrosswalkEntry(
        provision_key="ai_act/penalties/penalties",
        articles={"com2021_206": "71", "reg2024_1689": "98"},
    )
    with pytest.raises(FixtureError, match="penalties.*Art 71.*Art 98"):
        check_crosswalk([wrong], pairs, "com2021_206", "reg2024_1689")


def test_one_sided_crosswalk_entry_is_skipped():
    only_new = CrosswalkEntry(provision_key="ai_act/x", articles={"reg2024_1689": "4"})
    check_crosswalk([only_new], set(), "com2021_206", "reg2024_1689")


def test_containers_without_columns_fail():
    with pytest.raises(FixtureError, match="malformed container alignment"):
        parse_containers(b'"a","b"\n"1","2"\n')
