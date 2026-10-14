"""The owner's review page: its verdict follows the same rules as the promotion gate."""

import json
import re

import pytest

from womm.eval import judge_calibration as jc
from womm.eval import review_page
from womm.evolve import promotion as pm


def _result(**over):
    result = {
        "judge_version": "jv_test0000001",
        "pairs_sampled": 30,
        "pairs_resolved": 28,
        "excluded": 2,
        "annotators": {"ann": {"blank": 0}, "bob": {"blank": 0}},
        "agreement_raw": 0.9,
        "agreement_raw_ci95": jc.wilson_interval(25, 28),
        "agreement_natural": 0.92,
        "agreement": 0.9,
        "kappa": 0.7,
        "inter_annotator_agreement": 0.9,
        "confusion": {"judge_covered_human_not": 1, "judge_missed_human_covered": 2},
        "disagreements": [],
        "excluded_pairs": [],
    }
    return result | over


def _page(tmp_path, result):
    key, answers = {"pairs": {}, "sample": "s1"}, {"ann": {}, "bob": {}}
    path = review_page.calibration(tmp_path, key, result, answers, {}, minimum=pm.CALIBRATION_MIN)
    html = path.read_text()
    assert "__DATA__" not in html
    blob = re.search(r"const DATA = (\{.*?\});\n", html, re.S)
    return json.loads(blob.group(1).replace("<\\/", "</"))


@pytest.mark.parametrize(
    ("over", "tag", "says"),
    [
        ({}, "Passed", "trusted"),
        ({"inter_annotator_agreement": 0.7}, "Does not count", "below 80%"),
        ({"pairs_resolved": 20, "excluded": 10}, "Does not count", "only 20 pairs"),
        ({"agreement": 0.8, "agreement_raw": 0.8}, "Failed", "disagrees with people"),
        ({"annotators": {"ann": {"blank": 3}, "bob": {"blank": 0}}}, "Not finished", "no answer"),
    ],
)
def test_calibration_verdict_follows_the_gate(tmp_path, over, tag, says):
    verdict = _page(tmp_path, _result(**over))["verdict"]
    assert verdict["tag"] == tag
    assert says in verdict["headline"] + verdict["meaning"]


def test_the_page_shows_the_agreement_interval(tmp_path):
    lo, hi = jc.wilson_interval(25, 28)
    text = json.dumps(_page(tmp_path, _result()))
    assert f"{lo:.1%} to {hi:.1%}" in text and "context only" in text


def test_wilson_interval():
    assert jc.wilson_interval(0, 0) is None
    lo, hi = jc.wilson_interval(25, 28)
    assert 0.72 < lo < 0.74 and 0.96 < hi < 0.97
    assert jc.wilson_interval(10, 10)[1] == 1.0


def test_a_fidelity_pass_says_what_it_proves_and_lists_where_the_key_differs(tmp_path):
    from womm.eval import reference_check as rc

    items = {f"r{n}": {"case_id": "c", "fixture": "f", "item_id": f"i{n}", "stratum":
                       "judges_accepted", "tool_kept": True, "tool_reason": "page question: ok",
                       "annotator": "ann", "golden_kept": True, "golden_reason": ""}
             for n in range(1, 31)}  # fmt: skip
    items["r1"]["golden_kept"] = False
    key = {
        "sample": "2026-10-14-x",
        "annotators": ["ann"],
        "items": items,
        "comparison": "fidelity",
    }
    labels = {i: "keep" for i in items}
    result = rc.score(key, labels)
    path = review_page.reference_check(tmp_path, key, result, labels, {}, limit=rc.MAX_ERROR)
    data = json.loads(re.search(r"const DATA = (\{.*?\});\n", path.read_text(), re.S)
                      .group(1).replace("<\\/", "</"))  # fmt: skip
    assert data["verdict"]["headline"] == "People and the AI read the sources the same way"
    assert "does not prove every point in the answer key" in data["verdict"]["meaning"]
    assert [c["id"] for c in data["cards"] if c["section"] == "golden"] == ["r1"]
    assert "does not prove the key" in rc.format_result(result, key)
