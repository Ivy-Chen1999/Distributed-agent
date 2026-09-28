"""Expert nodes (R5, R6). Each expert catches its own failures and reports them as data, so
one failing expert degrades the run instead of aborting the LangGraph superstep."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from langgraph.runtime import Runtime

from womm.graph import render
from womm.graph.state import RIAState, WommContext
from womm.llm.base import LLMError
from womm.models.findings import ExpertFailure, FindingBatch, ImpactFinding, Provenance
from womm.models.system_version import ExpertConfig

ExpertNode = Callable[[RIAState, Runtime[WommContext]], Awaitable[dict]]


def expert_user_content(state: RIAState) -> str:
    focus = state.get("focus")
    return (
        "Regulatory changes (texts are in the sources below):\n"
        + render.changes_index(state["diff"])
        + "\n\nImpact Planner focus areas:\n"
        + (focus.render() if focus else "(none)")
        + "\n\nCitable sources (quote only from these, verbatim):\n\n"
        + render.sources_block(list(state["sources"].values()))
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
