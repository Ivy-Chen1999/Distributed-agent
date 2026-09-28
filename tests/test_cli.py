import json

import pytest
import yaml

from womm import cli
from womm.config import DEFAULT_SYSTEM_VERSION
from womm.llm.base import LLMError
from womm.llm.claude_code import IsolationCheckFailed, SelfCheckReport
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity

from .eval.test_run_eval_fake import _script
from .graph.conftest import good_script


@pytest.fixture
def fake_version(tmp_path):
    spec = yaml.safe_load(DEFAULT_SYSTEM_VERSION.read_text())
    for role in ("planner", "synthesis", "judge"):
        spec[role]["backend"] = "fake"
    for e in spec["experts"]:
        e["role"]["backend"] = "fake"
    path = tmp_path / "fake.yaml"
    path.write_text(yaml.safe_dump(spec))
    return path


@pytest.fixture
def use_script(monkeypatch):
    def install(script):
        async def fake_prepare(sv, *, skip_self_check=False):
            return {"fake": FakeBackend(script)}, None, None

        monkeypatch.setattr(cli, "prepare_backends", fake_prepare)
        monkeypatch.setattr(
            cli, "code_identity", lambda v=None: CodeIdentity(git_sha="t", dirty=False)
        )

    return install


def _args(fake_version, tmp_path, *rest):
    return [*rest, "--system-version", str(fake_version), "--runs-dir", str(tmp_path / "runs")]


def test_scenarios_json(capsys):
    assert cli.main(["scenarios", "--json"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert {s["scenario_id"] for s in data} >= {
        "eval_sme_impacts",
        "eval_provider_compliance_costs",
    }


def test_run_unknown_scenario_is_usage_error(fake_version, tmp_path, capsys, use_script):
    use_script({})
    code = cli.main(_args(fake_version, tmp_path, "run", "nope"))
    err = capsys.readouterr()
    assert code == cli.EXIT_USAGE and "unknown scenario" in err.err and err.out == ""


def test_run_json_success(fake_version, tmp_path, capsys, use_script):
    script = _script()
    del script["judge"]
    use_script(script)
    code = cli.main(_args(fake_version, tmp_path, "run", "eval_sme_impacts", "--json"))
    data = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK and data["status"] == "succeeded"
    assert (tmp_path / "runs" / f"{data['run_id']}.json").is_file()


def test_run_degraded_exit_code(fake_version, tmp_path, capsys, use_script):
    script = good_script()
    script["synthesis"] = [LLMError("schema_invalid", "bad", attempts=3)]
    use_script(script)
    code = cli.main(_args(fake_version, tmp_path, "run", "eval_sme_impacts", "--json"))
    assert code == cli.EXIT_DEGRADED
    assert json.loads(capsys.readouterr().out)["status"] == "degraded"


def test_eval_unknown_case(fake_version, tmp_path, capsys, use_script):
    use_script({})
    assert cli.main(_args(fake_version, tmp_path, "eval", "--case", "x")) == cli.EXIT_USAGE
    assert "unknown golden case" in capsys.readouterr().err


def test_eval_local_json_skips_langsmith(fake_version, tmp_path, capsys, use_script, monkeypatch):
    use_script(_script())

    async def must_not_run(*a, **k):
        raise AssertionError("LangSmith recording must be skipped with --local")

    monkeypatch.setattr(cli, "record_langsmith_experiment", must_not_run)
    code = cli.main(
        _args(fake_version, tmp_path, "eval", "--case", "case_02_sme_impacts", "--local", "--json")
    )
    data = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK and data["summary"]["scored"] == 1
    assert (tmp_path / "runs").joinpath(data["report_path"].split("/")[-1]).is_file()


def test_eval_baseline_on_dirty_tree_refused(
    fake_version, tmp_path, capsys, use_script, monkeypatch
):
    use_script(_script())
    monkeypatch.setattr(cli, "code_identity", lambda v=None: CodeIdentity(git_sha="t", dirty=True))
    code = cli.main(_args(fake_version, tmp_path, "eval", "--case", "case_02_sme_impacts",
                          "--baseline", "--local"))  # fmt: skip
    assert code == cli.EXIT_USAGE and "dirty" in capsys.readouterr().err


def test_selfcheck_failure_reports_json(tmp_path, capsys, monkeypatch):
    report = SelfCheckReport(passed=False, flags=["--tools", ""], problems=["canary leaked"])

    async def failing(sv, *, skip_self_check=False):
        raise IsolationCheckFailed(report)

    monkeypatch.setattr(cli, "prepare_backends", failing)
    code = cli.main(["selfcheck", "--json", "--runs-dir", str(tmp_path)])
    data = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_BACKEND and data["passed"] is False
    assert data["problems"] == ["canary leaked"]
    assert list(tmp_path.glob("selfcheck_*.json"))


def test_backend_auth_error_exit_code(fake_version, tmp_path, capsys, monkeypatch):
    async def not_logged_in(sv, *, skip_self_check=False):
        raise LLMError("auth", "Not logged in")

    monkeypatch.setattr(cli, "prepare_backends", not_logged_in)
    code = cli.main(_args(fake_version, tmp_path, "run", "eval_sme_impacts"))
    assert code == cli.EXIT_BACKEND and "backend unavailable" in capsys.readouterr().err


def test_missing_system_version_file(tmp_path, capsys):
    code = cli.main(["run", "eval_sme_impacts", "--system-version", str(tmp_path / "no.yaml")])
    assert code == cli.EXIT_USAGE and "invalid configuration" in capsys.readouterr().err
