"""Endpoints the WOMM Console uses beyond the core run API."""

from fastapi.testclient import TestClient

from womm.api.app import create_app
from womm.data.fixtures import load_fixture
from womm.llm.fake import FakeBackend

from ..graph.conftest import fake_sv, good_script
from .test_app import AUTH, _settings, _wait
from .test_db import synthesis_all


def _client(database_url, script, **kw):
    app = create_app(
        _settings(database_url), sv=fake_sv(), fixture=load_fixture(),
        backends={"fake": FakeBackend(script)}, orphan_stale_after_s=0, **kw,
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


def test_ask_answers_and_filters_cites(database_url):
    answer = {"answer": "Providers face tiered fines.", "cites": ["I1", "made_up"], "covered": True}
    script = good_script(synthesis_all())
    script["ask"] = [answer]
    with _client(database_url, script) as c:
        run_id, _ = _finished_run(c)
        r = c.post(f"/runs/{run_id}/ask", headers=AUTH, json={"question": "Who pays fines?"})
        assert r.status_code == 200
        assert r.json() == {"answer": "Providers face tiered fines.", "cites": ["I1"],
                            "covered": True}  # fmt: skip


def test_ask_without_dossier_is_409(database_url):
    from womm.llm.base import LLMError

    script = good_script()
    script["planner"] = [LLMError("auth", "x")]
    with _client(database_url, script) as c:
        run_id, _ = _finished_run(c)
        r = c.post(f"/runs/{run_id}/ask", headers=AUTH, json={"question": "q"})
        assert r.status_code == 409


def test_console_served_when_built(database_url, tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>WOMM Console</title>")
    with _client(database_url, {}, web_dist=tmp_path) as c:
        assert "WOMM Console" in c.get("/").text
        assert c.get("/livez").status_code == 200  # API routes still win
    with _client(database_url, {}, web_dist=None) as c:
        assert c.get("/", follow_redirects=False).headers["location"] == "/docs"
