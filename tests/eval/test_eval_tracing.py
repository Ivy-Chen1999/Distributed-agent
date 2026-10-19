"""Eval runs and LangSmith: trajectory metrics in the report, feedback on the real graph run,
traj.* in the recorded experiment, and nothing traced for a sealed split."""

import json
import time
from unittest.mock import MagicMock

import pytest
from langsmith import Client, tracing_context

from womm.eval.trajectory import TRAJECTORY_KEYS

from ..graph.conftest import fixture  # noqa: F401
from .test_run_eval_fake import CASE, _evaluate, _FakeClient, _script


@pytest.fixture(autouse=True)
def _no_env_tracing(monkeypatch):
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY",
                "LANGCHAIN_API_KEY"):  # fmt: skip
        monkeypatch.delenv(var, raising=False)


async def test_report_carries_trajectory_per_case_and_summary(fixture):  # noqa: F811
    report = await _evaluate(fixture, _script())
    (score,) = report.scores
    assert set(TRAJECTORY_KEYS) <= set(score.trajectory)
    # The fake plan picks only Art 55 of the SME case's anchor keys.
    assert score.trajectory["planner_key_precision"] == 1.0
    assert 0 < score.trajectory["planner_key_recall"] < 1
    assert score.trajectory["scope_violations"] == 0
    assert score.graph_run_id is None  # tracing off
    data = json.loads(report.to_json())
    assert data["trajectory"]["planner_key_precision"] == {"n": 1, "mean": 1.0}


async def test_feedback_goes_to_the_graph_run(fixture):  # noqa: F811
    client = MagicMock(spec=Client)
    with tracing_context(enabled=True, client=client, project_name="p"):
        report = await _evaluate(fixture, _script())
    time.sleep(0.05)
    (score,) = report.scores
    graph_runs = [
        c.kwargs for c in client.method_calls
        if c[0] == "create_run" and c.kwargs["name"] == f"womm:{CASE.scenario_id}"
    ]  # fmt: skip
    (graph_run,) = graph_runs
    assert score.graph_run_id == str(graph_run["id"])
    assert graph_run["extra"]["metadata"]["mode"] == "eval"
    assert graph_run["extra"]["metadata"]["case_id"] == CASE.case_id
    calls = client.create_feedback.call_args_list
    assert calls and {str(c.args[0]) for c in calls} == {score.graph_run_id}
    keys = {c.kwargs["key"] for c in calls}
    assert {"coverage", "grounding", "traj.planner_key_recall", "traj.scope_violations"} <= keys
    # The eval-case span carries the shared metadata too.
    (case_run,) = [c.kwargs for c in client.method_calls
                   if c[0] == "create_run" and c.kwargs["name"] == "womm:eval_case"]  # fmt: skip
    meta = case_run["extra"]["metadata"]
    assert meta["mode"] == "eval" and meta["repetition"] == 1 and meta["data_scope"] == "unscoped"


async def test_sealed_case_is_neither_traced_nor_scored_in_langsmith(fixture, monkeypatch):  # noqa: F811
    from womm.eval import run_eval

    monkeypatch.setattr(run_eval, "case_split", lambda case: "holdout")
    client = MagicMock(spec=Client)
    with tracing_context(enabled=True, client=client, project_name="p"):
        report = await _evaluate(fixture, _script())
    time.sleep(0.05)
    names = [c.kwargs["name"] for c in client.method_calls if c[0] == "create_run"]
    assert "womm:eval_case" not in names
    assert f"womm:{CASE.scenario_id}" not in names
    client.create_feedback.assert_not_called()
    assert report.scores[0].graph_run_id is None


async def test_experiment_metrics_include_trajectory(fixture, monkeypatch):  # noqa: F811
    import langsmith

    from womm.eval.run_eval import record_langsmith_experiment

    report = await _evaluate(fixture, _script())
    seen = {}

    async def fake_aevaluate(target, **kw):
        seen["metrics"] = kw["evaluators"][0](await target({"case_id": CASE.case_id}))
        return type("R", (), {"experiment_name": "exp"})()

    monkeypatch.setattr(langsmith, "aevaluate", fake_aevaluate)
    await record_langsmith_experiment(report, [CASE], _FakeClient(), prefix="t")
    keys = {r["key"] for r in seen["metrics"]["results"]}
    assert {f"traj.{k}" for k in TRAJECTORY_KEYS} <= keys
