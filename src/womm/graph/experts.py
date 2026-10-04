"""Expert nodes (R5, R6). Each expert catches its own failures and reports them as data, so
one failing expert degrades the run instead of aborting the LangGraph superstep.

Each expert resolves its requested provision keys through its own data scope (Layer 1,
``womm.retrieval.retrieve``) and reads only what that grants, plus the memorandum when it is
unscoped or its scope sets ``sees_memorandum``. Requested keys are the scenario's keys in
preset mode and the union of the Planner's focus keys in explore mode."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from langgraph.runtime import Runtime

from womm.graph import render
from womm.graph.state import RIAState, WommContext
from womm.llm.base import LLMError
from womm.models.findings import (
    NO_DATA_IN_SCOPE,
    ExpertFailure,
    FindingBatch,
    ImpactFinding,
    Provenance,
    no_data_note,
)
from womm.models.regulation import Source
from womm.models.run import RetrievalRecord
from womm.models.system_version import DataScope, ExpertConfig
from womm.retrieval import Retrieval, memorandum_sources, retrieve

ExpertNode = Callable[[RIAState, Runtime[WommContext]], Awaitable[dict]]


def focus_keys(focus) -> list[str]:
    """The distinct provision keys of a FocusPlan, in the Planner's order."""
    return list(dict.fromkeys(k for a in focus.focus_areas for k in a.provision_keys))


@dataclass(frozen=True)
class ExpertView:
    """What one expert reads and may cite: its retrieval through its scope, and its citable
    sources (retrieved sources plus the memorandum sources it is given)."""

    scope: DataScope | None
    retrieval: Retrieval
    sources: list[Source]

    @property
    def granted_keys(self) -> set[str]:
        return set(self.retrieval.granted_keys)

    @property
    def records_by_key(self) -> dict[str, RetrievalRecord]:
        return {r.key: r for r in self.retrieval.records}


def requested_keys(state: RIAState, focus) -> list[str]:
    """Preset: the scenario's provision keys. Explore: the union of the focus-area keys."""
    if state.get("mode") == "explore":
        return focus_keys(focus) if focus is not None else []
    return list(state.get("scenario_keys") or state["diff"].keys())


def expert_view(state: RIAState, expert: ExpertConfig, ctx: WommContext, focus=None) -> ExpertView:
    """Resolve the expert's requested keys through its scope. ``focus`` overrides
    ``state["focus"]`` (the Planner sizes its selection with it)."""
    focus = focus if focus is not None else state.get("focus")
    explore = state.get("mode") == "explore"
    scope = expert.scope
    diff = state["diff"]
    if explore:
        text_store = ctx.provision_corpus()
    elif ctx.fixture is not None:
        text_store = ctx.fixture
    else:
        raise RuntimeError("a preset run needs the fixture in the run context")
    corpus = ctx.provision_corpus() if scope is not None else ctx.corpus
    retrieval = retrieve(
        scope,
        requested_keys(state, focus),
        diff.before_version,
        diff.after_version,
        text_store,
        corpus,
        agent=expert.id,
    )
    memos = memorandum_sources(scope, state["sources"])
    if explore:
        # A run on the consolidated text marks the adopted texts of amended units (titles only).
        retrieval = Retrieval(
            sources=[
                ctx.provision_corpus().for_target(s, diff.after_version) for s in retrieval.sources
            ],
            records=retrieval.records,
        )
    if scope is None:
        # Unscoped (v0): the same sources in the same order as before scopes existed, i.e. the
        # run's source union order, restricted to what was retrieved, plus the memorandum.
        ids = {s.source_id for s in retrieval.sources} | {s.source_id for s in memos}
        sources = [s for sid, s in state["sources"].items() if sid in ids]
    else:
        sources = [*retrieval.sources, *memos]
    return ExpertView(scope=scope, retrieval=retrieval, sources=sources)


def _scoped_focus(focus, granted: set[str]) -> str:
    """Focus areas for a scoped expert: granted keys only, with neither the question nor the
    rationale (the Planner wrote both with full delta visibility, so either could carry the
    change kind or out-of-scope content into the expert's context)."""
    if focus is None:
        return "(none)"
    areas = [[k for k in a.provision_keys if k in granted] for a in focus.focus_areas]
    areas = [keys for keys in areas if keys]
    if not areas:
        return "(no focus area is within your data scope)"
    return "\n".join(f"{i}. [keys: {', '.join(keys)}]" for i, keys in enumerate(areas, 1))


def _explore_head(state: RIAState) -> str:
    law = state["law_version"]
    head = f"Law analysed: {law.version_id} ({law.source}, {law.date}).\n"
    if law.note:
        head += law.note + "\n"
    return head + "\n"


def expert_user_content(state: RIAState, focus=None, view: ExpertView | None = None) -> str:
    """The expert's user content.

    Unscoped experts (``view`` None or without a scope) see what they saw before scopes: preset
    runs render the scenario's whole diff and the view's sources (all scenario sources), explore
    runs the Planner's selection only. Scoped experts see only granted keys, without change
    kinds unless ``sees_delta``, and focus areas as keys only (no question, no rationale)."""
    focus = focus if focus is not None else state.get("focus")
    explore = state.get("mode") == "explore"
    head = _explore_head(state) if explore else ""
    diff = state["diff"]
    if view is not None and view.scope is not None:
        changes = render.scoped_changes_index(diff, view.records_by_key, view.scope.sees_delta)
        focus_text = _scoped_focus(focus, view.granted_keys)
        sources = view.sources
    else:
        if explore:
            keys = set(focus_keys(focus)) if focus is not None else set()
            diff = diff.model_copy(
                update={"changes": [c for c in diff.changes if c.provision_key in keys]}
            )
        if view is not None:
            sources = view.sources
        elif explore:
            ids = {p.source_id for c in diff.changes for p in (c.before, c.after) if p is not None}
            sources = [s for sid, s in state["sources"].items()
                       if sid in ids or s.kind == "memorandum"]  # fmt: skip
        else:
            sources = list(state["sources"].values())
        changes = render.changes_index(diff)
        focus_text = focus.render() if focus else "(none)"
    return (
        head
        + "Regulatory changes (texts are in the sources below):\n"
        + changes
        + "\n\nImpact Planner focus areas:\n"
        + focus_text
        + "\n\nCitable sources (quote only from these, verbatim):\n\n"
        + render.sources_block(sources)
    )


def _log(agent: str, view: ExpertView | None) -> dict:
    """The expert's citable sources and retrieval records, for every return path."""
    if view is None:
        return {"retrieved": {agent: {}}, "retrievals": {agent: []}}
    return {
        "retrieved": {agent: {s.source_id: s for s in view.sources}},
        "retrievals": {agent: list(view.retrieval.records)},
    }


def make_expert_node(expert: ExpertConfig) -> ExpertNode:
    async def expert_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
        ctx = runtime.context
        role = expert.role
        prompt = ctx.prompt(role)
        view: ExpertView | None = None
        try:
            view = expert_view(state, expert, ctx)
            if view.scope is not None and not view.granted_keys:
                # Nothing within its scope: no call (it could only answer from the memorandum,
                # and validation would reject every finding). Data, not an expert error.
                failure = ExpertFailure(
                    agent=expert.id, error_kind=NO_DATA_IN_SCOPE,
                    message=no_data_note(expert.id), attempts=0,
                )  # fmt: skip
                return {
                    "board": {expert.id: []},
                    "failures": {expert.id: failure},
                    **_log(expert.id, view),
                }
            batch, usage = await ctx.backend_for(role).call(
                "expert", prompt, expert_user_content(state, view=view), FindingBatch, role,
                agent=expert.id,
            )  # fmt: skip
        except LLMError as exc:
            failure = ExpertFailure(
                agent=expert.id, error_kind=exc.error_kind, message=exc.message,
                attempts=exc.attempts,
            )  # fmt: skip
            return {
                "board": {expert.id: []},
                "failures": {expert.id: failure},
                "usage": [exc.usage] if exc.usage else [],
                **_log(expert.id, view),
            }
        except Exception as exc:  # noqa: BLE001 - any expert bug must not abort the run
            failure = ExpertFailure(
                agent=expert.id, error_kind="process_error", message=f"{type(exc).__name__}: {exc}"
            )
            return {
                "board": {expert.id: []},
                "failures": {expert.id: failure},
                **_log(expert.id, view),
            }

        prov = Provenance(
            agent=expert.id,
            system_version=ctx.sv.version_id,
            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest()[:16],
            backend=role.backend,
            model=role.model,
        )
        findings = [
            ImpactFinding.from_draft(d, run_id=state["run_id"], index=i, provenance=prov)
            for i, d in enumerate(batch.findings)
        ]
        return {
            "board": {expert.id: findings},
            "failures": {expert.id: None},
            "usage": [usage],
            **_log(expert.id, view),
        }

    expert_node.__name__ = f"expert_{expert.id}"
    return expert_node
