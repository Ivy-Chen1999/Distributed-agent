"""Tracing conventions: shared metadata, the sealed-split predicate, retriever documents and
feedback. A mock client stands in for LangSmith; nothing here needs the network."""

import datetime as dt
from unittest.mock import MagicMock

import pytest
from langsmith import Client, tracing_context

from womm import tracing
from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT
from womm.models.regulation import Source
from womm.models.run import CodeIdentity, RetrievalRecord
from womm.models.system_version import load_system_version

AT = dt.datetime(2026, 10, 4, tzinfo=dt.UTC)
CODE = CodeIdentity(git_sha="abc123", dirty=False, claude_cli_version="2.1.0")


@pytest.fixture(autouse=True)
def _no_env_tracing(monkeypatch):
    for var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY",
                "LANGCHAIN_API_KEY"):  # fmt: skip
        monkeypatch.delenv(var, raising=False)


def _sv(name=DEFAULT_SYSTEM_VERSION):
    return load_system_version(name, REPO_ROOT)


def test_is_sealed_is_the_holdout_predicate():
    assert tracing.is_sealed("holdout")
    for split in (None, "train", "val", "mixed"):
        assert not tracing.is_sealed(split)


def test_run_metadata_holds_every_convention_key():
    sv = _sv()
    meta = tracing.run_metadata(
        sv, scenario_id="eval_sme_impacts", mode="eval", code=CODE, case_id="case_02",
        split="train",
    )  # fmt: skip
    assert meta["system_version"] == sv.version_id
    assert meta["scenario_id"] == "eval_sme_impacts"
    assert meta["case_id"] == "case_02" and meta["split"] == "train"
    assert meta["mode"] == "eval"
    assert meta["backends"] == "claude_code"
    assert meta["router_mode"] == sv.spec.router.mode
    assert meta["router_decider"] == sv.spec.router.decider
    assert meta["git_sha"] == "abc123" and meta["git_dirty"] is False
    assert meta["claude_cli_version"] == "2.1.0"
    assert meta["data_scope"] == "unscoped"
    assert sv.spec.planner.model in meta["models"]
    tags = tracing.run_tags(meta)
    assert {"womm", f"sv:{sv.version_id}", "mode:eval", "scope:unscoped", "split:train"} <= set(
        tags
    )


def test_run_metadata_scoped_version_and_optional_fields():
    scoped = _sv(REPO_ROOT / "system_versions" / "v1.0-scoped.yaml")
    meta = tracing.run_metadata(scoped, scenario_id="s", mode="explore")
    assert meta["data_scope"] == "scoped"
    assert "case_id" not in meta and "split" not in meta and "git_sha" not in meta
    with pytest.raises(ValueError):
        tracing.run_metadata(_sv(), scenario_id="s", mode="production")


def test_tracing_disabled_without_env_or_client():
    assert not tracing.tracing_enabled()
    with tracing_context(enabled=True, client=MagicMock(spec=Client)):
        assert tracing.tracing_enabled()
    with tracing_context(enabled=False, client=MagicMock(spec=Client)):
        assert not tracing.tracing_enabled()


def test_traced_returns_the_plain_function_for_a_sealed_split():
    def fn():
        return 1

    assert tracing.traced(fn, split="holdout", name="x") is fn
    assert tracing.traced(fn, split="train", name="x") is not fn


def _records():
    return [
        RetrievalRecord(agent="fiscal", key="k/a", status="granted_text",
                        source_ids=["com2021_206/art_55"], at=AT),
        RetrievalRecord(agent="fiscal", key="k/b", status="out_of_scope", at=AT),
        RetrievalRecord(agent="fiscal", key="k/c", status="unknown_key", at=AT),
    ]  # fmt: skip


def _sources():
    return [
        Source(source_id="com2021_206/art_55", title="Art 55", kind="provision", text="T55"),
        Source(source_id="com2021_206/memorandum/context", title="M", kind="memorandum",
               text="memo"),
    ]  # fmt: skip


def test_retrieval_documents_render_grants_refusals_and_memorandum():
    docs = tracing.retrieval_documents(_records(), _sources())
    assert all(d["type"] == "Document" for d in docs)
    by_id = {d["metadata"].get("source_id") or d["metadata"]["key"]: d for d in docs}
    granted = by_id["com2021_206/art_55"]
    assert granted["page_content"] == "T55"
    assert granted["metadata"] == {
        "source_id": "com2021_206/art_55", "kind": "provision", "version": "com2021_206",
        "key": "k/a", "status": "granted_text", "granted": True,
    }  # fmt: skip
    memo = by_id["com2021_206/memorandum/context"]
    assert memo["metadata"]["status"] == "memorandum" and memo["metadata"]["granted"] is True
    refused = by_id["k/b"]
    assert refused["page_content"] == ""
    assert refused["metadata"]["granted"] is False
    assert refused["metadata"]["refusal_reason"] == "out_of_scope"
    assert by_id["k/c"]["metadata"]["refusal_reason"] == "unknown_key"


def test_send_feedback_posts_one_score_per_key():
    client = MagicMock(spec=Client)
    with tracing_context(enabled=True, client=client):
        n = tracing.send_feedback(
            "11111111-1111-1111-1111-111111111111",
            {"coverage": 0.5, "traj.scope_violations": 0, "traj.router_brier": None},
            split="train", trace_id="22222222-2222-2222-2222-222222222222", client=client,
        )  # fmt: skip
    assert n == 2
    keys = {c.kwargs["key"]: c for c in client.create_feedback.call_args_list}
    assert set(keys) == {"coverage", "traj.scope_violations"}
    call = keys["coverage"]
    assert str(call.args[0] if call.args else call.kwargs["run_id"]).startswith("1111")
    assert call.kwargs["score"] == 0.5
    assert str(call.kwargs["trace_id"]).startswith("2222")


def test_send_feedback_refuses_holdout_and_no_ops_without_tracing():
    client = MagicMock(spec=Client)
    with tracing_context(enabled=True, client=client):
        assert tracing.send_feedback("r", {"coverage": 1.0}, split="holdout", client=client) == 0
    assert tracing.send_feedback("r", {"coverage": 1.0}, split="train", client=client) == 0
    assert tracing.send_feedback(None, {"coverage": 1.0}, split="train", client=client) == 0
    client.create_feedback.assert_not_called()


def test_send_feedback_swallows_client_errors():
    client = MagicMock(spec=Client)
    client.create_feedback.side_effect = RuntimeError("network down")
    with tracing_context(enabled=True, client=client):
        assert tracing.send_feedback("r", {"coverage": 1.0}, split="train", client=client) == 0
