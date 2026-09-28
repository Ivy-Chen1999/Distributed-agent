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
        return {}
    context = (
        f"Scenario {state['scenario_id']}: {len(state['diff'].changes)} changed provisions: "
        + ", ".join(state["diff"].keys())
    )
    records: list[DecisionRecord] = await ctx.decisions.expert_relevance(
        ctx.sv.spec.experts, context, ctx.sv
    )
    return {"decisions": records}


def dispatch(state: RIAState, runtime: Runtime[WommContext]) -> list[Send]:
    ctx = runtime.context
    experts = ctx.sv.spec.experts
    if ctx.sv.spec.router.mode == "active":
        relevant = {
            d.subject
            for d in state.get("decisions", [])
            if d.decision_point == "router.relevance" and d.decision != "not_relevant"
        }
        experts = [e for e in experts if e.id in relevant] or experts
    return [Send(expert_node_name(e.id), state) for e in experts]
