"""Impact Planner node (R4).

Preset runs: the Planner reads the scenario's changes with their texts. Explore runs: it reads
the corpus index of the whole diff (no texts) with ``explore_prompt``, and its selection is
bounded by ``retrieval.max_provisions`` and ``retrieval.max_prompt_chars``.
"""

from __future__ import annotations

from collections.abc import Callable

from langgraph.runtime import Runtime
from pydantic import Field

from womm.graph import render
from womm.graph.experts import expert_user_content, expert_view
from womm.graph.state import RIAState, WommContext
from womm.llm.base import LLMError
from womm.models.base import StrictModel
from womm.models.system_version import DEFAULT_RETRIEVAL


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


def cap_plan(
    plan: FocusPlan, max_provisions: int, fits: Callable[[FocusPlan], bool]
) -> tuple[FocusPlan, list[str]]:
    """Bound an explore plan: keep the first ``max_provisions`` distinct keys in the Planner's
    order, then drop (in that order) every key whose addition would make ``fits`` false. Returns
    the bounded plan and one note per kind of drop."""
    keys = list(dict.fromkeys(k for a in plan.focus_areas for k in a.provision_keys))
    notes: list[str] = []
    kept = keys[:max_provisions]
    if len(keys) > max_provisions:
        notes.append(
            f"planner selected {len(keys)} provisions; kept the first {max_provisions} "
            f"(retrieval.max_provisions), dropped {', '.join(keys[max_provisions:])}"
        )
    chosen: list[str] = []
    over: list[str] = []
    for key in kept:
        if fits(plan.restrict_to({*chosen, key})):
            chosen.append(key)
        else:
            over.append(key)
    if over:
        notes.append(
            f"dropped {', '.join(over)}: an expert prompt would exceed retrieval.max_prompt_chars"
        )
    return plan.restrict_to(set(chosen)), notes


def _explore_fits(state: RIAState, ctx: WommContext, max_chars: int) -> Callable[[FocusPlan], bool]:
    """Whether every expert's prompt (its system prompt plus its own user content, rendered
    through its own scope) stays within ``max_chars`` for a candidate plan."""
    experts = [(len(ctx.prompt(e.role)), e) for e in ctx.sv.spec.experts]

    def fits(candidate: FocusPlan) -> bool:
        return all(
            system
            + len(expert_user_content(state, candidate, expert_view(state, e, ctx, candidate)))
            <= max_chars
            for system, e in experts
        )

    return fits


async def planner_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
    ctx = runtime.context
    role = ctx.sv.spec.planner
    explore = state.get("mode") == "explore"
    limits = ctx.sv.spec.retrieval or DEFAULT_RETRIEVAL
    if explore:
        # Versions without an explore prompt fall back to the preset prompt, so the prompt
        # actually sent is always part of the version's content address.
        system = ctx.sv.prompt_file(role.explore_prompt or role.prompt)
        user = render.explore_planner_input(
            state["index_header"], state["index_lines"], limits.max_provisions
        )
    else:
        system = ctx.prompt(role)
        user = "Regulatory changes:\n\n" + render.changes_with_text(state["diff"])
    try:
        plan, usage = await ctx.backend_for(role).call("planner", system, user, FocusPlan, role)
    except LLMError as exc:
        return {
            "fatal_error": f"planner failed: {exc}",
            "usage": [exc.usage] if exc.usage else [],
        }
    except Exception as exc:  # noqa: BLE001 - a backend bug must fail the run, not crash it
        return {"fatal_error": f"planner failed: [process_error] {type(exc).__name__}: {exc}"}
    focus = plan.restrict_to(set(state["diff"].keys()))
    if not explore:
        return {"focus": focus, "usage": [usage]}

    focus, notes = cap_plan(
        focus, limits.max_provisions, _explore_fits(state, ctx, limits.max_prompt_chars)
    )
    if not focus.focus_areas:
        # Experts must never run on the memorandum alone while told to analyse all changes.
        # The cap notes go to the dossier once, as planner notes, not again inside the error.
        reason = (
            "the caps dropped every selected provision key (see the dossier notes)"
            if notes
            else "the explore plan selected no provision keys from the index"
        )
        return {
            "fatal_error": f"planner failed: [no_provisions] {reason}",
            "usage": [usage],
            "planner_notes": notes,
        }
    return {"focus": focus, "usage": [usage], "planner_notes": notes}
