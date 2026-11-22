"""WOMM_VIEW_TOKEN: a public, read-only access code (printed on the poster). It opens every read
endpoint; every write answers it 403, so the console keeps the code and shows why; the team's
token is unchanged."""

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from womm.api.app import VIEW_ONLY, create_app
from womm.config import ConfigError, load_settings
from womm.data.fixtures import load_fixture
from womm.llm.fake import FakeBackend

from ..graph.conftest import fake_sv, good_script
from .test_app import AUTH, TOKEN
from .test_db import synthesis_all

CODE = "womm-demo"
VIEW = {"Authorization": f"Bearer {CODE}"}
PUBLIC = {"/health", "/livez", "/"}  # "/" redirects to /docs when the console is not built


def _app(database_url, view=CODE):
    env = {"DATABASE_URL": database_url, "WOMM_API_TOKEN": TOKEN}
    if view is not None:
        env["WOMM_VIEW_TOKEN"] = view
    return create_app(
        load_settings(env), sv=fake_sv(), fixture=load_fixture(), web_dist=None,
        backends={"fake": FakeBackend(good_script(synthesis_all()))}, orphan_stale_after_s=0,
    )  # fmt: skip


def test_the_view_code_reads_the_console(database_url):
    with TestClient(_app(database_url)) as c:
        for path in ("/scenarios", "/runs", "/system", "/evolution/lineage", "/regulations"):
            r = c.get(path, headers=VIEW)
            assert r.status_code == 200, (path, r.text)


def test_the_view_code_cannot_write(database_url):
    with TestClient(_app(database_url)) as c:
        r = c.post("/runs", json={"scenario_id": "eval_sme_impacts"}, headers=VIEW)
        assert r.status_code == 403 and r.json()["detail"] == VIEW_ONLY
        assert c.post("/evolution/archive", json={}, headers=VIEW).status_code == 403
        assert c.post("/eval-reports", json={}, headers=VIEW).status_code == 403
        assert c.get("/runs", headers=VIEW).json()["runs"] == [], "nothing was started"
        team = c.post("/runs", json={"scenario_id": "eval_sme_impacts"}, headers=AUTH)
        assert team.status_code == 202, "the team's token still writes"


def test_the_system_tells_the_console_who_can_run(database_url):
    with TestClient(_app(database_url)) as c:
        assert c.get("/system", headers=VIEW).json()["can_run"] is False
        assert c.get("/system", headers=AUTH).json()["can_run"] is True


def test_other_codes_are_still_refused(database_url):
    with TestClient(_app(database_url)) as c:
        assert c.get("/scenarios").status_code == 401
        assert c.get("/scenarios", headers={"Authorization": "Bearer womm-dem0"}).status_code == 401
        assert c.post("/runs", json={"scenario_id": "eval_sme_impacts"}).status_code == 401
    with TestClient(_app(database_url, view=None)) as c:
        assert c.get("/scenarios", headers=VIEW).status_code == 401, "no code configured"
        assert c.post("/runs", json={"scenario_id": "x"}, headers=VIEW).status_code == 401


def test_every_route_is_either_a_read_or_a_team_only_write(database_url):
    app = _app(database_url)
    seen = 0
    for route in app.routes:
        if not isinstance(route, APIRoute) or route.path in PUBLIC:
            continue
        gates = {getattr(d.call, "__name__", None) for d in route.dependant.dependencies}
        want = "require_reader" if route.methods <= {"GET", "HEAD"} else "require_token"
        assert want in gates, (route.path, route.methods, gates)
        seen += 1
    assert seen >= 15


@pytest.mark.parametrize(
    ("view", "match"), [("abc", "at least"), (TOKEN, "differ")], ids=["short", "same-as-team"]
)
def test_a_bad_view_code_stops_the_app(database_url, view, match):
    with pytest.raises(ConfigError, match=match):
        _app(database_url, view=view)
