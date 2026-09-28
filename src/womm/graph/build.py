"""Build the RIA graph from a SystemVersion and run a scenario through it (F1)."""

from __future__ import annotations

import uuid
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT
from womm.data.fixtures import Fixture
from womm.decisions.service import DecisionService
from womm.diff import diff_versions
from womm.graph.assemble import assemble_node
from womm.graph.experts import make_expert_node
from womm.graph.planner import planner_node
from womm.graph.router import dispatch, expert_node_name, router_node
from womm.graph.state import RIAState, WommContext
from womm.graph.synthesis import all_experts_failed, synthesis_node, validate_node
from womm.llm.base import LLMBackend
from womm.models.run import CodeIdentity, GroundingStats, RunResult, RunStatus
from womm.models.system_version import SystemVersion, load_system_version


def _after_start(state: RIAState) -> str:
    return "assemble" if state["diff"].is_empty else "planner"


def _after_planner(state: RIAState) -> str:
    return "assemble" if state.get("fatal_error") else "router"


def _after_validate(state: RIAState, runtime: Runtime[WommContext]) -> str:
    return "assemble" if all_experts_failed(state, runtime) else "synthesis"


def build_graph(sv: SystemVersion) -> CompiledStateGraph:
    g = StateGraph(RIAState, context_schema=WommContext)
    g.add_node("planner", planner_node)
    g.add_node("router", router_node)
    expert_nodes = []
    for expert in sv.spec.experts:
        name = expert_node_name(expert.id)
        expert_nodes.append(name)
        g.add_node(name, make_expert_node(expert))
        g.add_edge(name, "validate")
    g.add_node("validate", validate_node)
    g.add_node("synthesis", synthesis_node)
    g.add_node("assemble", assemble_node)

    g.add_conditional_edges(START, _after_start, ["planner", "assemble"])
    g.add_conditional_edges("planner", _after_planner, ["router", "assemble"])
    g.add_conditional_edges("router", dispatch, expert_nodes)
    g.add_conditional_edges("validate", _after_validate, ["synthesis", "assemble"])
    g.add_edge("synthesis", "assemble")
    g.add_edge("assemble", END)
    return g.compile(name="womm-ria")


def make_graph() -> CompiledStateGraph:
    """Factory for langgraph.json / LangGraph Studio (default system version)."""
    return build_graph(load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT))


_STATUS = {
    "succeeded": RunStatus.succeeded,
    "degraded": RunStatus.degraded,
    "failed": RunStatus.failed,
    "no_changes": RunStatus.no_changes,
}


async def run_scenario(
    scenario_id: str,
    *,
    sv: SystemVersion,
    fixture: Fixture,
    backends: dict[str, LLMBackend],
    decisions: DecisionService,
    code_identity: CodeIdentity,
    run_id: str | None = None,
    repo_root: Path = REPO_ROOT,
    tags: list[str] | None = None,
) -> RunResult:
    run_id = run_id or f"run_{uuid.uuid4()}"
    scenario = fixture.scenario(scenario_id)
    before, after = fixture.scenario_versions(scenario_id)
    diff = diff_versions(before, after, keys=scenario.provision_keys)
    sources = {s.source_id: s for s in fixture.scenario_sources(scenario_id)}

    ctx = WommContext(sv=sv, backends=backends, decisions=decisions, repo_root=repo_root)
    graph = build_graph(sv)
    final = await graph.ainvoke(
        {"run_id": run_id, "scenario_id": scenario_id, "diff": diff, "sources": sources},
        context=ctx,
        config={
            "run_name": f"womm:{scenario_id}",
            "tags": ["womm", *(tags or [])],
            "metadata": {
                "run_id": run_id,
                "scenario_id": scenario_id,
                "system_version": sv.version_id,
                "git_sha": code_identity.git_sha,
                "git_dirty": code_identity.dirty,
                "claude_cli_version": code_identity.claude_cli_version,
            },
            "max_concurrency": sv.spec.max_parallel_llm_calls + 2,
        },
    )

    dossier = final["dossier"]
    validation = final.get("validation")
    return RunResult(
        run_id=run_id,
        scenario_id=scenario_id,
        status=_STATUS[dossier.status],
        system_version=sv.version_id,
        code_identity=code_identity,
        dossier=dossier,
        board=[f for e in sv.spec.experts for f in final.get("board", {}).get(e.id, [])],
        failures=list(final.get("failures", {}).values()),
        decisions=final.get("decisions", []),
        grounding=validation.report.grounding if validation else GroundingStats(),
        usage=[u for u in final.get("usage", []) if u is not None],
        error=final.get("fatal_error"),
    )
