"""`womm policy sign`: the owner signs the pre-registered promotion policy with one command."""

import shutil

import yaml

from womm import cli
from womm.evolve import promotion as pm


def _policy(tmp_path):
    path = tmp_path / "promotion_policy.yaml"
    shutil.copy(pm.POLICY_PATH, path)
    data = yaml.safe_load(path.read_text())
    assert data["signed_by"] is None, "the committed policy is expected unsigned"
    return path


def test_sign_fills_in_name_and_date_and_keeps_the_comments(tmp_path, capsys):
    path = _policy(tmp_path)
    before = path.read_text()
    assert cli.main(["policy", "sign", "--by", "Ana Owner", "--policy", str(path)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "primary metric" in out and "commit it now" in out
    policy, _ = pm.load_policy(path)
    assert policy.signed and policy.signed_by == "Ana Owner"
    after = path.read_text()
    assert after.count("\n") == before.count("\n") and "HUMAN STEP" in after
    # A signed policy is never changed.
    assert cli.main(["policy", "sign", "--by", "Someone Else", "--policy", str(path)]) != 0
    assert "already signed" in capsys.readouterr().err
    assert pm.load_policy(path)[0].signed_by == "Ana Owner"


def test_sign_needs_a_name(tmp_path, capsys):
    path = _policy(tmp_path)
    assert cli.main(["policy", "sign", "--by", "  ", "--policy", str(path)]) != 0
    assert not pm.load_policy(path)[0].signed
