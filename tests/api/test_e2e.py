"""The scripted backend behind the console's Playwright suite (womm.api.e2e)."""

import json
import time

import pytest
from fastapi.testclient import TestClient

from womm.api.e2e import (
    FABRICATED_QUOTE,
    DelayedFakeBackend,
    Scripts,
    create_e2e_app,
    e2e_script,
    fake_system_version,
    plan,
)
from womm.data.fixtures import load_fixture
from womm.decisions.stub import StubDecisionService
from womm.diff import diff_versions
from womm.graph.build import run_scenario
from womm.graph.experts import expert_user_content
from womm.models.run import CodeIdentity

TOKEN = "e2e-test-token-0123456789"


@pytest.fixture(scope="module")
def fx():
    return load_fixture()


async def _run(fx, scenario, **backend_kw):
    sv = fake_system_version()
    backend = DelayedFakeBackend(e2e_script(fx, [e.id for e in sv.spec.experts]), delay_s=0)
    result = await run_scenario(
        scenario, sv=sv, fixture=fx, backends={"fake": backend},
        decisions=StubDecisionService(), code_identity=CodeIdentity(git_sha="t", dirty=False),
    )  # fmt: skip
    return result, backend


def test_fake_system_version_uses_only_fake():
    sv = fake_system_version()
    assert {r.backend for r in sv.spec.roles().values()} == {"fake"}
    assert sv.spec.router.mode == "shadow"


async def test_sme_run_succeeds_with_all_quotes_verified(fx):
    result, backend = await _run(fx, "eval_sme_impacts")
    assert result.status == "succeeded"
    g = result.grounding
    assert g.total > 0 and g.passed == g.total
    d = result.dossier
    assert len(d.impacts) >= 2 and d.chains and len(d.disagreements) == 1
    assert len({f.agent for fid in d.disagreements[0].finding_ids for f in result.board
                if f.finding_id == fid}) == 2  # fmt: skip
    assert [q.reason for q in d.open_questions] == ["synthesis", "synthesis"]
    assert all(u.input_tokens > 0 and u.cost_usd for u in result.usage)
    # The planner's two focus areas use the diff's keys.
    planner_call = next(c for c in backend.calls if c.role_name == "planner")
    plan_out = plan("", planner_call.user_content)
    keys = {k for a in plan_out["focus_areas"] for k in a["provision_keys"]}
    assert len(plan_out["focus_areas"]) == 2
    assert keys == set(fx.scenario("eval_sme_impacts").provision_keys)


async def test_provider_costs_has_one_fabricated_quote(fx):
    result, _ = await _run(fx, "eval_provider_compliance_costs")
    assert result.status == "succeeded"
    assert result.grounding.passed == result.grounding.total - 1
    unresolved = [q for q in result.dossier.open_questions if q.reason == "evidence_unresolved"]
    assert len(unresolved) == 1
    fabricated = [f for f in result.board if f.evidence[0].quote == FABRICATED_QUOTE]
    assert [f.finding_id for f in fabricated] == [unresolved[0].finding_id]


async def test_demo_diff_is_degraded_by_a_timed_out_expert(fx):
    result, _ = await _run(fx, "demo_penalties_amended")
    assert result.status == "degraded"
    assert [(f.agent, f.error_kind) for f in result.failures] == [("stakeholder", "timeout")]
    assert result.grounding.passed == result.grounding.total > 0
    assert result.dossier.impacts


async def test_env_overrides_apply_to_every_scenario(fx, monkeypatch):
    monkeypatch.setenv("WOMM_E2E_FAIL_EXPERT", "legal")
    monkeypatch.setenv("WOMM_E2E_FABRICATE_QUOTE", "1")
    result, _ = await _run(fx, "eval_sme_impacts")
    assert result.status == "degraded"
    assert [f.agent for f in result.failures] == ["legal"]
    assert result.grounding.passed < result.grounding.total


def test_quotes_are_verbatim_sentences_of_eight_words_or_more(fx):
    scenario = "eval_provider_compliance_costs"
    before, after = fx.scenario_versions(scenario)
    diff = diff_versions(before, after, keys=fx.scenario(scenario).provision_keys)
    sources = {x.source_id: x for x in fx.scenario_sources(scenario)}
    user = expert_user_content({"diff": diff, "sources": sources, "focus": None})
    out = Scripts(fx).expert("legal", 0)("system", user)
    assert len(out["findings"]) == 2
    for f in out["findings"]:
        ev = f["evidence"][0]
        assert len(ev["quote"].split()) >= 8 and ev["quote"] in sources[ev["source_id"]].text


def test_ask_cites_dossier_ids_and_refuses_uncovered_questions():
    dossier = {"impacts": [{"impact_id": "I1", "summary": "S", "findings": [
        {"finding_id": "f_1", "agent": "legal"}]}]}  # fmt: skip
    user = "Dossier:\n" + json.dumps(dossier) + "\n\nQuestion: "
    covered = Scripts.ask("", user + "Who pays?")
    assert covered["covered"] and covered["cites"] == ["I1", "f_1"]
    uncovered = Scripts.ask("", user + "What is the weather tomorrow?")
    assert uncovered == {**uncovered, "covered": False, "cites": []}


def test_e2e_app_runs_scenarios_over_http(database_url, monkeypatch):
    monkeypatch.setenv("WOMM_API_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("WOMM_E2E_DELAY_S", "0")
    auth = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_e2e_app()) as c:
        system = c.get("/system", headers=auth).json()
        assert system["available_backends"] == ["api", "fake"]
        run_id = c.post("/runs", json={"scenario_id": "demo_penalties_amended"},
                        headers=auth).json()["run_id"]  # fmt: skip
        deadline = time.monotonic() + 15
        while (body := c.get(f"/runs/{run_id}", headers=auth).json())["status"] in (
            "queued", "running"
        ) and time.monotonic() < deadline:  # fmt: skip
            time.sleep(0.05)
        assert body["status"] == "degraded"
        answer = c.post(f"/runs/{run_id}/ask", json={"question": "Who pays?"}, headers=auth)
        assert answer.json()["covered"] is True and answer.json()["cites"]
