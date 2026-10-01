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
    context = router_state(state)
    records: list[DecisionRecord] = await ctx.decisions.expert_relevance(
        ctx.sv.spec.experts, context, ctx.sv
    )
    return {"decisions": records, "dispatched": selected_experts(ctx, records)}


def router_state(state: RIAState) -> str:
    """What the decider sees: the changed provisions (key, change kind, article, opening text)
    and the planner's focus areas. Full texts stay out to keep the bounded decision cheap."""
    lines = [f"Scenario {state['scenario_id']}: {len(state['diff'].changes)} changed provisions."]
    for c in state["diff"].changes:
        p = c.after or c.before
        lines.append(f"- [{c.kind}] {c.provision_key} (Art {p.article}): {p.text[:300]}")
    focus = state.get("focus")
    lines.append("Planner focus areas:\n" + (focus.render() if focus else "(none)"))
    return "\n".join(lines)


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
