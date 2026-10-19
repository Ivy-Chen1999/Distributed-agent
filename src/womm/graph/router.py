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
    and the planner's focus areas. Full texts stay out to keep the bounded decision cheap.

    Explore runs cover a whole act, so the decider sees the index summary and the index lines
    of the Planner's selected provisions instead (no text)."""
    if state.get("mode") == "explore":
        return _explore_router_state(state)
    lines = [f"Scenario {state['scenario_id']}: {len(state['diff'].changes)} changed provisions."]
    for c in state["diff"].changes:
        p = c.after or c.before
        lines.append(f"- [{c.kind}] {c.provision_key} (Art {p.article}): {p.text[:300]}")
    focus = state.get("focus")
    lines.append("Planner focus areas:\n" + (focus.render() if focus else "(none)"))
    return "\n".join(lines)


def _explore_router_state(state: RIAState) -> str:
    focus = state.get("focus")
    keys = (
        list(dict.fromkeys(k for a in focus.focus_areas for k in a.provision_keys)) if focus else []
    )
    index = state.get("index_lines", {})
    lines = [
        f"Scenario {state['scenario_id']} (explore): {len(state['diff'].changes)} changed "
        f"provisions in the index; the Planner selected {len(keys)}.",
        state.get("index_header", ""),
        "Selected provisions (index lines, no text):",
        *(f"- {index.get(k, k)}" for k in keys),
        "Planner focus areas:\n" + (focus.render() if focus else "(none)"),
    ]
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
