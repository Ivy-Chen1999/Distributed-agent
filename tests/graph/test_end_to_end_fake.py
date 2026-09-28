from womm.decisions.stub import StubDecisionService
from womm.llm.base import LLMError
from womm.models.run import RunStatus

from .conftest import K55, K71, fake_sv, finding, good_script


def _synth_all(result_ids):
    return {
        "impacts": [{"impact_id": "I1", "summary": "s", "finding_ids": result_ids}],
        "chains": [],
        "disagreements": [],
        "open_questions": [],
        "discarded": [],
    }


def _ids_by_agent(backend, run_id="run_test"):
    from womm.models.findings import stable_id

    return {a: stable_id("f", run_id, a, "0") for a in ("legal", "fiscal", "stakeholder")}


async def test_happy_path_traceable(run):
    ids = _ids_by_agent(None)
    result, _ = await run(good_script(_synth_all([ids["legal"], ids["fiscal"]])))
    assert result.status == RunStatus.succeeded
    d = result.dossier
    (impact,) = d.impacts
    for f in impact.findings:
        assert f.provenance.system_version == result.system_version
        for ev in f.evidence:
            assert ev.source_id.startswith("com2021_206/art_")
    assert {f.provision_key for f in impact.findings} == {K55, K71}
    assert result.grounding.passed == 2 and result.grounding.total == 3


async def test_unsupported_finding_becomes_open_question(run):
    """Covers AE1."""
    ids = _ids_by_agent(None)
    result, _ = await run(good_script(_synth_all([ids["legal"], ids["fiscal"]])))
    (q,) = [q for q in result.dossier.open_questions if q.reason == "evidence_unresolved"]
    assert q.finding_id == ids["stakeholder"]
    assert all(ids["stakeholder"] not in [f.finding_id for f in i.findings]
               for i in result.dossier.impacts)  # fmt: skip


async def test_synthesis_cannot_promote_unsupported(run):
    """Covers AE1: synthesis puts an unsupported finding into an impact; assembler ignores it."""
    ids = _ids_by_agent(None)
    result, _ = await run(good_script(_synth_all([ids["stakeholder"], ids["legal"]])))
    impact_ids = [f.finding_id for i in result.dossier.impacts for f in i.findings]
    assert ids["stakeholder"] not in impact_ids
    assert any("unsupported" in n for n in result.dossier.notes)
    assert any(q.finding_id == ids["stakeholder"] for q in result.dossier.open_questions)


async def test_shadow_router_runs_all_experts(run):
    """Covers AE2."""
    ids = _ids_by_agent(None)
    decisions = StubDecisionService({"fiscal": ("not_relevant", 0.1)})
    result, backend = await run(
        good_script(_synth_all([ids["legal"], ids["fiscal"]])), decisions=decisions
    )
    called = {c.agent for c in backend.calls if c.role_name == "expert"}
    assert called == {"legal", "fiscal", "stakeholder"}
    rec = {r.subject: r for r in result.decisions}["fiscal"]
    assert (rec.decision, rec.mode, rec.decider) == ("not_relevant", "shadow", "stub")


async def test_active_router_skips_irrelevant(run):
    decisions = StubDecisionService({"fiscal": ("not_relevant", 0.1)})
    script = good_script(_synth_all([_ids_by_agent(None)["legal"]]))
    result, backend = await run(script, decisions=decisions, sv=fake_sv(router_mode="active"))
    assert {c.agent for c in backend.calls if c.role_name == "expert"} == {"legal", "stakeholder"}


async def test_expert_timeout_degrades(run):
    ids = _ids_by_agent(None)
    script = good_script(_synth_all([ids["legal"]]))
    script["expert/fiscal"] = [LLMError("timeout", "exceeded 240s")]
    result, _ = await run(script)
    assert result.status == RunStatus.degraded
    (fail,) = result.dossier.failed_experts
    assert (fail.agent, fail.error_kind) == ("fiscal", "timeout")


async def test_all_experts_fail(run):
    script = good_script()
    for a in ("legal", "fiscal", "stakeholder"):
        script[f"expert/{a}"] = [LLMError("process_error", "boom")]
    result, backend = await run(script)
    assert result.status == RunStatus.failed
    assert not any(c.role_name == "synthesis" for c in backend.calls)
    assert len(result.dossier.failed_experts) == 3


async def test_planner_failure(run):
    script = good_script()
    script["planner"] = [LLMError("auth", "Not logged in")]
    result, backend = await run(script)
    assert result.status == RunStatus.failed
    assert "planner failed" in result.error
    assert not any(c.role_name == "expert" for c in backend.calls)


async def test_synthesis_failure_lists_unmerged(run):
    script = good_script()
    script["synthesis"] = [LLMError("schema_invalid", "bad output", attempts=3)]
    result, _ = await run(script)
    assert result.status == RunStatus.degraded
    assert len(result.dossier.impacts) == 2
    assert all(not i.merged for i in result.dossier.impacts)
    assert any("synthesis unavailable" in n for n in result.dossier.notes)


async def test_planner_keys_outside_diff_filtered(run):
    ids = _ids_by_agent(None)
    script = good_script(_synth_all([ids["legal"]]))
    script["planner"] = [
        {"focus_areas": [{"provision_keys": ["not/in/diff"], "question": "q", "rationale": "r"}]}
    ]
    result, backend = await run(script)
    expert_call = next(c for c in backend.calls if c.role_name == "expert")
    assert "no focus areas: analyse all changes" in expert_call.user_content
    assert K55 in expert_call.user_content and K71 in expert_call.user_content


async def test_unplaced_finding_goes_to_unprocessed(run):
    ids = _ids_by_agent(None)
    result, _ = await run(good_script(_synth_all([ids["legal"]])))
    assert [f.finding_id for f in result.dossier.unprocessed] == [ids["fiscal"]]


async def test_unknown_finding_id_dropped(run):
    ids = _ids_by_agent(None)
    synth = _synth_all(["f_doesnotexist"])
    synth["impacts"].append({"impact_id": "I2", "summary": "s", "finding_ids": [ids["legal"]]})
    result, _ = await run(good_script(synth))
    assert [i.impact_id for i in result.dossier.impacts] == ["I2"]
    assert any("unknown" in n for n in result.dossier.notes)


async def test_expert_retry_replaces_board_slot(run):
    """A second write from the same expert replaces its slot (merge_slots)."""
    from womm.graph.state import merge_slots

    merged = merge_slots({"fiscal": ["old1", "old2"]}, {"fiscal": ["new"]})
    assert merged == {"fiscal": ["new"]}
    assert merge_slots({"fiscal": "x"}, {"fiscal": None}) == {}


async def test_system_version_expert_subset(run):
    sv = fake_sv(experts=["legal", "fiscal"])
    ids = _ids_by_agent(None)
    script = good_script(_synth_all([ids["legal"], ids["fiscal"]]))
    result, backend = await run(script, sv=sv)
    assert {c.agent for c in backend.calls if c.role_name == "expert"} == {"legal", "fiscal"}
    assert result.status == RunStatus.succeeded


async def test_empty_diff_no_llm_calls(run, fixture):
    """A demo-style scenario whose before and after texts are identical yields no changes."""
    from womm.models.regulation import Scenario

    fx = type(fixture)(
        regulation=fixture.regulation,
        sources=fixture.sources,
        scenarios={
            **fixture.scenarios,
            "same": Scenario(
                scenario_id="same", kind="demo", description="d",
                before_version="com2021_206", after_version="com2021_206",
                provision_keys=[K55],
            ),
        },
    )  # fmt: skip
    result, backend = await run({}, scenario="same", fixture_obj=fx)
    assert result.status == RunStatus.no_changes
    assert backend.calls == []


async def test_fabricated_quote_elsewhere_in_other_source_rejected(run):
    ids = _ids_by_agent(None)
    script = good_script(_synth_all([ids["legal"]]))
    script["expert/fiscal"] = [{"findings": [finding(K55, "com2021_206/art_71",
                                                     "provide small-scale providers and start-ups "
                                                     "with priority access")]}]  # fmt: skip
    result, _ = await run(script)
    assert any(q.finding_id == ids["fiscal"] for q in result.dossier.open_questions)


async def test_active_router_all_dispatched_fail_is_failed(run):
    decisions = StubDecisionService(
        {"fiscal": ("not_relevant", 0.1), "stakeholder": ("not_relevant", 0.1)}
    )
    script = good_script()
    script["expert/legal"] = [LLMError("timeout", "t")]
    result, backend = await run(script, decisions=decisions, sv=fake_sv(router_mode="active"))
    assert {c.agent for c in backend.calls if c.role_name == "expert"} == {"legal"}
    assert result.status == RunStatus.failed


async def test_planner_non_llm_exception_fails_run(run):
    script = good_script()
    script["planner"] = [RuntimeError("backend bug")]
    result, _ = await run(script)
    assert result.status == RunStatus.failed and "process_error" in result.error


async def test_synthesis_non_llm_exception_degrades(run):
    script = good_script()
    script["synthesis"] = [RuntimeError("backend bug")]
    result, _ = await run(script)
    assert result.status == RunStatus.degraded
    assert "process_error" in result.synthesis_error


async def test_duplicate_impact_ids_and_disagreement_ids(run):
    ids = _ids_by_agent(None)
    synth = {
        "impacts": [
            {"impact_id": "I1", "summary": "a", "finding_ids": [ids["legal"]]},
            {"impact_id": "I1", "summary": "b", "finding_ids": [ids["fiscal"]]},
        ],
        "chains": [],
        "disagreements": [{"finding_ids": [ids["legal"], ids["legal"]], "note": "self"}],
        "open_questions": [],
        "discarded": [],
    }
    result, _ = await run(good_script(synth))
    d = result.dossier
    assert [i.impact_id for i in d.impacts] == ["I1"]
    assert d.disagreements == []
    assert [f.finding_id for f in d.unprocessed] == [ids["fiscal"]]


async def test_run_events_stream(fixture):
    from womm.graph.build import run_scenario
    from womm.llm.fake import FakeBackend
    from womm.models.run import CodeIdentity

    ids = _ids_by_agent(None)
    script = good_script(_synth_all([ids["legal"], ids["fiscal"]]))
    script["expert/fiscal"] = [LLMError("timeout", "t")]
    events = []

    async def collect(e):
        events.append(e)

    await run_scenario(
        "eval_sme_impacts", sv=fake_sv(), fixture=fixture,
        backends={"fake": FakeBackend(script)}, decisions=StubDecisionService(),
        code_identity=CodeIdentity(git_sha="t", dirty=False), run_id="run_test",
        on_event=collect,
    )  # fmt: skip
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    by = {(e.node, e.event): e for e in events}
    assert ("planner", "started") in by and ("assemble", "finished") in by
    assert by[("router", "finished")].payload["dispatched"] == ["legal", "fiscal", "stakeholder"]
    assert by[("expert_fiscal", "finished")].payload["failures"] == {"fiscal": "timeout"}
    assert by[("expert_legal", "finished")].payload["findings"] == {"legal": 1}
    assert by[("validate", "finished")].payload["grounding"]["total"] == 2
    assert by[("assemble", "finished")].payload["status"] == "degraded"


async def test_router_sees_diff_summary_and_focus(run):
    from womm.graph.router import router_state

    seen = {}

    class Spy(StubDecisionService):
        async def expert_relevance(self, experts, context, sv):
            seen["context"] = context
            return await super().expert_relevance(experts, context, sv)

    await run(good_script(_synth_all([_ids_by_agent(None)["legal"]])), decisions=Spy())
    ctx = seen["context"]
    assert K55 in ctx and "(Art 55)" in ctx and "How are SMEs affected?" in ctx
    assert router_state.__doc__
