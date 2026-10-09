"""The one-time reference-answer check: population, blind sheets and the error-rate gate."""

from __future__ import annotations

import importlib.util
import json
import sys

import pytest
import yaml

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


def test_the_sheets_are_blind_and_the_key_is_private(tmp_path):
    out = tmp_path / "check"
    drafts = [tiebroken("case_90_a"), tiebroken("case_91_b")]
    res = rc.write_sample(out, drafts, n=6, seed=1, annotators=["ana", "bo"], ia_root=tmp_path)
    assert res["items"] == 6 and res["missing_ia"] == ["data_act"]
    sheet = (out / "sheet_ana.md").read_text()
    for leak in ("llm_kept", "llm_dropped", "auto_accepted", "repeats e01", "tie-break", "judge"):
        assert leak not in sheet
    assert "widget providers would face cost number" in sheet and "keep / drop / unsure" in sheet
    key = json.loads((out / rc.KEY_FILE).read_text())
    assert set(key["items"]) == {f"r{i}" for i in range(1, 7)}
    assert abs(sum(key["population"]["shares"].values()) - 1) < 1e-9
    assert (out / "sheet_ana.md").read_text() != (out / "sheet_bo.md").read_text()  # own order
    with pytest.raises(rc.ReferenceCheckError, match="exists"):
        rc.write_sample(out, drafts, n=6, seed=1, annotators=["ana"])
    with pytest.raises(rc.ReferenceCheckError, match="distinct"):
        rc.write_sample(tmp_path / "x", drafts, n=6, seed=1, annotators=["ana", "ana"])


def _key(rows: list[tuple[str, bool]]) -> dict:
    items = {f"r{n}": {"case_id": "c", "fixture": "f", "item_id": f"i{n}", "stratum": s,
                       "tool_kept": k} for n, (s, k) in enumerate(rows, 1)}  # fmt: skip
    shares = {s: sum(r[0] == s for r in rows) / len(rows) for s in rc.STRATA}
    return {"items": items, "population": {"items": len(rows), "shares": shares}}


def test_score_counts_the_tool_wrong_where_the_majority_disagrees():
    key = _key([("judges_accepted", True)] * 10 + [("tiebreak_dropped", False)] * 10)
    yes = {f"r{n}": "keep" if n <= 10 else "drop" for n in range(1, 21)}
    one_wrong = yes | {"r11": "keep"}
    res = rc.score(key, {"ana": one_wrong, "bo": one_wrong})
    assert res["error_raw"] == pytest.approx(0.05) and res["robust"]
    assert res["wrong"] == ["r11"] and res["error_by_stratum"]["tiebreak_dropped"] == 0.1
    three_wrong = yes | {"r11": "keep", "r12": "keep", "r1": "drop"}
    res = rc.score(key, {"ana": three_wrong, "bo": three_wrong})
    assert res["error"] == pytest.approx(0.15) and not res["robust"]


def test_ties_and_unsure_are_excluded_and_blanks_block_the_verdict():
    key = _key([("tiebreak_kept", True)] * 4)
    ana = {"r1": "keep", "r2": "keep", "r3": "unsure", "r4": "keep"}
    bo = {"r1": "keep", "r2": "drop", "r3": "unsure", "r4": None}
    res = rc.score(key, {"ana": ana, "bo": bo})
    assert res["items_resolved"] == 2 and res["excluded"] == 2  # r2 tied, r3 all unsure
    assert res["error"] == 0 and not res["robust"]  # bo left r4 blank
    assert res["inter_annotator_agreement"] == pytest.approx(0.5)


def test_the_natural_rate_weights_the_strata_by_population():
    key = _key([("judges_accepted", True)] * 2 + [("tiebreak_kept", True)] * 2)
    key["population"]["shares"] = {"judges_accepted": 0.9, "tiebreak_kept": 0.1,
                                   "tiebreak_dropped": 0.0}  # fmt: skip
    labels = {"r1": "keep", "r2": "keep", "r3": "drop", "r4": "keep"}
    res = rc.score(key, {"ana": labels})
    assert res["error_raw"] == 0.25 and res["error_natural"] == pytest.approx(0.05)
    assert res["error"] == 0.25  # the higher of the two


def test_the_script_samples_then_scores(tmp_path, capsys):
    drafts_dir = tmp_path / "drafts"
    paths = [write_draft(tiebroken(c), drafts_dir) for c in ("case_90_a", "case_91_b")]
    out = tmp_path / "check"
    argv = ["sample", *map(str, paths), "--annotator", "ana", "--out", str(out), "--n", "4",
            "--ia-root", str(tmp_path), "--holdout-registry", str(tmp_path / "x.yaml")]  # fmt: skip
    assert script.main(argv) == 0
    key = json.loads((out / rc.KEY_FILE).read_text())
    answers = yaml.safe_load((out / "answers_ana.yaml").read_text())
    for row in answers["answers"]:
        row["label"] = "keep" if key["items"][row["item"]]["tool_kept"] else "drop"
    (out / "answers_ana.yaml").write_text(yaml.safe_dump(answers))
    assert script.main(["score", "--dir", str(out)]) == 0
    assert "ROBUST" in capsys.readouterr().out
    answers["answers"][0]["label"] = "maybe"
    (out / "answers_ana.yaml").write_text(yaml.safe_dump(answers))
    assert script.main(["score", "--dir", str(out)]) == 2


def test_the_html_page_is_blind_and_its_answers_are_scored(tmp_path):
    import re

    out = tmp_path / "check"
    drafts = [tiebroken("case_90_a"), tiebroken("case_91_b")]
    rc.write_sample(out, drafts, n=6, seed=1, annotators=["ana"], ia_root=tmp_path)
    page = (out / "label_ana.html").read_text()
    data = json.loads(re.search(r"const DATA = (.*);\n", page).group(1))
    assert data["annotator"] == "ana" and len(data["items"]) == 6
    for leak in ("llm_kept", "llm_dropped", "auto_accepted", "repeats e01", "why_missing",
                 "tiebreak", "judge"):  # fmt: skip
        assert leak not in json.dumps(data)
    assert "__DATA__" not in page and "__ANNOTATOR__" not in page
    # The page's download, in the format its JavaScript writes (JSON-quoted notes).
    rows = ["annotator: ana", "answers:"]
    for item in sorted(i["id"] for i in data["items"]):
        rows += [f"- item: {item}", "  label: keep", '  note: "a \\"quoted\\" note"']
    (out / "answers_ana.yaml").write_text("\n".join(rows) + "\n")
    key = json.loads((out / rc.KEY_FILE).read_text())
    answers = rc.read_answers(out, set(key["items"]))
    assert set(answers["ana"].values()) == {"keep"}
    assert rc.score(key, answers)["items_resolved"] == 6


def test_the_passage_keeps_the_case_and_marks_the_quote():
    text = "Intro.  The Widget Providers would face   cost number 5 under the option. Rest."
    p = rc._passage("widget providers would face cost number 5 under the option", text, 10)
    assert p["quote"] == "Widget Providers would face cost number 5 under the option"
    assert p["before"].endswith("The ") and p["after"].startswith(". Rest")
    assert rc._passage("nowhere to be found in it", text, 10) is None
