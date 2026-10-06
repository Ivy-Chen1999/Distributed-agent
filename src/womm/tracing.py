"""LangSmith tracing conventions shared by graph runs and eval runs.

- ``run_metadata`` / ``run_tags``: one set of metadata keys and tags on every root run, so traces
  can be filtered by system version, scenario, case, split, mode, backend, model, router mode,
  git sha and data scope. LangSmith propagates them to child runs.
- ``is_sealed``: the single predicate every helper here checks. Holdout material never reaches
  LangSmith: sealed spans are not traced and sealed feedback is never sent.
- ``trace_retrieval``: an expert's Layer 1 retrieval as a ``retriever`` run, whose outputs are
  LangSmith documents (``page_content``, ``type: "Document"``, ``metadata``), so the trace UI
  renders what each expert was granted and refused.
- ``send_feedback``: scores attached to an existing run with ``Client.create_feedback``.

Everything is a no-op when tracing is off (no ``LANGSMITH_TRACING`` / API key, or an enclosing
``tracing_context(enabled=False)``), so tests and local runs never touch the network.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal

from langsmith import traceable
from langsmith import utils as ls_utils
from langsmith.run_helpers import get_tracing_context

from womm.models.regulation import Source
from womm.models.run import CodeIdentity, RetrievalRecord
from womm.models.system_version import SystemVersion

SEALED_SPLITS = frozenset({"holdout"})
RunMode = Literal["eval", "explore", "demo"]
RUN_MODES: tuple[str, ...] = ("eval", "explore", "demo")


def is_sealed(split: str | None) -> bool:
    """True when material of this split must never be traced or scored in LangSmith."""
    return split in SEALED_SPLITS


def tracing_enabled() -> bool:
    """Whether a span or feedback would actually be sent.

    An enclosing ``tracing_context(enabled=False)`` always wins. A context that enables tracing
    with its own client (tests) counts as enabled. Otherwise tracing needs both the LangSmith
    tracing switch and an API key."""
    ctx = get_tracing_context()
    if ctx.get("enabled") is False:
        return False
    if ctx.get("enabled") and ctx.get("client") is not None:
        return True
    key = os.environ.get("LANGSMITH_API_KEY") or os.environ.get("LANGCHAIN_API_KEY")
    return bool(ls_utils.tracing_is_enabled() and key)


def data_scope(sv: SystemVersion) -> str:
    """``scoped`` when any expert has a data scope, else ``unscoped``."""
    return "scoped" if any(e.scope is not None for e in sv.spec.experts) else "unscoped"


def run_metadata(
    sv: SystemVersion,
    *,
    scenario_id: str,
    mode: str,
    code: CodeIdentity | None = None,
    case_id: str | None = None,
    split: str | None = None,
) -> dict[str, Any]:
    """The metadata every WOMM root run carries. Optional keys are left out when unknown, so a
    filter on them never matches a placeholder."""
    if mode not in RUN_MODES:
        raise ValueError(f"run mode must be one of {RUN_MODES}, got {mode!r}")
    roles = sv.spec.roles()
    meta: dict[str, Any] = {
        "system_version": sv.version_id,
        "scenario_id": scenario_id,
        "mode": mode,
        "backends": ",".join(sorted({r.backend for r in roles.values()})),
        "models": ",".join(sorted({r.model for r in roles.values()})),
        "router_mode": sv.spec.router.mode,
        "router_decider": sv.spec.router.decider,
        "data_scope": data_scope(sv),
    }
    if case_id is not None:
        meta["case_id"] = case_id
    if split is not None:
        meta["split"] = split
    if code is not None:
        meta["git_sha"] = code.git_sha
        meta["git_dirty"] = code.dirty
        if code.claude_cli_version:
            meta["claude_cli_version"] = code.claude_cli_version
    return meta


def run_tags(meta: Mapping[str, Any]) -> list[str]:
    """Short tags derived from ``run_metadata`` for the LangSmith run list."""
    tags = ["womm", f"sv:{meta['system_version']}", f"mode:{meta['mode']}",
            f"scope:{meta['data_scope']}", f"router:{meta['router_mode']}"]  # fmt: skip
    if "split" in meta:
        tags.append(f"split:{meta['split']}")
    if "case_id" in meta:
        tags.append(meta["case_id"])
    return tags


def traced[F: Callable[..., Any]](fn: F, *, split: str | None, **traceable_kwargs: Any) -> F:
    """``traceable(**kwargs)(fn)``, or ``fn`` itself for a sealed split."""
    if is_sealed(split):
        return fn
    return traceable(**traceable_kwargs)(fn)


# ---------------------------------------------------------------- retrieval spans


def _version(source_id: str) -> str:
    return source_id.split("/", 1)[0]


def retrieval_documents(
    records: Iterable[RetrievalRecord], sources: Iterable[Source]
) -> list[dict[str, Any]]:
    """LangSmith retriever documents for one expert: every citable source it was given (granted
    texts and obligation views, then the memorandum), then one empty document per refused key."""
    records = list(records)
    key_of: dict[str, RetrievalRecord] = {}
    for r in records:
        for sid in r.source_ids:
            key_of.setdefault(sid, r)
    docs: list[dict[str, Any]] = []
    for s in sources:
        record = key_of.get(s.source_id)
        meta: dict[str, Any] = {"source_id": s.source_id, "kind": s.kind,
                                "version": _version(s.source_id)}  # fmt: skip
        if record is not None:
            meta |= {"key": record.key, "status": record.status, "granted": True}
        else:
            meta |= {"status": "memorandum" if s.kind == "memorandum" else "given",
                     "granted": True}  # fmt: skip
        docs.append({"page_content": s.text, "type": "Document", "metadata": meta})
    for r in records:
        if not r.granted:
            docs.append({
                "page_content": "",
                "type": "Document",
                "metadata": {"key": r.key, "status": r.status, "granted": False,
                             "refusal_reason": r.status},
            })  # fmt: skip
    return docs


def _retrieval_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Only small, text-free inputs on the retriever span (never the whole graph state)."""
    return {"agent": inputs.get("agent"), "keys": inputs.get("keys"),
            "scope": inputs.get("scope")}  # fmt: skip


def trace_retrieval[F: Callable[..., Any]](fn: F, *, agent: str, split: str | None) -> F:
    """Wrap ``fn(agent=..., keys=..., scope=...) -> ExpertView`` as a ``retriever`` run named
    ``retrieval:<agent>``. Untraced for a sealed split."""

    def outputs(view: Any) -> dict[str, Any]:
        if view is None:
            return {"documents": []}
        return {"documents": retrieval_documents(view.retrieval.records, view.sources)}

    return traced(
        fn, split=split, run_type="retriever", name=f"retrieval:{agent}",
        process_inputs=_retrieval_inputs, process_outputs=outputs,
    )  # fmt: skip


# ---------------------------------------------------------------- feedback


def send_feedback(
    run_id: str | None,
    scores: Mapping[str, float | int | bool | None],
    *,
    split: str | None,
    trace_id: str | None = None,
    client: Any | None = None,
    comment: str | None = None,
) -> int:
    """Attach each non-None score to ``run_id`` as feedback; returns how many were sent.

    Refused for a sealed split, and a no-op without a run id or with tracing off. Feedback is
    best effort: a client error never fails an eval."""
    if is_sealed(split) or run_id is None or not tracing_enabled():
        return 0
    if client is None:
        client = get_tracing_context().get("client")
    if client is None:
        from langsmith.run_trees import get_cached_client

        client = get_cached_client()
    sent = 0
    for key, score in scores.items():
        if score is None:
            continue
        with contextlib.suppress(Exception):
            client.create_feedback(run_id, key=key, score=score, trace_id=trace_id, comment=comment)
            sent += 1
    return sent
