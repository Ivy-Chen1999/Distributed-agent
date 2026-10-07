"""Appending gate records (`womm calibrate score --record`, `womm noise report --record`)
keeps the records file's comments, and an MDD report must match the policy's design."""

import shutil

import pytest
import yaml

from womm.eval.evaluators import judge_version
from womm.evolve import promotion as pm

from .test_promotion import policy, records, versions

CALIB = {"judge_version": "jv_a", "agreement": 0.9, "pairs": 30, "annotators": ["a", "b"],
         "recorded_on": "2026-10-20", "kappa": 0.78}  # fmt: skip


@pytest.fixture
def records_file(tmp_path):
    path = tmp_path / "promotion_records.yaml"
    shutil.copy(pm.RECORDS_PATH, path)
    return path


def test_append_keeps_comments_and_other_lists(records_file):
    header = records_file.read_text().split("judge_calibrations:")[0]
    pm.append_records(records_file, "judge_calibrations", [CALIB])
    mdd = {"metric": "coverage", "judge_version": "jv_a", "mdd": 0.06, "recorded_on": "x",
           "repetitions": 3}  # fmt: skip
    pm.append_records(records_file, "mdd_reports", [mdd])
    pm.append_records(records_file, "judge_calibrations", [CALIB | {"agreement": 0.95}])
    text = records_file.read_text()
    assert text.startswith(header)
    loaded = pm.load_records(records_file)
    assert [c.agreement for c in loaded.judge_calibrations] == [0.9, 0.95]
    assert loaded.calibration("jv_a").agreement == 0.95 and loaded.calibration("jv_a").kappa
    assert loaded.formal_noise_runs == [] and loaded.mdd("coverage", "jv_a").mdd == 0.06
    assert yaml.safe_load(text)["mdd_reports"][0]["repetitions"] == 3


def test_an_invalid_record_is_not_written(records_file):
    before = records_file.read_text()
    with pytest.raises(pm.GateRefused, match="invalid"):
        pm.append_records(records_file, "judge_calibrations", [CALIB | {"agreement": 2}])
    with pytest.raises(ValueError):
        pm.append_records(records_file, "promotions", [CALIB])
    assert records_file.read_text() == before


def test_an_mdd_report_for_other_repetitions_is_not_used():
    cand, base = versions("api")
    jv = judge_version(base)
    recs = records(jv, mdd=0.05)
    recs.mdd_reports[0].repetitions = 6
    choice = pm.choose_mode(policy(), recs, cand, base)  # the policy runs 3
    assert choice.mode == "weak" and "6 repetitions" in choice.notes[0]
    recs.mdd_reports[0].repetitions = 3
    assert pm.choose_mode(policy(), recs, cand, base).mode == "statistical"
