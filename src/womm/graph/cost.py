"""The cost node (EU cost plan U3): runs after ``validate``, only in cost-enabled versions.

It reads the corpus obligation records of the run's provisions (the scenario keys in preset
mode, the Planner's keys in explore mode, restricted to changed keys when the run has a before
version) and writes ``state["cost"]``. It never reads or writes the board, the findings or the
synthesis input, so the dossier impacts and the judge input are the same with or without it.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from womm.cost.estimate import estimate_costs, relevant_records
from womm.cost.hotspots import build_section
from womm.data.corpus import Corpus
from womm.graph.experts import requested_keys
from womm.graph.state import RIAState, WommContext
from womm.models.cost import CostSection
from womm.models.regulation import Source
from womm.retrieval import obligations_source


def cost_sources(section: CostSection, corpus: Corpus, after_version: str) -> list[Source]:
    """The ``full`` obligation views the section's records cite, one per provision key."""
    out = []
    for key in dict.fromkeys(r.provision_key for r in section.records):
        try:
            out.append(obligations_source(corpus, after_version, key, "full"))
        except ValueError:
            continue
    return out


def cost_keys(state: RIAState) -> list[str]:
    keys = requested_keys(state, state.get("focus"))
    diff = state["diff"]
    if diff.before_version is not None:
        changed = set(diff.keys())
        keys = [k for k in keys if k in changed]
    return keys


async def cost_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
    ctx = runtime.context
    role = ctx.sv.spec.cost
    if role is None:  # the graph adds this node only when the version has a cost role
        return {}
    relevant = relevant_records(
        ctx.provision_corpus(), state["diff"].after_version, cost_keys(state)
    )
    est = await estimate_costs(
        relevant.items,
        backend=ctx.backend_for(role),
        role=role,
        prompt=ctx.prompt(role),
        max_parallel=ctx.sv.spec.max_parallel_llm_calls,
    )
    section = build_section(est.records, relevant.not_covered, invalid=est.invalid, notes=est.notes)
    return {"cost": section, "usage": est.usage}
