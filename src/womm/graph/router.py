"""Router node (R10): asks the decision service which experts are relevant, then dispatches.

In shadow (and off) mode every expert runs regardless of the decision; only 'active' mode
enforces it.
"""

from __future__ import annotations

from langgraph.runtime import Runtime
from langgraph.types import Send

from womm.graph.state import RIAState, WommContext
from womm.models.decisions import DecisionRecord


def expert_node_name(expert_id: str) -> str:
    return f"expert_{expert_id}"


async def router_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
    ctx = runtime.context
    if ctx.sv.spec.router.mode == "off":
        return {"dispatched": [e.id for e in ctx.sv.spec.experts]}
    context = (
        f"Scenario {state['scenario_id']}: {len(state['diff'].changes)} changed provisions: "
        + ", ".join(state["diff"].keys())
    )
    records: list[DecisionRecord] = await ctx.decisions.expert_relevance(
        ctx.sv.spec.experts, context, ctx.sv
    )
    return {"decisions": records, "dispatched": selected_experts(ctx, records)}


def selected_experts(ctx: WommContext, records: list[DecisionRecord]) -> list[str]:
    """Shadow/off: every expert. Active: the relevant ones (all, if none is relevant)."""
    ids = [e.id for e in ctx.sv.spec.experts]
    if ctx.sv.spec.router.mode != "active":
        return ids
    relevant = {
        d.subject
        for d in records
        if d.decision_point == "router.relevance" and d.decision != "not_relevant"
    }
    return [i for i in ids if i in relevant] or ids


def dispatch(state: RIAState, runtime: Runtime[WommContext]) -> list[Send]:
    return [Send(expert_node_name(i), state) for i in state["dispatched"]]
