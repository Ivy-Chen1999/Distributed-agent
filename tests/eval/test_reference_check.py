"""The one-time reference-answer check: population, blind sheets and the error-rate gate."""

from __future__ import annotations

import importlib.util
import json
import re
import sys

import pytest

from womm.config import REPO_ROOT
from womm.eval import reference_check as rc
from womm.eval.drafting import GoldenDraft, write_draft

from .draft_factory import decide, draft_dict, seal, to_draft
from .test_tiebreak import run, verdict

SCRIPT = REPO_ROOT / "scripts/reference_check.py"
spec = importlib.util.spec_from_file_location("reference_check_script", SCRIPT)
script = importlib.util.module_from_spec(spec)
sys.modules["reference_check_script"] = script
spec.loader.exec_module(script)


def tiebroken(case_id: str = "case_90_widget_switching") -> GoldenDraft:
    data = draft_dict(case_id=case_id)
    data["expected_impacts"][3]["flags"] = ["anchor_not_found"]
    data["expected_impacts"][3]["review"]["decision"] = "pending"
    draft, _ = run(
        to_draft(seal(data)),
        [verdict(f"{data['expected_impacts'][4]['expected_id']}"),
         verdict(data["possibly_missing"][0]["candidate_id"], "drop", reason="repeats e01")],
    )  # fmt: skip
    return draft


def strata(draft: GoldenDraft) -> dict[str, str | None]:
    return {i.item_id: rc.stratum(i) for i in draft.items()}


def test_population_holds_only_tool_judgements():
    draft = tiebroken()
    s = strata(draft)
    assert s["c90_e05"] == "tiebreak_kept"
    assert s["c90_m01"] == "tiebreak_dropped"  # ruled drop
    assert s["c90_m02"] == "tiebreak_dropped"  # no ruling: unsure, dropped by the LLM
    assert s["c90_e04"] is None  # dropped by the deterministic anchor check
    assert s["c90_e01"] == "judges_accepted"
    human = GoldenDraft.model_validate(decide(draft.model_dump(mode="json"), "c90_e01", "verified"))
    assert strata(human)["c90_e01"] is None
    kept = {i.item_id: i.tool_kept for i in rc.population([draft])}
    assert kept["c90_e05"] and kept["c90_e01"] and not kept["c90_m01"]


def test_holdout_drafts_are_refused():
    with pytest.raises(rc.ReferenceCheckError, match="holdout"):
        rc.population([to_draft(draft_dict("holdout"))])


def test_the_sample_spreads_over_strata_and_fills_in_when_one_runs_short():
    pop = rc.population([tiebroken("case_90_a"), tiebroken("case_91_b")])
    by = {s: sum(i.stratum == s for i in pop) for s in rc.STRATA}
    assert by["tiebreak_kept"] == 2  # one per case
    sample = rc.stratified_sample(pop, 9, seed=3)
    got = {s: sum(i.stratum == s for i in sample) for s in rc.STRATA}
    assert got["tiebreak_kept"] == 2 and sum(got.values()) == 9
    assert got["tiebreak_dropped"] >= 3 and got["judges_accepted"] >= 3
    assert {i.case_id for i in sample if i.stratum == "tiebreak_kept"} == {"case_90_a", "case_91_b"}
    assert sample == rc.stratified_sample(pop, 9, seed=3)
    assert len(rc.stratified_sample(pop, 500, seed=3)) == len(pop)


LEAKS = ("tool_kept", "llm_kept", "llm_dropped", "auto_accepted", "repeats e01", "tiebreak",
         "judge", "why_missing")  # fmt: skip


def page_data(path) -> dict:
    return json.loads(re.search(r"const DATA = (.*);\n", path.read_text()).group(1))


def answer_file(path, key: dict, name: str, labels: dict, notes: dict | None = None) -> None:
    """An answer file in the format the page's JavaScript writes (JSON-quoted notes)."""
    rows = [f"sample: {json.dumps(key['sample'])}", f"annotator: {json.dumps(name)}", "answers:"]
    for iid, label in sorted(labels.items()):
        rows += [f"- item: {iid}", f"  label: {label or ''}",
                 f"  note: {json.dumps((notes or {}).get(iid, ''))}"]  # fmt: skip
    path.write_text("\n".join(rows) + "\n")


def test_the_pages_are_blind_and_the_key_is_private(tmp_path):
    out = tmp_path / "check"
    drafts = [tiebroken("case_90_a"), tiebroken("case_91_b")]
    res = rc.write_sample(out, drafts, n=6, seed=1, annotators=["ana", "bo"], ia_root=tmp_path,
                          send_to="Yuxin on Slack")  # fmt: skip
    assert res["items"] == 6 and res["missing_ia"] == ["data_act"]
    page = (out / "label_ana.html").read_text()
    assert not re.search(r"__[A-Z_]+__", page) and "Yuxin on Slack" in page
    data = page_data(out / "label_ana.html")
    assert data["annotator"] == "ana" and len(data["items"]) == 6
    for leak in LEAKS:
        assert leak not in json.dumps(data)
    key = json.loads((out / rc.KEY_FILE).read_text())
    assert data["sample"] == key["sample"] and set(key["items"]) == {f"r{i}" for i in range(1, 7)}
    assert abs(sum(key["population"]["shares"].values()) - 1) < 1e-9
    assert [i["id"] for i in data["items"]] != [
        i["id"] for i in page_data(out / "label_bo.html")["items"]
    ]  # each annotator has their own order
    with pytest.raises(rc.ReferenceCheckError, match="exists"):
        rc.write_sample(out, drafts, n=6, seed=1, annotators=["ana"])
    with pytest.raises(rc.ReferenceCheckError, match="distinct"):
        rc.write_sample(tmp_path / "x", drafts, n=6, seed=1, annotators=["ana", "ana"])


def test_a_sampled_item_never_shows_among_the_others():
    """Otherwise a kept sampled item would appear on its sibling's page, a dropped one not."""
    draft = tiebroken("case_90_a")
    kept = [i.item_id for i in draft.items() if i.review.decision in rc.KEPT_DECISIONS]
    everything = rc._others(draft, set())
    assert len(everything) == len(kept)
    assert len(rc._others(draft, {kept[0], "c90_m01"})) == len(kept) - 1


def _key(rows: list[tuple[str, bool]], annotators=("ana", "bo")) -> dict:
    items = {f"r{n}": {"case_id": "c", "fixture": "f", "item_id": f"i{n}", "stratum": s,
                       "tool_kept": k} for n, (s, k) in enumerate(rows, 1)}  # fmt: skip
    shares = {s: sum(r[0] == s for r in rows) / len(rows) for s in rc.STRATA}
    return {"sample": "2026-10-09-test", "annotators": list(annotators), "items": items,
            "population": {"items": len(rows), "shares": shares}}  # fmt: skip


def test_score_counts_the_tool_wrong_where_the_humans_disagree_with_it():
    key = _key([("judges_accepted", True)] * 10 + [("tiebreak_dropped", False)] * 10)
    yes = {f"r{n}": "keep" if n <= 10 else "drop" for n in range(1, 21)}
    one_wrong = yes | {"r11": "keep"}
    res = rc.score(key, {"ana": one_wrong, "bo": one_wrong})
    assert res["error_raw"] == pytest.approx(0.05) and res["robust"]
    assert res["wrong"] == ["r11"] and res["error_by_stratum"]["tiebreak_dropped"] == 0.1
    assert 0.05 < res["upper_bound_95"] < 0.25
    three_wrong = yes | {"r11": "keep", "r12": "keep", "r1": "drop"}
    res = rc.score(key, {"ana": three_wrong, "bo": three_wrong})
    assert res["error"] == pytest.approx(0.15) and not res["robust"]


def test_no_error_in_20_items_still_allows_about_12_percent():
    assert rc.upper_bound(0, 20) == pytest.approx(0.119, abs=0.002)
    assert rc.upper_bound(0, 30) < 0.1 < rc.upper_bound(0, 20)


def test_a_split_or_an_unsure_leaves_the_item_unsettled_and_counted_as_an_error():
    key = _key([("judges_accepted", True)] * 5 + [("tiebreak_dropped", False)] * 5)
    ana = {f"r{n}": "keep" if n <= 5 else "drop" for n in range(1, 11)}
    bo = ana | {"r1": "drop", "r6": "unsure"}  # a split, and drop + unsure
    res = rc.score(key, {"ana": ana, "bo": bo})
    assert res["split"] == ["r1", "r6"] and res["unsettled"] == ["r1", "r6"]
    assert res["wrong"] == [] and res["error_raw"] == pytest.approx(0.2) and not res["robust"]
    assert res["inter_annotator_agreement"] == pytest.approx(8 / 9)
    res = rc.score(key, {"ana": ana, "bo": bo}, {"r1": "keep", "r6": "drop"})
    assert res["reconciled"] == 2 and res["unsettled"] == [] and res["robust"]
    res = rc.score(key, {"ana": ana, "bo": bo}, {"r1": "drop", "r6": "keep"})
    assert res["wrong"] == ["r1", "r6"] and not res["robust"]


def test_kappa_corrects_for_chance_and_blanks_block_the_verdict():
    key = _key([("judges_accepted", True)] * 5 + [("tiebreak_dropped", False)] * 5)
    ana = {f"r{n}": "keep" if n <= 5 else "drop" for n in range(1, 11)}
    bo = ana | {"r1": "drop", "r6": "keep"}
    res = rc.score(key, {"ana": ana, "bo": bo})
    assert res["inter_annotator_agreement"] == pytest.approx(0.8)
    assert res["cohen_kappa"] == pytest.approx(0.6)
    res = rc.score(key, {"ana": ana, "bo": ana | {"r2": None}}, {"r2": "keep"})
    assert res["blank"] == {"ana": 0, "bo": 1} and not res["robust"]


def test_three_annotators_need_two_matching_votes():
    key = _key([("tiebreak_kept", True)] * 3, annotators=("a", "b", "c"))
    votes = {"a": {"r1": "keep", "r2": "keep", "r3": "keep"},
             "b": {"r1": "keep", "r2": "unsure", "r3": "drop"},
             "c": {"r1": "drop", "r2": "unsure", "r3": "unsure"}}  # fmt: skip
    res = rc.score(key, votes)
    assert res["unsettled"] == ["r2", "r3"] and res["settled"] == 1


def test_the_natural_rate_weights_the_strata_by_population():
    key = _key([("judges_accepted", True)] * 2 + [("tiebreak_kept", True)] * 2)
    key["population"]["shares"] = {"judges_accepted": 0.9, "tiebreak_kept": 0.1,
                                   "tiebreak_dropped": 0.0}  # fmt: skip
    labels = {"r1": "keep", "r2": "keep", "r3": "drop", "r4": "keep"}
    res = rc.score(key, {"ana": labels})
    assert res["error_raw"] == 0.25 and res["error_natural"] == pytest.approx(0.05)
    assert res["error"] == 0.25  # the higher of the two


def test_answers_must_come_from_the_samples_annotators_and_the_same_sample(tmp_path):
    key = _key([("tiebreak_kept", True)] * 2)
    labels = {"r1": "keep", "r2": "keep"}
    answer_file(tmp_path / "answers_ana.yaml", key, "ana", labels)
    with pytest.raises(rc.ReferenceCheckError, match="no answers yet from bo"):
        rc.read_answers(tmp_path, key)
    answer_file(tmp_path / "answers_bo.yaml", key, "bo", {"r1": "keep"})
    answers, _ = rc.read_answers(tmp_path, key)
    assert answers["bo"] == {"r1": "keep", "r2": None}  # a missing row is a blank
    answer_file(tmp_path / "answers_test.yaml", key, "test", labels)
    with pytest.raises(rc.ReferenceCheckError, match="test, who is not an annotator"):
        rc.read_answers(tmp_path, key)
    (tmp_path / "answers_test.yaml").unlink()
    answer_file(tmp_path / rc.RECONCILED_FILE, key | {"sample": "other"}, "together", labels)
    with pytest.raises(rc.ReferenceCheckError, match="belongs to sample other"):
        rc.read_reconciled(tmp_path, key)


def test_the_script_samples_scores_and_writes_a_blind_reconcile_page(tmp_path, capsys):
    drafts_dir = tmp_path / "drafts"
    paths = [write_draft(tiebroken(c), drafts_dir) for c in ("case_90_a", "case_91_b")]
    out = tmp_path / "check"
    argv = ["sample", *map(str, paths), "--annotator", "ana", "--annotator", "bo", "--out",
            str(out), "--n", "6", "--ia-root", str(tmp_path), "--holdout-registry",
            str(tmp_path / "x.yaml")]  # fmt: skip
    assert script.main(argv) == 0
    capsys.readouterr()
    key = json.loads((out / rc.KEY_FILE).read_text())
    ids = sorted(key["items"])
    truth = {i: "keep" if key["items"][i]["tool_kept"] else "drop" for i in ids}
    flip = {"keep": "drop", "drop": "keep"}
    answer_file(out / "answers_ana.yaml", key, "ana", truth, {i: f"ana on {i}" for i in ids})
    answer_file(out / "answers_bo.yaml", key, "bo", truth | {ids[0]: flip[truth[ids[0]]]},
                {i: f"bo on {i}" for i in ids})  # fmt: skip
    score = ["score", "--dir", str(out)]
    assert script.main(score) == 1
    printed = capsys.readouterr().out
    assert f"not settled yet: {ids[0]}" in printed and "NOT SETTLED YET" in printed
    assert "wrong:" not in printed
    data = page_data(out / rc.RECONCILE_PAGE)
    assert data["mode"] == "reconcile" and data["file"] == rc.RECONCILED_FILE
    assert [i["id"] for i in data["items"]] == [ids[0]]
    assert {v["annotator"]: v["note"] for v in data["items"][0]["votes"]} == {
        "ana": f"ana on {ids[0]}", "bo": f"bo on {ids[0]}"}  # fmt: skip
    for leak in LEAKS:
        assert leak not in json.dumps(data)
    answer_file(out / rc.RECONCILED_FILE, key, "together", {ids[0]: truth[ids[0]]})
    assert script.main(score) == 0
    assert "1 of them together" in capsys.readouterr().out
    answer_file(out / "answers_bo.yaml", key, "bo", truth | {ids[1]: "maybe"})
    assert script.main(score) == 2


def test_the_passage_keeps_the_case_and_marks_the_quote():
    text = "Intro.  The Widget Providers would face   cost number 5 under the option. Rest."
    p = rc._passage("widget providers would face cost number 5 under the option", text, 10)
    assert p["quote"] == "Widget Providers would face cost number 5 under the option"
    assert p["before"].endswith("The ") and p["after"].startswith(". Rest")
    assert rc._passage("nowhere to be found in it", text, 10) is None


def test_one_unsettled_item_blocks_the_verdict_even_under_the_limit():
    key = _key([("judges_accepted", True)] * 20)
    labels = {f"r{n}": "keep" for n in range(1, 21)}
    res = rc.score(key, {"ana": labels, "bo": labels | {"r1": "unsure"}})
    assert res["error"] == pytest.approx(0.05) and res["unsettled"] == ["r1"]
    assert not res["robust"]


def test_answer_files_need_the_sample_id_and_quoted_names_survive(tmp_path):
    key = _key([("tiebreak_kept", True)] * 2, annotators=("yes", "007"))
    labels = {"r1": "keep", "r2": "keep"}
    for name in ("yes", "007"):
        answer_file(tmp_path / f"answers_{name}.yaml", key, name, labels)
    answers, _ = rc.read_answers(tmp_path, key)
    assert set(answers) == {"yes", "007"}  # not True and 7
    (tmp_path / "answers_007.yaml").write_text("annotator: '007'\nanswers: []\n")
    with pytest.raises(rc.ReferenceCheckError, match="belongs to sample None"):
        rc.read_answers(tmp_path, key)
    (tmp_path / "answers_007.yaml").write_text("- just a list\n")
    with pytest.raises(rc.ReferenceCheckError, match="not an answer file"):
        rc.read_answers(tmp_path, key)
