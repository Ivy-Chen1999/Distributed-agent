"""`womm cost ...` commands on the fake backend."""

import json

import pytest
import yaml

from womm import cli
from womm.config import REPO_ROOT
from womm.llm.fake import FakeBackend, fake_cost_batch
from womm.models.run import CodeIdentity


@pytest.fixture
def cost_version(tmp_path):
    spec = yaml.safe_load((REPO_ROOT / "system_versions" / "v1.0-cost.yaml").read_text())
    for role in ("planner", "synthesis", "judge", "cost"):
        spec[role]["backend"] = "fake"
    for e in spec["experts"]:
        e["role"]["backend"] = "fake"
    path = tmp_path / "cost-fake.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


@pytest.fixture
def fake_backends(monkeypatch):
    backend = FakeBackend({"cost": [fake_cost_batch] * 100})

    async def fake_prepare(sv, *, skip_self_check=False, settings=None):
        return {"fake": backend}, None, None

    monkeypatch.setattr(cli, "prepare_backends", fake_prepare)
    monkeypatch.setattr(
        cli, "code_identity", lambda v=None: CodeIdentity(git_sha="cafe1234", dirty=False)
    )
    return backend


def test_cost_sweep_refuses_an_unknown_version(cost_version, tmp_path, capsys, fake_backends):
    code = cli.main(["cost", "sweep", "--sv", str(cost_version), "--version", "nope",
                     "--runs-dir", str(tmp_path)])  # fmt: skip
    assert code == cli.EXIT_USAGE
    assert "com2021_206" in capsys.readouterr().err
    assert fake_backends.calls == []


def test_cost_sweep_refuses_a_version_without_a_cost_role(tmp_path, capsys, fake_backends):
    path = REPO_ROOT / "system_versions" / "v1.0-unscoped.yaml"
    code = cli.main(["cost", "sweep", "--sv", str(path), "--version", "com2021_206",
                     "--runs-dir", str(tmp_path)])  # fmt: skip
    assert code == cli.EXIT_USAGE
    assert "no cost role" in capsys.readouterr().err


def test_sv_names_resolve_under_system_versions():
    assert cli._sv_path("v1.0-cost") == REPO_ROOT / "system_versions" / "v1.0-cost.yaml"


def test_cost_sweep_runs_and_reports(cost_version, tmp_path, capsys, fake_backends):
    code = cli.main(["cost", "sweep", "--sv", str(cost_version), "--version", "com2021_206",
                     "--repetitions", "2", "--runs-dir", str(tmp_path), "--json"])  # fmt: skip
    assert code == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["batches"] == 9 and data["repetitions"] == 2 and data["calls"] == 18
    assert data["failed"] == [] and data["directory"].startswith(str(tmp_path / "cost_sweeps"))
    assert len(fake_backends.calls) == 18
