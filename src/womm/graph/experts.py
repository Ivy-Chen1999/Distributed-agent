"""Expert nodes (R5, R6). Each expert catches its own failures and reports them as data, so
one failing expert degrades the run instead of aborting the LangGraph superstep."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from langgraph.runtime import Runtime

from womm.diff import RegulatoryDiff
from womm.graph import render
from womm.graph.state import RIAState, WommContext
from womm.llm.base import LLMError
from womm.models.findings import ExpertFailure, FindingBatch, ImpactFinding, Provenance
from womm.models.regulation import Source
from womm.models.system_version import ExpertConfig

ExpertNode = Callable[[RIAState, Runtime[WommContext]], Awaitable[dict]]


def focus_keys(focus) -> list[str]:
    """The distinct provision keys of a FocusPlan, in the Planner's order."""
    return list(dict.fromkeys(k for a in focus.focus_areas for k in a.provision_keys))


def explore_view(state: RIAState, focus) -> tuple[RegulatoryDiff, list[Source]]:
    """What an expert reads in explore mode: only the changes the Planner selected, and only
    their texts plus the run's memorandum sources (if any), never the whole diff's texts.

    Until per-expert scopes are enforced, every expert gets this same view."""
    keys = set(focus_keys(focus)) if focus is not None else set()
    diff = state["diff"]
    changes = [c for c in diff.changes if c.provision_key in keys]
    ids = {p.source_id for c in changes for p in (c.before, c.after) if p is not None}
    sources = [s for sid, s in state["sources"].items() if sid in ids or s.kind == "memorandum"]
    return diff.model_copy(update={"changes": changes}), sources


def expert_user_content(state: RIAState, focus=None) -> str:
    """The expert's user content. Preset runs render the scenario's whole diff and sources
    (unchanged since v0); explore runs render the Planner's selection only. ``focus``
    overrides ``state["focus"]`` (the Planner sizes its selection with it)."""
    focus = focus if focus is not None else state.get("focus")
    if state.get("mode") == "explore":
        diff, sources = explore_view(state, focus)
        law = state["law_version"]
        head = f"Law analysed: {law.version_id} ({law.source}, {law.date}).\n"
        if law.note:
            head += law.note + "\n"
        head += "\n"
    else:
        diff, sources = state["diff"], list(state["sources"].values())
        head = ""
    return (
        head
        + "Regulatory changes (texts are in the sources below):\n"
        + render.changes_index(diff)
        + "\n\nImpact Planner focus areas:\n"
        + (focus.render() if focus else "(none)")
        + "\n\nCitable sources (quote only from these, verbatim):\n\n"
        + render.sources_block(sources)
    )


def make_expert_node(expert: ExpertConfig) -> ExpertNode:
    async def expert_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
        ctx = runtime.context
        role = expert.role
        prompt = ctx.prompt(role)
        try:
            batch, usage = await ctx.backend_for(role).call(
                "expert", prompt, expert_user_content(state), FindingBatch, role, agent=expert.id
            )
        except LLMError as exc:
            failure = ExpertFailure(
                agent=expert.id, error_kind=exc.error_kind, message=exc.message,
                attempts=exc.attempts,
            )  # fmt: skip
            return {
                "board": {expert.id: []},
                "failures": {expert.id: failure},
                "usage": [exc.usage] if exc.usage else [],
            }
        except Exception as exc:  # noqa: BLE001 - any expert bug must not abort the run
            failure = ExpertFailure(
                agent=expert.id, error_kind="process_error", message=f"{type(exc).__name__}: {exc}"
            )
            return {"board": {expert.id: []}, "failures": {expert.id: failure}}

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
        return {"board": {expert.id: findings}, "failures": {expert.id: None}, "usage": [usage]}

    expert_node.__name__ = f"expert_{expert.id}"
    return expert_node
