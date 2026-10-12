"""`womm policy sign`: the owner signs the pre-registered promotion policy with one command."""

import yaml

from womm import cli
from womm.evolve import promotion as pm


def _policy(tmp_path):
    """The committed policy with its signature lines reset, as it was before signing."""
    path = tmp_path / "promotion_policy.yaml"
    text = pm.POLICY_PATH.read_text()
    data = yaml.safe_load(text)
    if data["signed_by"] is not None:
        text = text.replace(f'signed_by: "{data["signed_by"]}"', "signed_by: null")
        text = text.replace(f'signed_on: "{data["signed_on"]}"', "signed_on: null")
    path.write_text(text)
    assert yaml.safe_load(path.read_text())["signed_by"] is None
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
