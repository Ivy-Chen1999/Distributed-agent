"""Citation validation and Synthesis nodes (R8, R9)."""

from __future__ import annotations

from langgraph.runtime import Runtime

from womm.citations import validate_findings
from womm.graph import render
from womm.graph.state import RIAState, WommContext
from womm.llm.base import LLMError
from womm.models.dossier import SynthesisPlan


def board_findings(state: RIAState, runtime: Runtime[WommContext]) -> list:
    """Board contents in the configured expert order."""
    board = state.get("board", {})
    return [f for e in runtime.context.sv.spec.experts for f in board.get(e.id, [])]


async def validate_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
    findings = board_findings(state, runtime)
    outcome = validate_findings(findings, state["sources"], set(state["diff"].keys()))
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
