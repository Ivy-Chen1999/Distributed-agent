import time
import uuid

import pytest
from fastapi.testclient import TestClient

from womm.api.app import create_app
from womm.config import ConfigError, load_settings
from womm.data.fixtures import load_fixture
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend

from ..graph.conftest import fake_sv, good_script
from .test_db import synthesis_all

TOKEN = "test-token-0123456789"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _settings(database_url, token=TOKEN):
    return load_settings({"DATABASE_URL": database_url, "WOMM_API_TOKEN": token})


def _client(database_url, script, **kw):
    kw.setdefault("orphan_stale_after_s", 0)
    app = create_app(
        _settings(database_url), sv=fake_sv(), fixture=load_fixture(),
        backends={"fake": FakeBackend(script)}, **kw,
    )  # fmt: skip
    return TestClient(app)


def _wait(client, run_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/runs/{run_id}", headers=AUTH).json()
        if body["status"] not in ("queued", "running"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish: {body}")


def test_refuses_to_start_without_token(database_url):
    with pytest.raises(ConfigError, match="WOMM_API_TOKEN"):
        create_app(_settings(database_url, token=""))


def test_health_is_public_everything_else_needs_token(database_url):
    with _client(database_url, {}) as c:
        assert c.get("/health").json()["status"] == "ok"
        assert c.get("/", follow_redirects=False).headers["location"] == "/docs"
        assert c.get("/scenarios").status_code == 401
        assert c.get("/scenarios", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.post("/runs", json={"scenario_id": "eval_sme_impacts"}).status_code == 401
        assert c.get(f"/runs/run_{uuid.uuid4()}").status_code == 401
        assert c.get("/scenarios", headers=AUTH).status_code == 200


def test_submit_poll_events_dossier(database_url):
    with _client(database_url, good_script(synthesis_all())) as c:
        r = c.post("/runs", json={"scenario_id": "eval_sme_impacts"}, headers=AUTH)
        assert r.status_code == 202 and r.json()["status"] == "queued"
        run_id = r.json()["run_id"]
        uuid.UUID(run_id.removeprefix("run_"))  # not guessable
        body = _wait(c, run_id)
        assert body["status"] == "succeeded"
        assert body["dossier"]["impacts"] and body["nodes"]["assemble"] == "finished"
        assert len(body["decisions"]) == 3
        ev = c.get(f"/runs/{run_id}/events", headers=AUTH).json()
        assert [e["seq"] for e in ev["events"]] == list(range(1, len(ev["events"]) + 1))
        tail = c.get(f"/runs/{run_id}/events?after={ev['next_after'] - 1}", headers=AUTH).json()
        assert len(tail["events"]) == 1


def test_unknown_scenario_and_run(database_url):
    with _client(database_url, {}) as c:
        assert c.post("/runs", json={"scenario_id": "nope"}, headers=AUTH).status_code == 404
        assert c.get(f"/runs/run_{uuid.uuid4()}", headers=AUTH).status_code == 404
        assert c.get(f"/runs/run_{uuid.uuid4()}/events", headers=AUTH).status_code == 404


def test_failed_run_has_no_dossier(database_url):
    script = good_script()
    script["planner"] = [LLMError("auth", "Not logged in")]
    with _client(database_url, script) as c:
        run_id = c.post("/runs", json={"scenario_id": "eval_sme_impacts"}, headers=AUTH).json()[
            "run_id"
        ]
        body = _wait(c, run_id)
        assert body["status"] == "failed" and "dossier" not in body


def test_backend_unavailable_returns_503(database_url, monkeypatch):
    import womm.api.app as app_mod

    async def broken(sv, *, skip_self_check=False, settings=None):
        raise LLMError("auth", "no API key")

    monkeypatch.setattr(app_mod, "prepare_backends", broken)
    app = create_app(_settings(database_url), sv=fake_sv(), fixture=load_fixture())
    with TestClient(app) as c:
        assert c.get("/livez").status_code == 200
        health = c.get("/health")
        assert health.status_code == 503 and health.json()["backend_ready"] is False
        r = c.post("/runs", json={"scenario_id": "eval_sme_impacts"}, headers=AUTH)
        assert r.status_code == 503 and "no API key" in r.json()["detail"]


def test_restart_reconciles_orphans(database_url):
    import asyncio

    from womm.api.db import Database

    async def seed():
        db = Database(database_url)
        await db.open()
        await db.migrate()
        await db.create_run("run_orphan", "eval_sme_impacts", "sv")
        await db.mark_running("run_orphan")
        await db.close()

    asyncio.run(seed())
    with _client(database_url, {}) as c:
        body = c.get("/runs/run_orphan", headers=AUTH).json()
        assert (body["status"], body["error_kind"]) == ("failed", "orphaned")


def test_short_token_refused(database_url):
    with pytest.raises(ConfigError, match="at least"):
        create_app(_settings(database_url, token="short"))


def test_queue_full_returns_429(database_url):
    from .test_jobs import BlockingBackend

    app = create_app(
        _settings(database_url), sv=fake_sv(), fixture=load_fixture(),
        backends={"fake": BlockingBackend()}, max_pending_runs=1,
    )  # fmt: skip
    with TestClient(app) as c:
        assert (
            c.post("/runs", json={"scenario_id": "eval_sme_impacts"}, headers=AUTH).status_code
            == 202
        )
        r = c.post("/runs", json={"scenario_id": "eval_sme_impacts"}, headers=AUTH)
        assert r.status_code == 429
