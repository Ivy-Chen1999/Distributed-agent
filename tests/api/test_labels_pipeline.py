"""The whole hosted labelling path, end to end against Postgres, for both tasks: draw a sample,
upload it, people label on the app and submit, fetch, then the unchanged local score (and for
the calibration, the --record step). If this passes, finished labels reach the scoring."""

import json
import shutil

import pytest
from fastapi.testclient import TestClient

from womm import cli
from womm.api.app import create_app
from womm.config import load_settings
from womm.data.fixtures import load_fixture
from womm.eval import hosted_labels as hl
from womm.eval import judge_calibration as jc
from womm.eval import reference_check as rc
from womm.evolve import promotion as pm

from ..eval.calibration_factory import JV, SV, golden, make_report, write_report
from ..eval.test_reference_check import script, tiebroken
from ..graph.conftest import fake_sv

ADMIN = "admin-token-0123456789"
URL = "http://testserver"


@pytest.fixture
def app_client(database_url, monkeypatch):
    env = {"DATABASE_URL": database_url, "WOMM_API_TOKEN": "api-token-0123456789",
           "WOMM_LABEL_ADMIN_TOKEN": ADMIN}  # fmt: skip
    app = create_app(load_settings(env), sv=fake_sv(), fixture=load_fixture(), backends={},
                     web_dist=None)  # fmt: skip
    monkeypatch.setenv(hl.TOKEN_ENV, ADMIN)
    with TestClient(app) as c:
        # The CLI builds its own httpx client: hand it the app's.
        monkeypatch.setattr(hl.httpx, "Client", lambda **_: c)
        yield c


def _label_all(c: TestClient, sample: str, name: str, choose) -> None:
    """One annotator's whole sitting, through the same endpoints the page calls."""
    c.cookies.clear()
    me = c.post(f"/label/api/{sample}/session", json={"name": name})
    assert me.status_code == 200, me.text
    assert c.get(f"/label/{sample}").status_code == 200
    for card in me.json()["items"]:
        label, note = choose(card["id"])
        r = c.put(f"/label/api/{sample}/answers/{card['id']}", json={"label": label, "note": note})
        assert r.status_code == 200, r.text
    assert c.post(f"/label/api/{sample}/submit").status_code == 200


def test_reference_check_labels_reach_the_score(app_client, tmp_path, capsys):
    out = tmp_path / "check"
    rc.write_sample(out, [tiebroken("case_90_a"), tiebroken("case_91_b")], n=3, seed=1,
                    annotators=["ana", "bo"], ia_root=tmp_path)  # fmt: skip
    key = json.loads((out / rc.KEY_FILE).read_text())
    truth = {i: "keep" if it["tool_kept"] else "drop" for i, it in key["items"].items()}
    base = ["--dir", str(out), "--url", URL, "--holdout-registry", str(tmp_path / "none.yaml")]
    assert script.main(["upload", *base]) == 0
    assert f"{URL}/label/{key['sample']}" in capsys.readouterr().out
    wrong = sorted(truth)[0]
    flip = {"keep": "drop", "drop": "keep"}
    for name in ("ana", "bo"):
        _label_all(app_client, key["sample"], name,
                   lambda i: (flip[truth[i]], "the text is about option 2") if i == wrong
                   else (truth[i], ""))  # fmt: skip
    assert script.main(["fetch", *base]) == 0
    assert "waiting" not in capsys.readouterr().out
    assert script.main(["score", "--dir", str(out)]) == 1  # 1 wrong in 6: not robust
    printed = capsys.readouterr().out
    assert f"wrong: {wrong}" in printed and "person: the text is about option 2" in printed


def test_reference_check_flags_a_real_error_rate(app_client, tmp_path, capsys):
    out = tmp_path / "check"
    rc.write_sample(out, [tiebroken("case_90_a"), tiebroken("case_91_b")], n=6, seed=2,
                    annotators=["ana", "bo"], ia_root=tmp_path)  # fmt: skip
    key = json.loads((out / rc.KEY_FILE).read_text())
    truth = {i: "keep" if it["tool_kept"] else "drop" for i, it in key["items"].items()}
    base = ["--dir", str(out), "--url", URL, "--holdout-registry", str(tmp_path / "none.yaml")]
    assert script.main(["upload", *base]) == 0
    for name in ("ana", "bo"):
        _label_all(app_client, key["sample"], name, lambda i: (truth[i], ""))
    assert script.main(["fetch", *base]) == 0
    assert script.main(["score", "--dir", str(out)]) == 0
    assert "ROBUST" in capsys.readouterr().out


def test_calibration_labels_reach_the_score_and_the_record(app_client, tmp_path, capsys):
    cases = golden(4)
    runs = tmp_path / "runs"
    report = write_report(runs / "eval_a.json", make_report(
        cases, reps=2, covered=lambda c, e, r: e[-1] in "13", runs_dir=runs))  # fmt: skip
    out = tmp_path / "cal"
    jc.write_sample(out, system_version=SV, judge_version=JV, reports=[report], cases=cases,
                    runs_dirs=[], n=8, seed=7, annotators=["alice", "bob"])  # fmt: skip
    key = json.loads((out / jc.KEY_FILE).read_text())
    pages = json.loads((out / hl.PAGES_FILE).read_text())
    assert pages["kind"] == "calibration" and pages["sample"] == key["sample"]
    assert "SECRET_JUSTIFICATION" not in json.dumps(pages) and "judge_covered" not in json.dumps(
        pages
    )
    assert cli.main(["calibrate", "upload", "--dir", str(out), "--url", URL]) == cli.EXIT_OK
    assert key["sample"] in capsys.readouterr().out
    page = app_client.get(f"/label/{key['sample']}").text
    assert '"kind": "calibration"' in page
    judge = {pid: p["judge_covered"] for pid, p in key["pairs"].items()}
    for name in ("alice", "bob"):
        _label_all(
            app_client,
            key["sample"],
            name,
            lambda i: ("covered" if judge[i] else "not_covered", ""),
        )
    # A label of the other task is refused.
    r = app_client.put(f"/label/api/{key['sample']}/answers/{sorted(judge)[0]}",
                       json={"label": "keep"})  # fmt: skip
    assert r.status_code == 422
    assert cli.main(["calibrate", "fetch", "--dir", str(out), "--url", URL]) == cli.EXIT_OK
    records = tmp_path / "promotion_records.yaml"
    shutil.copy(pm.RECORDS_PATH, records)
    rc_ = cli.main(["calibrate", "score", "--dir", str(out), "--record", "--records",
                    str(records)])  # fmt: skip
    assert rc_ == cli.EXIT_OK, capsys.readouterr().err
    calib = pm.load_records(records).judge_calibrations[-1]
    assert calib.annotators == ["alice", "bob"] and calib.agreement == 1.0 and calib.pairs == 8


def test_fetch_before_everyone_is_done_cannot_be_recorded(app_client, tmp_path, capsys):
    cases = golden(4)
    runs = tmp_path / "runs"
    report = write_report(runs / "eval_a.json", make_report(cases, reps=1, runs_dir=runs))
    out = tmp_path / "cal"
    jc.write_sample(out, system_version=SV, judge_version=JV, reports=[report], cases=cases,
                    runs_dirs=[], n=4, seed=1, annotators=["alice", "bob"])  # fmt: skip
    key = json.loads((out / jc.KEY_FILE).read_text())
    assert cli.main(["calibrate", "upload", "--dir", str(out), "--url", URL]) == cli.EXIT_OK
    _label_all(app_client, key["sample"], "alice", lambda i: ("covered", ""))
    assert cli.main(["calibrate", "fetch", "--dir", str(out), "--url", URL]) == cli.EXIT_OK
    assert "waiting for bob" in capsys.readouterr().out
    records = tmp_path / "promotion_records.yaml"
    shutil.copy(pm.RECORDS_PATH, records)
    assert cli.main(["calibrate", "score", "--dir", str(out), "--record", "--records",
                     str(records)]) != cli.EXIT_OK  # fmt: skip
    assert "not recorded" in capsys.readouterr().err


def test_upload_without_the_token_is_refused(app_client, tmp_path, monkeypatch, capsys):
    out = tmp_path / "check"
    rc.write_sample(out, [tiebroken("case_90_a")], n=3, seed=1, annotators=["ana"],
                    ia_root=tmp_path)  # fmt: skip
    monkeypatch.setenv(hl.TOKEN_ENV, "wrong-token-0123456789")
    base = ["--dir", str(out), "--url", URL, "--holdout-registry", str(tmp_path / "none.yaml")]
    assert script.main(["upload", *base]) == 2
    assert "401" in capsys.readouterr().err
