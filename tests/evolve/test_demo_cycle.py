"""U10 rehearsal (R30): the scripted demo cycle runs failures -> prompt stage -> topology stage
(a new expert) -> dev-mode gate on a synthetic holdout -> page 4, with no real LLM."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from womm import demo_cycle as demo
from womm.api.app import create_app
from womm.data.fixtures import fixture_dir, load_fixture
from womm.llm.fake import FakeBackend
from womm.models.system_version import load_system_version

from ..api.test_app import AUTH, _settings
from ..graph.conftest import fake_sv


def _run(database_url, tmp_path):
    return asyncio.run(demo.run_scripted_demo(database_url, tmp_path))


def test_the_scripted_demo_proposes_a_new_expert_and_gates_it_in_dev_mode(database_url, tmp_path):
    r = _run(database_url, tmp_path)
    # 1. Failures: a persistent unowned workforce miss across both proposals.
    unowned = [p for p in r.patterns if p["category"] == demo.CATEGORY and p["owner"] == "none"]
    assert unowned and len(unowned[0]["proposals"]) == 2
    # 2. The prompt stage found the Fiscal relief edit.
    assert r.prompt_best and r.prompt_best != r.base
    # 3. The topology stage proposed the Workforce expert on top of it, and it was chosen.
    assert r.topology_reason == "proposed" and r.topology == r.chosen
    # 4. The dev-mode gate promoted it, labelled dev-only and directional.
    d = r.decision
    assert d["mode"] == "dev" and not d["deployable"] and d["decision"] == "promoted"
    assert d["label"] == "promoted (dev-only, not deployable; weak threshold: directional)"
    assert "insufficient_proposals" in d["flags"]
    assert d["r37"]["status"] == "not_available"


def test_page_four_shows_the_lineage_with_the_new_expert(database_url, tmp_path):
    r = _run(database_url, tmp_path)
    policy = tmp_path / "policy.yaml"
    policy.write_text("publish_summary: true\n")
    app = create_app(_settings(database_url), sv=fake_sv(), fixture=load_fixture(),
                     backends={"fake": FakeBackend({})}, promotion_policy_path=policy)  # fmt: skip
    with TestClient(app) as c:
        lineage = c.get("/evolution/lineage", headers=AUTH).json()
        nodes = {n["version_id"]: n for n in lineage["nodes"]}
        detail = c.get(f"/evolution/candidates/{r.chosen}", headers=AUTH).json()
    assert nodes[r.base]["badges"] == ["seed"]
    assert nodes[r.prompt_best]["origin"] == "gepa" and nodes[r.prompt_best]["parent_id"] == r.base
    topo = nodes[r.chosen]
    assert topo["parent_id"] == r.prompt_best and topo["new_expert"] == "workforce"
    assert topo["badges"] == ["topology", "promoted", "dev-only"]
    expert = detail["new_expert"]
    assert expert["router_gloss"] == demo.WORKFORCE_GLOSS
    assert expert["target_pattern"] == {"kind": "missed_impact", "category": demo.CATEGORY,
                                        "owner": "none"}  # fmt: skip
    assert detail["holdout"]["decisions"][0]["mode"] == "dev"
    assert detail["proposer"]["model"] == "fake-scripted-demo", "the proposer's own record"


def test_the_demo_never_touches_the_sealed_holdout(monkeypatch):
    monkeypatch.setenv("HOLDOUT_DATABASE_URL", "postgresql://x/holdout")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/main")
    with pytest.raises(SystemExit, match="HOLDOUT_DATABASE_URL"):
        demo.main(["scripted"])


def test_the_synthetic_cases_span_two_proposals_and_never_reuse_public_holdout_ids():
    for split in ("train", "val", "holdout"):
        cases = demo.demo_cases(split)
        assert {c.fixture for c in cases} == {"ai_act", "platform_work"}
        assert all(c.split == split for c in cases)
    sealed = asyncio.run(demo.ScriptedHoldoutStore()._sealed())
    for case, scenario in sealed:
        assert scenario.scenario_id == case.scenario_id
        assert scenario.scenario_id not in load_fixture(fixture_dir(case.fixture)).scenarios


def test_the_dev_plan_names_the_committed_seed(capsys):
    assert demo.main(["dev-plan"]) == 0
    out = capsys.readouterr().out
    dev = load_system_version(demo.SEED_FILE, demo.REPO_ROOT).version_id
    assert f"womm evolve cycle --base {dev} --stage both" in out
    assert f"womm evolve promote <chosen> --incumbent {dev}" in out


def test_the_scripted_command_prints_each_step(database_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv("HOLDOUT_DATABASE_URL", raising=False)
    assert demo.main(["scripted"]) == 0
    out = capsys.readouterr().out
    assert "missed_impact/social_environmental/none (2 proposals)" in out
    assert "topology stage: proposed" in out
    assert "gate: promoted (dev-only, not deployable; weak threshold: directional)" in out
