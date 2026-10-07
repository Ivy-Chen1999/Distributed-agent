"""`womm calibrate sample|score` and `womm noise report` end to end (fake backends, no
network): the human only fills in labels and commits the records file."""

import asyncio
import json
import shutil

import yaml

from womm import cli
from womm.eval import judge_calibration as jc
from womm.eval.run_eval import write_report as write_eval_report
from womm.evolve import promotion as pm

from .calibration_factory import golden, make_report, write_report
from .test_run_eval_fake import _evaluate, _script


def _records(tmp_path):
    path = tmp_path / "promotion_records.yaml"
    shutil.copy(pm.RECORDS_PATH, path)
    return path


def _sv_file(tmp_path):
    from ..graph.conftest import fake_sv

    sv = fake_sv()
    spec = sv.spec.model_dump(mode="json")
    path = tmp_path / "fake_sv.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


def test_calibrate_sample_then_score_and_record(fixture, tmp_path, capsys):
    runs = tmp_path / "runs"
    report = asyncio.run(_evaluate(fixture, _script(reps=2), repetitions=2, runs_dir=runs))
    path = write_eval_report(report, runs)
    out = tmp_path / "cal"
    rc = cli.main(["calibrate", "sample", "--sv", str(_sv_file(tmp_path)), "--report", str(path),
                   "--n", "4", "--seed", "3", "--annotator", "ann", "--annotator", "bob",
                   "--out", str(out), "--json"])  # fmt: skip
    assert rc == cli.EXIT_OK
    summary = json.loads(capsys.readouterr().out)
    assert summary["pairs"] == 4 and summary["judge_covered"] == 4  # the fake judge covers all
    key = json.loads((out / jc.KEY_FILE).read_text())
    for name, flip in (("ann", None), ("bob", sorted(key["pairs"])[0])):
        answers_path = out / f"answers_{name}.yaml"
        data = yaml.safe_load(answers_path.read_text())
        for row in data["answers"]:
            row["label"] = "not_covered" if row["pair_id"] == flip else "covered"
        answers_path.write_text(yaml.safe_dump(data))
    records = _records(tmp_path)
    rc = cli.main(["calibrate", "score", "--dir", str(out), "--record", "--records",
                   str(records)])  # fmt: skip
    assert rc == cli.EXIT_OK, capsys.readouterr().err
    text = capsys.readouterr().out
    assert "gate agreement" in text and "recorded in" in text
    calib = pm.load_records(records).judge_calibrations[-1]
    assert calib.judge_version == key["judge_version"] and calib.annotators == ["ann", "bob"]
    assert calib.pairs == 3 and calib.agreement == 1.0  # the tied pair is excluded
    assert calib.excluded == 1


def test_score_refuses_to_record_blank_labels(tmp_path, capsys):
    cases = golden(2)
    runs = tmp_path / "runs"
    report = write_report(runs / "eval.json", make_report(cases, runs_dir=runs))
    out = tmp_path / "cal"
    jc.write_sample(out, system_version="sv_test0000001", judge_version="jv_test0000001",
                    reports=[report], cases=cases, runs_dirs=[], n=4, seed=1,
                    annotators=["a"])  # fmt: skip
    records = _records(tmp_path)
    before = records.read_text()
    rc = cli.main(["calibrate", "score", "--dir", str(out), "--record", "--records",
                   str(records)])  # fmt: skip
    assert rc == cli.EXIT_USAGE and "blank" in capsys.readouterr().err
    assert records.read_text() == before


def test_sample_refuses_a_holdout_report(tmp_path, capsys):
    runs = tmp_path / "runs"
    data = make_report(golden(1))
    data["metadata"]["split"] = "holdout"
    path = write_report(runs / "eval.json", data)
    rc = cli.main(["calibrate", "sample", "--sv", str(_sv_file(tmp_path)), "--report", str(path),
                   "--seed", "1", "--out", str(tmp_path / "cal")])  # fmt: skip
    assert rc == cli.EXIT_USAGE and "holdout" in capsys.readouterr().err
    assert not (tmp_path / "cal").exists()


def _metric(case_id, metric, rep):
    return 0.5 + 0.1 * ((rep + int(case_id[6])) % 3)


def test_noise_report_records_a_formal_run_and_refuses_a_dev_one(tmp_path, capsys):
    records = _records(tmp_path)
    before = records.read_text()
    dev = write_report(tmp_path / "dev.json", make_report(
        golden(3, split="val"), reps=6, metric=_metric, backends=("claude_code",)))  # fmt: skip
    args = ["noise", "report", "--holdout-cases", "8", "--holdout-proposals", "8", "--records",
            str(records)]  # fmt: skip
    rc = cli.main([*args, "--report", str(dev), "--record"])
    assert rc == cli.EXIT_USAGE and "not a formal run" in capsys.readouterr().err
    assert records.read_text() == before
    rc = cli.main([*args, "--report", str(dev)])  # printing a dev noise run is fine
    assert rc == cli.EXIT_OK and "not recordable" in capsys.readouterr().out

    formal = write_report(tmp_path / "formal.json", make_report(
        golden(3, split="val"), reps=6, metric=_metric, formal=True,
        backends=("api",)))  # fmt: skip
    rc = cli.main([*args, "--report", str(formal), "--record", "--json"])
    assert rc == cli.EXIT_OK, capsys.readouterr().err
    out = json.loads(capsys.readouterr().out)
    assert out["design"]["repetitions"] == 3  # the policy's
    loaded = pm.load_records(records)
    assert len(loaded.formal_noise_runs) == 1 and loaded.formal_noise_runs[0].repetitions == 6
    cov = loaded.mdd("coverage", "jv_test0000001")
    assert cov is not None and cov.repetitions == 3 and cov.holdout_cases == 8
    assert cov.mdd == round(out["metrics"]["coverage"]["mdd"], 4)
