"""Impact Planner node (R4)."""

from __future__ import annotations

from langgraph.runtime import Runtime
from pydantic import Field

from womm.graph import render
from womm.graph.state import RIAState, WommContext
from womm.llm.base import LLMError
from womm.models.base import StrictModel


class FocusArea(StrictModel):
    provision_keys: list[str]
    question: str
    rationale: str


class FocusPlan(StrictModel):
    focus_areas: list[FocusArea] = Field(description="2 to 6 focus areas.")

    def restrict_to(self, keys: set[str]) -> FocusPlan:
        """Drop keys not in the diff, then drop focus areas left with no keys."""
        areas = []
        for a in self.focus_areas:
            kept = [k for k in a.provision_keys if k in keys]
            if kept:
                areas.append(a.model_copy(update={"provision_keys": kept}))
        return FocusPlan(focus_areas=areas)

    def render(self) -> str:
        if not self.focus_areas:
            return "(no focus areas: analyse all changes)"
        return "\n".join(
            f"{i}. {a.question} [keys: {', '.join(a.provision_keys)}] — {a.rationale}"
            for i, a in enumerate(self.focus_areas, 1)
        )


async def planner_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
    ctx = runtime.context
    role = ctx.sv.spec.planner
    user = "Regulatory changes:\n\n" + render.changes_with_text(state["diff"])
    try:
        plan, usage = await ctx.backend_for(role).call(
            "planner", ctx.prompt(role), user, FocusPlan, role
        )
    except LLMError as exc:
        return {
            "fatal_error": f"planner failed: {exc}",
            "usage": [exc.usage] if exc.usage else [],
        }
    except Exception as exc:  # noqa: BLE001 - a backend bug must fail the run, not crash it
        return {"fatal_error": f"planner failed: [process_error] {type(exc).__name__}: {exc}"}
    return {"focus": plan.restrict_to(set(state["diff"].keys())), "usage": [usage]}
