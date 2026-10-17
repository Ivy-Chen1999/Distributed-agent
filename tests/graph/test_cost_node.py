"""The cost node in the graph (EU cost plan U3): it reads obligation records only, writes a
separate dossier section, and leaves the board, the impacts and the judge input untouched."""

import pytest

from womm.config import REPO_ROOT
from womm.decisions.stub import StubDecisionService
from womm.eval.evaluators import judge_input
from womm.eval.golden import GOLDEN_DIR, load_golden
from womm.graph.build import build_graph, run_scenario
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend, fake_cost_batch
from womm.models.findings import stable_id
from womm.models.run import CodeIdentity, RunStatus
from womm.models.system_version import build_system_version, load_system_version

SCENARIO = "eval_provider_compliance_costs"
K10 = "ai_act/high_risk/data_governance"
SV_DIR = REPO_ROOT / "system_versions"


def _fake(name: str, *, cost: bool):
    base = load_system_version(SV_DIR / name, REPO_ROOT).spec
    data = base.model_dump()
    for role in ("planner", "synthesis", "judge"):
        data[role]["backend"] = "fake"
    for e in data["experts"]:
        e["role"]["backend"] = "fake"
    if cost:
        data["cost"]["backend"] = "fake"
    else:
        data["cost"] = None
    return build_system_version(type(base).model_validate(data), REPO_ROOT)


def _quote(fixture) -> str:
    text = fixture.sources["com2021_206/art_10"].text
    return " ".join(text.split()[:14])


def _script(fixture, cost_steps=None) -> dict:
    fid = stable_id("f", "run_test", "fiscal", "0")
    finding = {
        "provision_key": K10, "affected_actor": "providers", "impact": "data costs",
        "mechanism": "data governance duties",
        "evidence": [{"source_id": "com2021_206/art_10", "quote": _quote(fixture)}],
        "confidence": 0.8,
    }  # fmt: skip
    area = {"provision_keys": [K10], "question": "q", "rationale": "r"}
    script = {
        "planner": [{"focus_areas": [area]}],
        "expert/legal": [{"findings": []}],
        "expert/fiscal": [{"findings": [finding]}],
        "expert/stakeholder": [{"findings": []}],
        "synthesis": [{
            "impacts": [{"impact_id": "I1", "summary": "s", "finding_ids": [fid]}],
            "chains": [], "disagreements": [], "open_questions": [], "discarded": [],
        }],
    }  # fmt: skip
    if cost_steps is not None:
        script["cost"] = cost_steps
    return script


@pytest.fixture
def run_case(fixture):
    async def _run(sv, script, on_event=None):
        backend = FakeBackend(script)
        result = await run_scenario(
            SCENARIO, sv=sv, fixture=fixture, backends={"fake": backend},
            decisions=StubDecisionService(),
            code_identity=CodeIdentity(git_sha="test", dirty=False), run_id="run_test",
            on_event=on_event,
        )  # fmt: skip
        return result, backend

    return _run


def test_the_graph_has_a_cost_node_only_when_the_version_sets_one():
    with_cost = build_graph(_fake("v1.0-cost.yaml", cost=True))
    without = build_graph(_fake("v1.0-cost.yaml", cost=False))
    assert "cost" in with_cost.get_graph().nodes
    assert "cost" not in without.get_graph().nodes
    unscoped = build_graph(load_system_version(SV_DIR / "v1.0-unscoped.yaml", REPO_ROOT))
    assert set(unscoped.get_graph().nodes) == set(without.get_graph().nodes)


async def test_case01_on_the_cost_version_gives_one_record_per_relevant_duty(run_case, fixture):
    result, backend = await run_case(
        _fake("v1.0-cost.yaml", cost=True), _script(fixture, [fake_cost_batch] * 5)
    )
    assert result.status == RunStatus.succeeded
    section = result.dossier.costs
    assert section is not None and len(section.records) == 103
    for rec in section.records:
        assert rec.source_id.startswith("com2021_206/obligations/")
        assert rec.unit_id.startswith("52021PC0206:")
    assert section.coverage.relevant == 103 and section.coverage.not_covered_keys == []
    assert any(u.role == "cost" for u in result.usage)
    citable = {s.source_id: s for s in result.citable_sources}
    for rec in section.records:
        assert f"[{rec.obligation_id}]" in citable[rec.source_id].text
    cost_calls = [c for c in backend.calls if c.role_name == "cost"]
    assert len(cost_calls) == 2


async def test_cost_role_leaves_board_impacts_and_judge_input_identical(run_case, fixture):
    case = load_golden(GOLDEN_DIR / "case_01_provider_compliance_costs.yaml")
    with_cost, _ = await run_case(
        _fake("v1.0-cost.yaml", cost=True), _script(fixture, [fake_cost_batch] * 5)
    )
    without, _ = await run_case(_fake("v1.0-cost.yaml", cost=False), _script(fixture))
    assert with_cost.dossier.costs is not None and without.dossier.costs is None
    # The two versions differ by id only; provenance names it, everything else is identical.
    sv_ids = (with_cost.system_version, without.system_version)

    def same(x):
        return x.model_dump_json().replace(sv_ids[0], "SV").replace(sv_ids[1], "SV")

    assert [same(f) for f in with_cost.board] == [same(f) for f in without.board]
    assert [same(i) for i in with_cost.dossier.impacts] == [
        same(i) for i in without.dossier.impacts
    ]
    assert judge_input(case, with_cost) == judge_input(case, without)
    assert with_cost.dossier.impacts  # the comparison is not vacuous


async def test_a_timed_out_cost_batch_keeps_the_run_status_and_notes_the_failure(run_case, fixture):
    steps = [fake_cost_batch, LLMError("timeout", "too slow")]
    result, _ = await run_case(_fake("v1.0-cost.yaml", cost=True), _script(fixture, steps))
    assert result.status == RunStatus.succeeded
    costs = result.dossier.costs
    assert costs.coverage.not_estimated > 0
    assert any("cost step" in n and "timeout" in n for n in result.dossier.notes)
    assert result.dossier.failed_experts == []


async def test_cost_node_events_carry_counts_only(run_case, fixture):
    events = []

    async def collect(event):
        events.append(event)

    await run_case(
        _fake("v1.0-cost.yaml", cost=True), _script(fixture, [fake_cost_batch] * 5), collect
    )
    (done,) = [e for e in events if e.node == "cost" and e.event == "finished"]
    payload = done.payload["cost"]
    assert set(payload) == {"records", "estimated", "not_costed", "not_estimated"}
    assert all(isinstance(v, int) for v in payload.values())


async def test_proposal_to_final_run_marks_changed_and_late_added_records(fixture):
    empty = {"impacts": [], "chains": [], "disagreements": [], "open_questions": [],
             "discarded": []}  # fmt: skip
    area = {"provision_keys": ["ai_act/penalties/penalties"], "question": "q", "rationale": "r"}
    script = {
        "planner": [{"focus_areas": [area]}],
        "expert/legal": [{"findings": []}],
        "expert/fiscal": [{"findings": []}],
        "expert/stakeholder": [{"findings": []}],
        "synthesis": [empty],
        "cost": [fake_cost_batch] * 5,
    }
    result = await run_scenario(
        "demo_penalties_amended", sv=_fake("v1.0-cost.yaml", cost=True), fixture=fixture,
        backends={"fake": FakeBackend(script)}, decisions=StubDecisionService(),
        code_identity=CodeIdentity(git_sha="test", dirty=False), run_id="run_test",
    )  # fmt: skip
    section = result.dossier.costs
    assert {r.records_version for r in section.records} == {"reg2024_1689"}
    added = [r for r in section.records if r.unit_delta == "added"]
    assert added and all(r.late_added and r.changed_after_proposal for r in added)
    assert section.late_added == [r.obligation_id for r in added]
    for r in section.records:
        if r.unit_delta in ("modified", "split_merge"):
            assert r.changed_after_proposal and not r.late_added
    assert section.delta_basis and section.hotspots


async def test_a_run_without_the_cost_role_has_no_cost_section(run_case, fixture):
    result, _ = await run_case(_fake("v1.0-cost.yaml", cost=False), _script(fixture))
    assert result.dossier.costs is None
    assert result.dossier.model_dump()["costs"] is None


async def test_an_error_inside_the_cost_node_degrades_the_section_only(run_case, fixture,
                                                                       monkeypatch):  # fmt: skip
    import womm.graph.cost as cost_mod

    async def boom(*args, **kwargs):
        raise RuntimeError("injected cost failure")

    monkeypatch.setattr(cost_mod, "estimate_costs", boom)
    result, _ = await run_case(_fake("v1.0-cost.yaml", cost=True), _script(fixture, []))
    assert result.status == RunStatus.succeeded
    costs = result.dossier.costs
    assert costs is not None and costs.records == []
    assert any("injected cost failure" in n for n in costs.notes)
    assert result.dossier.impacts and result.board
    assert result.dossier.failed_experts == []
