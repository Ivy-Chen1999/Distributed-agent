"""Graph runs under a mock LangSmith client: retriever spans per expert, shared root metadata,
the root run id on the RunResult, the Planner's focus persisted, and holdout isolation."""

import time
import uuid
from unittest.mock import MagicMock

import pytest
from langsmith import Client, tracing_context

from womm.decisions.stub import StubDecisionService
from womm.graph.build import run_scenario
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity, RunResult

from .conftest import K55, SCENARIO, fake_sv, good_script

CODE = CodeIdentity(git_sha="sha1", dirty=False)


@pytest.fixture(autouse=True)
def _no_env_tracing(monkeypatch):
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY",
                "LANGCHAIN_API_KEY"):  # fmt: skip
        monkeypatch.delenv(var, raising=False)


async def _run(fixture, *, sv=None, **kw):
    return await run_scenario(
        SCENARIO, sv=sv or fake_sv(), fixture=fixture,
        backends={"fake": FakeBackend(good_script())}, decisions=StubDecisionService(),
        code_identity=CODE, run_id="run_t", **kw,
    )  # fmt: skip


def _created(client) -> list[dict]:
    time.sleep(0.05)  # LangGraph's tracer posts from a background thread
    return [c.kwargs for c in client.method_calls if c[0] == "create_run"]


def _updated(client) -> dict:
    return {str(c.kwargs["run_id"]): c.kwargs for c in client.method_calls if c[0] == "update_run"}


async def test_untraced_run_has_no_trace_id_but_keeps_planner_focus(fixture):
    result = await _run(fixture)
    assert result.trace_run_id is None
    assert result.planner is not None
    assert result.planner.keys == [K55]
    assert result.planner.areas == [[K55]]
    assert result.planner.dispatched == ["legal", "fiscal", "stakeholder"]


async def test_root_run_metadata_and_id(fixture):
    client = MagicMock(spec=Client)
    with tracing_context(enabled=True, client=client, project_name="p"):
        result = await _run(fixture)
    runs = _created(client)
    (root,) = [r for r in runs if r.get("parent_run_id") is None]
    assert str(root["id"]) == result.trace_run_id
    uuid.UUID(result.trace_run_id)
    meta = root["extra"]["metadata"]
    assert meta["system_version"] == result.system_version
    assert meta["scenario_id"] == SCENARIO and meta["mode"] == "demo"
    assert meta["backends"] == "fake" and meta["data_scope"] == "unscoped"
    assert meta["git_sha"] == "sha1" and meta["run_id"] == "run_t"
    assert "mode:demo" in root["tags"] and "scope:unscoped" in root["tags"]


async def test_eval_mode_metadata_passes_through(fixture):
    client = MagicMock(spec=Client)
    with tracing_context(enabled=True, client=client, project_name="p"):
        await _run(fixture, run_mode="eval", case_id="case_02", split="train")
    (root,) = [r for r in _created(client) if r.get("parent_run_id") is None]
    meta = root["extra"]["metadata"]
    assert meta["mode"] == "eval" and meta["case_id"] == "case_02" and meta["split"] == "train"


async def test_one_retriever_span_per_expert_with_documents(fixture):
    client = MagicMock(spec=Client)
    with tracing_context(enabled=True, client=client, project_name="p"):
        await _run(fixture)
    runs = _created(client)
    retrievers = [r for r in runs if r["run_type"] == "retriever"]
    assert sorted(r["name"] for r in retrievers) == [
        "retrieval:fiscal", "retrieval:legal", "retrieval:stakeholder"
    ]  # fmt: skip
    updates = _updated(client)
    for r in retrievers:
        docs = updates[str(r["id"])]["outputs"]["documents"]
        assert docs and all(d["type"] == "Document" for d in docs)
        granted = [d for d in docs if d["metadata"]["status"] == "granted_text"]
        assert granted and all(d["page_content"] for d in granted)
        assert {d["metadata"]["version"] for d in granted} == {"com2021_206"}
        # Inputs stay small: keys and scope, never the graph state or source texts.
        assert set(r["inputs"]) == {"agent", "keys", "scope"}


async def test_holdout_run_posts_nothing(fixture):
    client = MagicMock(spec=Client)
    with tracing_context(enabled=True, client=client, project_name="p"):
        result = await _run(fixture, run_mode="eval", case_id="h1", split="holdout")
    assert _created(client) == []
    assert result.trace_run_id is None
    client.create_feedback.assert_not_called()


def test_old_run_json_without_new_fields_still_loads():
    old = {
        "run_id": "r", "scenario_id": "s", "status": "succeeded", "system_version": "sv_x",
        "code_identity": {"git_sha": None, "dirty": False},
    }  # fmt: skip
    run = RunResult.model_validate(old)
    assert run.planner is None and run.trace_run_id is None
