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


def _sweep(cost_version, tmp_path, capsys, version="com2021_206"):
    cli.main(["cost", "sweep", "--sv", str(cost_version), "--version", version,
              "--repetitions", "2", "--runs-dir", str(tmp_path), "--json"])  # fmt: skip
    return json.loads(capsys.readouterr().out)["directory"]


def _verified_reference(tmp_path):
    from womm.eval.cost_check import REFERENCE_PATH

    data = yaml.safe_load(REFERENCE_PATH.read_text(encoding="utf-8"))
    data.update(status="verified", verified_by="test", verified_on="2026-10-07")
    path = tmp_path / "ref.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_cost_check_refuses_the_unverified_reference(cost_version, tmp_path, capsys,
                                                     fake_backends):  # fmt: skip
    directory = _sweep(cost_version, tmp_path, capsys)
    code = cli.main(["cost", "check", "--sweep", directory, "--runs-dir", str(tmp_path)])
    assert code == cli.EXIT_USAGE
    assert "unverified" in capsys.readouterr().err


def test_cost_check_scores_a_proposal_sweep(cost_version, tmp_path, capsys, fake_backends):
    directory = _sweep(cost_version, tmp_path, capsys)
    ref = _verified_reference(tmp_path)
    code = cli.main(["cost", "check", "--sweep", directory, "--reference", str(ref),
                     "--runs-dir", str(tmp_path)])  # fmt: skip
    assert code == cli.EXIT_OK
    text = capsys.readouterr().out
    assert "cost_recall" in text and "dev-only (fake backend)" in text
    assert "descriptive, not significant" in text
    written = list((tmp_path / "cost_check").glob("*.json"))
    assert len(written) == 1 and written[0].with_suffix(".md").exists()
    data = json.loads(written[0].read_text())
    assert data["repetitions"] == 2 and data["header"]["reference_sha"]


async def test_cost_check_scores_saved_runs_on_their_scenario_items_only(tmp_path, capsys):
    from womm.data.fixtures import load_fixture
    from womm.decisions.stub import StubDecisionService
    from womm.graph.build import run_scenario

    from ..graph.test_cost_node import SCENARIO, _fake, _script

    fixture = load_fixture()
    paths = []
    for n in (1, 2):
        result = await run_scenario(
            SCENARIO, sv=_fake("v1.0-cost.yaml", cost=True), fixture=fixture,
            backends={"fake": FakeBackend(_script(fixture, [fake_cost_batch] * 5))},
            decisions=StubDecisionService(),
            code_identity=CodeIdentity(git_sha="t", dirty=False), run_id=f"run_{n}",
        )  # fmt: skip
        path = tmp_path / f"run_{n}.json"
        path.write_text(result.model_dump_json())
        paths.append(str(path))
    ref = _verified_reference(tmp_path)
    args = ["cost", "check", "--runs", *paths, "--reference", str(ref), "--runs-dir",
            str(tmp_path), "--json"]  # fmt: skip
    assert await __import__("asyncio").to_thread(cli.main, args) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    scored = set(data["per_repetition"]["1"]["items"])
    assert "ia09_national_authorities" not in scored and "ia01_data" in scored
    assert data["repetitions"] == 2 and data["header"]["restricted_to"]


def test_cost_check_refuses_a_final_act_sweep(cost_version, tmp_path, capsys, fake_backends):
    directory = _sweep(cost_version, tmp_path, capsys, version="reg2024_1689")
    ref = _verified_reference(tmp_path)
    code = cli.main(["cost", "check", "--sweep", directory, "--reference", str(ref),
                     "--runs-dir", str(tmp_path)])  # fmt: skip
    assert code == cli.EXIT_USAGE
    assert "assessed com2021_206" in capsys.readouterr().err


def test_cost_late_added_reports_a_final_act_sweep(cost_version, tmp_path, capsys,
                                                   fake_backends):  # fmt: skip
    directory = _sweep(cost_version, tmp_path, capsys, version="reg2024_1689")
    assert cli.main(["cost", "late-added", "--sweep", directory]) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert text.splitlines()[0].startswith("Not scored: SWD(2021) 84 assessed the proposal")
    assert "dev-only (fake backend)" in text and "across repetitions" in text
    assert cli.main(["cost", "late-added", "--sweep", directory, "--json"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["scored"] is False and set(data["repetitions"]) == {"1", "2"}
    assert data["repetitions"]["1"]["added_records"]


def test_cost_late_added_refuses_a_proposal_sweep(cost_version, tmp_path, capsys,
                                                  fake_backends):  # fmt: skip
    directory = _sweep(cost_version, tmp_path, capsys)
    assert cli.main(["cost", "late-added", "--sweep", directory]) == cli.EXIT_USAGE
    assert "not the proposal" in capsys.readouterr().err
