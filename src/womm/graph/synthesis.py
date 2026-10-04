"""Citation validation and Synthesis nodes (R8, R9)."""

from __future__ import annotations

from langgraph.runtime import Runtime

from womm.citations import AgentSources, validate_findings
from womm.graph import render
from womm.graph.state import RIAState, WommContext
from womm.llm.base import LLMError
from womm.models.dossier import SynthesisPlan


def board_findings(state: RIAState, runtime: Runtime[WommContext]) -> list:
    """Board contents in the configured expert order."""
    board = state.get("board", {})
    return [f for e in runtime.context.sv.spec.experts for f in board.get(e.id, [])]


async def validate_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
    """Per-agent validation (R11): a finding counts only against its own agent's granted keys
    and citable sources."""
    findings = board_findings(state, runtime)
    records = state.get("retrievals", {})
    per_agent = {
        agent: AgentSources(
            granted_keys=frozenset(r.key for r in records.get(agent, []) if r.granted),
            sources=sources,
        )
        for agent, sources in state.get("retrieved", {}).items()
    }
    outcome = validate_findings(findings, state["sources"], set(state["diff"].keys()), per_agent)
    return {"validation": outcome}


def all_experts_failed(state: RIAState, runtime: Runtime[WommContext]) -> bool:
    """True when every dispatched expert failed (in active mode, only a subset is dispatched)."""
    failures = state.get("failures", {})
    return all(i in failures for i in dispatched_ids(state, runtime))


def dispatched_ids(state: RIAState, runtime: Runtime[WommContext]) -> list[str]:
    return state.get("dispatched") or [e.id for e in runtime.context.sv.spec.experts]


async def synthesis_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
    ctx = runtime.context
    supported = state["validation"].supported
    if not supported:
        return {"synthesis": SynthesisPlan(
            impacts=[], chains=[], disagreements=[], open_questions=[], discarded=[]
        )}  # fmt: skip
    role = ctx.sv.spec.synthesis
    user = "Validated findings:\n" + render.findings_for_synthesis(supported)
    try:
        plan, usage = await ctx.backend_for(role).call(
            "synthesis", ctx.prompt(role), user, SynthesisPlan, role
        )
    except LLMError as exc:
        return {
            "synthesis": None,
            "synthesis_error": str(exc),
            "usage": [exc.usage] if exc.usage else [],
        }
    except Exception as exc:  # noqa: BLE001 - degrade to unmerged findings instead of crashing
        return {
            "synthesis": None,
            "synthesis_error": f"[process_error] {type(exc).__name__}: {exc}",
        }
    return {"synthesis": plan, "usage": [usage]}
