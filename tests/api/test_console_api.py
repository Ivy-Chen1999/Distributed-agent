"""Endpoints the WOMM Console uses beyond the core run API."""

from fastapi.testclient import TestClient

from womm.api.app import create_app
from womm.data.fixtures import load_fixture
from womm.llm.fake import FakeBackend

from ..graph.conftest import fake_sv, good_script
from .test_app import AUTH, _settings, _wait
from .test_db import synthesis_all


def _client(database_url, script, backend=None, **kw):
    app = create_app(
        _settings(database_url), sv=fake_sv(), fixture=load_fixture(),
        backends={"fake": backend or FakeBackend(script)}, orphan_stale_after_s=0, **kw,
    )  # fmt: skip
    return TestClient(app)


def _finished_run(c):
    run_id = c.post("/runs", json={"scenario_id": "eval_sme_impacts"}, headers=AUTH).json()[
        "run_id"
    ]
    return run_id, _wait(c, run_id)


def test_system(database_url):
    with _client(database_url, {}) as c:
        body = c.get("/system", headers=AUTH).json()
        assert body["version_id"].startswith("sv_")
        assert set(body["roles"]) >= {"planner", "synthesis", "judge", "expert:legal"}
        assert body["available_backends"] == ["fake"] and body["router"]["mode"] == "shadow"
        assert body["self_check"] is None and body["backend_ready"] is True


def test_scenario_sources(database_url):
    with _client(database_url, {}) as c:
        body = c.get("/scenarios/eval_sme_impacts/sources", headers=AUTH).json()
        keys = [ch["provision_key"] for ch in body["changes"]]
        assert "ai_act/innovation/sme_measures" in keys
        assert all(ch["kind"] == "added" and ch["before"] is None for ch in body["changes"])
        ids = {s["source_id"] for s in body["sources"]}
        assert "com2021_206/art_55" in ids
        assert c.get("/scenarios/nope/sources", headers=AUTH).status_code == 404


def test_runs_list_and_detail_fields(database_url):
    with _client(database_url, good_script(synthesis_all())) as c:
        run_id, detail = _finished_run(c)
        for key in ("board", "failures", "usage", "code_identity", "started_at"):
            assert key in detail
        ids = {s["source_id"] for s in detail["citable_sources"]}
        assert "com2021_206/art_55" in ids
        assert set(detail["citable_sources"][0]) == {"source_id", "title", "kind", "text"}
        (row,) = c.get("/runs", headers=AUTH).json()["runs"]
        assert row["run_id"] == run_id and row["status"] == "succeeded"
        assert row["impacts"] == len(detail["dossier"]["impacts"])
        assert row["grounding"] == detail["grounding"] and row["duration_s"] >= 0


def test_overrides_derive_new_version(database_url):
    with _client(database_url, good_script(synthesis_all())) as c:
        base = c.get("/system", headers=AUTH).json()["version_id"]
        body = {"scenario_id": "eval_sme_impacts", "overrides": {"router_mode": "active"}}
        r = c.post("/runs", headers=AUTH, json=body)
        assert r.status_code == 202 and r.json()["system_version"] != base
        detail = _wait(c, r.json()["run_id"])
        assert detail["system_version"] == r.json()["system_version"]
        bad = c.post(
            "/runs",
            headers=AUTH,
            json={"scenario_id": "eval_sme_impacts", "overrides": {"backends": {"planner": "api"}}},
        )
        assert bad.status_code == 422 and "not available" in bad.json()["detail"]
        bad_role = c.post(
            "/runs",
            headers=AUTH,
            json={"scenario_id": "eval_sme_impacts", "overrides": {"backends": {"nobody": "fake"}}},
        )
        assert bad_role.status_code == 422


def test_console_served_when_built(database_url, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>WOMM Console</title>")
    with _client(database_url, {}, web_dist=tmp_path) as c:
        assert "WOMM Console" in c.get("/").text
        assert c.get("/livez").status_code == 200  # API routes still win
    with _client(database_url, {}, web_dist=None) as c:
        assert c.get("/", follow_redirects=False).headers["location"] == "/docs"


EXPLORE_SCENARIOS = {"eval_whole_proposal", "consolidated_whole_act", "omnibus_2026"}


def test_explore_scenarios_hidden_from_console_but_runnable(database_url):
    plan = {
        "focus_areas": [{"provision_keys": ["ai_act/art/26"], "question": "q", "rationale": "r"}]
    }
    script = {"planner": [plan], "expert": [{"findings": []}] * 3, "synthesis": [synthesis_all()]}
    with _client(database_url, script) as c:
        listed = {s["scenario_id"] for s in c.get("/scenarios", headers=AUTH).json()}
        assert "eval_sme_impacts" in listed and not listed & EXPLORE_SCENARIOS
        for sid in EXPLORE_SCENARIOS:
            assert c.get(f"/scenarios/{sid}/sources", headers=AUTH).status_code == 404
        r = c.post("/runs", json={"scenario_id": "consolidated_whole_act"}, headers=AUTH)
        assert r.status_code == 202
        body = _wait(c, r.json()["run_id"])
        assert body["status"] == "succeeded", body
        assert body["dossier"]["law_version"]["version_id"] == "reg2024_1689_c20260727"
        assert body["dossier"]["law_version"]["pre_omnibus"] is False


def test_corpus_comparisons_and_compare(database_url):
    with _client(database_url, {}) as c:
        pairs = c.get("/corpus/ai_act/comparisons", headers=AUTH).json()
        ids = [(p["before"]["version_id"], p["after"]["version_id"]) for p in pairs]
        assert ("reg2024_1689", "reg2024_1689_c20260727") in ids
        by_pair = {(p["before"]["version_id"], p["after"]["version_id"]): p for p in pairs}
        assert "omnibus_2026" in by_pair["reg2024_1689", "reg2024_1689_c20260727"]["scenario_ids"]
        assert "final_vs_proposal" in by_pair["com2021_206", "reg2024_1689"]["scenario_ids"]
        body = c.get(
            "/corpus/ai_act/compare/reg2024_1689/reg2024_1689_c20260727", headers=AUTH
        ).json()
        assert body["paragraph_basis"] == "number"
        arts = {a["provision_key"]: a for a in body["articles"]}
        assert arts["ai_act/art/27"]["change"] == "modified"
        assert arts["ai_act/art/4a"]["change"] == "added"
        backwards = "/corpus/ai_act/compare/reg2024_1689/com2021_206"
        assert c.get(backwards, headers=AUTH).status_code == 404
        assert c.get("/corpus/nope/comparisons", headers=AUTH).status_code == 404
        assert c.get("/corpus/ai_act/comparisons").status_code in (401, 403)
        regs = c.get("/regulations", headers=AUTH).json()
        assert [r["regulation_id"] for r in regs] == ["ai_act"]  # corpus acts only, no fixtures
        assert "omnibus_2026" in regs[0]["comparisons"][1]["scenario_ids"]
        listed = {s["scenario_id"] for s in regs[0]["scenarios"]}
        assert {"eval_sme_impacts", "demo_penalties_amended", "omnibus_2026"} <= listed
        assert all("ia_reference" not in s for s in regs[0]["scenarios"])
