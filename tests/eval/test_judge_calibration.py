"""Coverage-judge calibration (golden-case plan, Revision 2026-10-04): blind sampling,
stratification, scoring and the judge_calibrations record."""

import json
import re

import pytest
import yaml

from womm.eval import judge_calibration as jc
from womm.eval.golden import GoldenError
from womm.evolve import promotion as pm

from .calibration_factory import JV, SV, golden, make_report, write_report


@pytest.fixture
def sample_dir(tmp_path):
    cases = golden(4)
    runs = tmp_path / "runs"
    report = write_report(runs / "eval_a.json",
                          make_report(cases, reps=2, covered=lambda c, e, r: e[-1] in "13",
                                      runs_dir=runs))  # fmt: skip
    out = tmp_path / "cal"
    summary = jc.write_sample(out, system_version=SV, judge_version=JV, reports=[report],
                              cases=cases, runs_dirs=[], n=12, seed=7,
                              annotators=["alice", "bob"])  # fmt: skip
    return out, summary, cases


def test_sample_is_stratified_and_spread_over_cases(sample_dir):
    out, summary, cases = sample_dir
    assert summary["pairs"] == 12
    assert summary["judge_covered"] == 6 and summary["judge_missed"] == 6
    assert summary["cases"] == 4
    key = json.loads((out / jc.KEY_FILE).read_text())
    pop = key["population"]
    assert pop["pairs"] == sum(len(c.expected_impacts) for c in cases.values()) * 2
    assert 0 < pop["natural_covered_rate"] < 1
    assert key["judge_version"] == JV and key["stratification"] == jc.STRATIFICATION
    picked = [(p["case_id"], p["expected_id"]) for p in key["pairs"].values()]
    assert len(set(picked)) == len(picked)  # distinct expected impacts first


def test_sheets_and_answers_are_blind(sample_dir):
    out, _, _ = sample_dir
    key = json.loads((out / jc.KEY_FILE).read_text())
    for name in ("alice", "bob"):
        sheet = (out / f"sheet_{name}.md").read_text()
        answers = (out / f"answers_{name}.yaml").read_text()
        for text in (sheet, answers):
            assert "SECRET_JUSTIFICATION" not in text
            assert "judge_covered" not in text and "impact_id" not in text
        assert all(f"## {pid}" in sheet for pid in key["pairs"])
        assert "affected actor" in sheet and "Dossier summary" in sheet
        rows = yaml.safe_load(answers)["answers"]
        assert [r["pair_id"] for r in rows] == sorted(key["pairs"])
        assert all(r["label"] is None for r in rows)
    order = [(out / f"sheet_{n}.md").read_text().split("## p")[1:] for n in ("alice", "bob")]
    assert [b[:2] for b in order[0]] != [b[:2] for b in order[1]]  # per-annotator order


def test_sample_is_reproducible_by_seed(tmp_path, sample_dir):
    out, _, cases = sample_dir
    again = tmp_path / "again"
    jc.write_sample(again, system_version=SV, judge_version=JV,
                    reports=[tmp_path / "runs" / "eval_a.json"], cases=cases, runs_dirs=[],
                    n=12, seed=7, annotators=["alice", "bob"])  # fmt: skip
    assert (json.loads((again / jc.KEY_FILE).read_text())["pairs"]
            == json.loads((out / jc.KEY_FILE).read_text())["pairs"])  # fmt: skip
    with pytest.raises(jc.CalibrationError, match="exists"):
        jc.write_sample(again, system_version=SV, judge_version=JV,
                        reports=[tmp_path / "runs" / "eval_a.json"], cases=cases, runs_dirs=[],
                        n=12, seed=7, annotators=["alice"])  # fmt: skip


def test_a_short_stratum_is_filled_from_the_other():
    pairs = [jc.Pair("c", "f", "train", f"r{i}", f"e{i}", i < 2, "x") for i in range(10)]
    sample = jc.stratified_sample(pairs, 6, seed=1)
    assert len(sample) == 6 and sum(p.judge_covered for p in sample) == 2


def test_holdout_material_is_refused(tmp_path):
    cases = golden(2)
    runs = tmp_path / "runs"
    report = make_report(cases, runs_dir=runs)
    holdout = write_report(runs / "eval_h.json", report | {
        "metadata": report["metadata"] | {"split": "holdout"}})  # fmt: skip
    with pytest.raises(GoldenError, match="holdout"):
        jc.population([holdout], cases, judge_version=JV, system_version=SV)
    unknown = write_report(runs / "eval_u.json", report)
    with pytest.raises(GoldenError, match="not a public golden case"):
        jc.population([unknown], dict(list(cases.items())[:1]), judge_version=JV,
                      system_version=SV)  # fmt: skip


def test_another_judges_report_is_refused(tmp_path):
    cases = golden(1)
    path = write_report(tmp_path / "r.json", make_report(cases, jv="jv_other"))
    with pytest.raises(jc.CalibrationError, match="jv_other"):
        jc.population([path], cases, judge_version=JV, system_version=SV)
    legacy = write_report(tmp_path / "l.json", make_report(cases, jv=None, sv="sv_other"))
    with pytest.raises(jc.CalibrationError, match="no judge version"):
        jc.population([legacy], cases, judge_version=JV, system_version=SV)


# ----------------------------------------------------------------------------- scoring


def _key(verdicts: dict[str, bool], natural: float = 0.8) -> dict:
    return {"judge_version": JV, "population": {"natural_covered_rate": natural},
            "pairs": {pid: {"judge_covered": v} for pid, v in verdicts.items()}}  # fmt: skip


def test_score_agreement_majority_unsure_and_reweighting():
    judge = {"p1": True, "p2": True, "p3": False, "p4": False, "p5": True}
    answers = {
        "a": {"p1": "covered", "p2": "covered", "p3": "not_covered", "p4": "covered",
              "p5": "unsure"},
        "b": {"p1": "covered", "p2": "not_covered", "p3": "not_covered", "p4": "covered",
              "p5": "unsure"},
        "c": {"p1": "covered", "p2": "covered", "p3": "unsure", "p4": "not_covered",
              "p5": "unsure"},
    }  # fmt: skip
    r = jc.score(_key(judge), answers)
    assert r["annotators"]["a"]["agreement"] == pytest.approx(3 / 4)
    assert r["annotators"]["c"]["unsure"] == 2
    # majority: p1 covered, p2 covered, p3 not (a, b), p4 covered (a, b); p5 all unsure
    assert r["pairs_resolved"] == 4 and r["excluded"] == 1
    assert r["agreement_raw"] == pytest.approx(3 / 4)
    assert r["agreement_by_stratum"] == {"judge_covered": 1.0, "judge_missed": 0.5}
    assert r["agreement_natural"] == pytest.approx(0.8 * 1.0 + 0.2 * 0.5)
    assert r["agreement"] == pytest.approx(0.75)  # the lower of raw and natural
    assert r["confusion"]["judge_missed_human_covered"] == 1
    # judge [T, T, F, F] vs majority [T, T, F, T]: po .75, pe .5*.75 + .5*.25 = .5
    assert r["kappa"] == pytest.approx(0.5)
    assert 0 < r["inter_annotator_agreement"] < 1


def test_a_tie_is_excluded_and_one_annotator_has_no_inter_annotator_agreement():
    r = jc.score(_key({"p1": True, "p2": False}),
                 {"a": {"p1": "covered", "p2": "not_covered"},
                  "b": {"p1": "not_covered", "p2": "not_covered"}})  # fmt: skip
    assert r["pairs_resolved"] == 1 and r["excluded"] == 1
    single = jc.score(_key({"p1": True}), {"a": {"p1": "covered"}})
    assert single["inter_annotator_agreement"] is None and single["agreement"] == 1.0


def test_answers_are_validated(tmp_path):
    (tmp_path / "answers_a.yaml").write_text(yaml.safe_dump(
        {"annotator": "a", "answers": [{"pair_id": "p1", "label": "maybe"}]}))  # fmt: skip
    with pytest.raises(jc.CalibrationError, match="maybe"):
        jc.read_answers(tmp_path, {"p1"})
    (tmp_path / "answers_a.yaml").write_text(yaml.safe_dump(
        {"annotator": "a", "answers": [{"pair_id": "p1", "label": " Covered "}]}))  # fmt: skip
    with pytest.raises(jc.CalibrationError, match="rows removed"):
        jc.read_answers(tmp_path, {"p1", "p2"})
    assert jc.read_answers(tmp_path, {"p1"}) == {"a": {"p1": "covered"}}


def test_record_shape_is_what_the_gate_reads(tmp_path):
    judge = {f"p{i}": i % 2 == 0 for i in range(10)}
    answers = {
        "a": {p: ("covered" if v else "not_covered") for p, v in judge.items()},
        "b": {p: ("covered" if v else "not_covered") for p, v in judge.items()},
    }
    result = jc.score(_key(judge), answers)
    assert jc.record_problems(result) == []
    record = jc.calibration_record(result, "2026-10-20")
    calib = pm.JudgeCalibration.model_validate(record)
    assert calib.agreement == 1.0 and calib.pairs == 10 and calib.annotators == ["a", "b"]
    assert calib.kappa == 1.0
    assert not re.search(r"\bp\d+\b", json.dumps(record))  # aggregates only
    blank = jc.score(_key(judge), {"a": dict.fromkeys(judge)})
    assert "a left 10 pair(s) blank" in jc.record_problems(blank)
