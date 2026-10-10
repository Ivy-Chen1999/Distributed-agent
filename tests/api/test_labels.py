"""Hosted labelling (womm.api.labels): sign-in by name, answers saved per item, admin export."""

import time

import pytest
from fastapi.testclient import TestClient

from womm.api import labels
from womm.api.app import create_app
from womm.config import ConfigError, load_settings
from womm.data.fixtures import load_fixture

from ..graph.conftest import fake_sv

TOKEN = "test-token-0123456789"
ADMIN = "admin-token-0123456789"
ADMIN_AUTH = {"Authorization": f"Bearer {ADMIN}"}
SAMPLE = "2026-10-10-0123abcd"
API = f"/label/api/{SAMPLE}"


def _card(i, fixture="dma"):
    return {"id": f"r{i:02d}", "case": "case_06_dma", "fixture": fixture, "provisions": [],
            "claim": {"affected_actor": "a"}, "source": "report", "section": "s",
            "quote": "q", "passage": None, "others": []}  # fmt: skip


def _client(database_url, admin=ADMIN):
    env = {"DATABASE_URL": database_url, "WOMM_API_TOKEN": TOKEN}
    if admin:
        env["WOMM_LABEL_ADMIN_TOKEN"] = admin
    app = create_app(load_settings(env), sv=fake_sv(), fixture=load_fixture(),
                     backends={}, web_dist=None)  # fmt: skip
    return TestClient(app)


def _upload(c, annotators=None):
    body = {"annotators": annotators or {"alice": [_card(1), _card(2)], "bob": [_card(3)]}}
    return c.put(f"{API}/admin/tasks", json=body, headers=ADMIN_AUTH)


def test_off_without_admin_token(database_url):
    with _client(database_url, admin=None) as c:
        assert c.get(f"/label/{SAMPLE}").status_code == 404
        assert c.put(f"{API}/admin/tasks", json={"annotators": {}}).status_code in (404, 405)


def test_short_admin_token_refused(database_url):
    with pytest.raises(ConfigError, match="WOMM_LABEL_ADMIN_TOKEN"):
        _client(database_url, admin="short")


def test_upload_needs_admin_and_refuses_the_key(database_url):
    with _client(database_url) as c:
        body = {"annotators": {"alice": [_card(1)]}}
        assert c.put(f"{API}/admin/tasks", json=body).status_code == 401
        bad = {"annotators": {"alice": [_card(1) | {"tool_kept": True}]}}
        r = c.put(f"{API}/admin/tasks", json=bad, headers=ADMIN_AUTH)
        assert r.status_code == 422 and "tool_kept" in r.text
        bad_name = {"annotators": {"al ice": [_card(1)]}}
        assert c.put(f"{API}/admin/tasks", json=bad_name, headers=ADMIN_AUTH).status_code == 422
        assert c.put("/label/api/not-a-sample/admin/tasks", json=body,
                     headers=ADMIN_AUTH).status_code == 404  # fmt: skip


def test_label_flow(database_url):
    with _client(database_url) as c:
        assert _upload(c).status_code == 200
        page = c.get(f"/label/{SAMPLE}")
        assert page.status_code == 200 and "Why a person has to look" in page.text
        assert "tool_kept" not in page.text
        assert c.get("/label/2026-10-10-ffffffff").status_code == 404
        assert c.get(f"{API}/me").status_code == 401
        assert c.post(f"{API}/session", json={"name": "mallory"}).status_code == 401
        me = c.post(f"{API}/session", json={"name": "@Alice"})
        assert me.status_code == 200
        assert me.json()["annotator"] == "alice" and [i["id"] for i in me.json()["items"]] == [
            "r01", "r02"]  # fmt: skip
        assert c.put(f"{API}/answers/r01", json={"label": "maybe"}).status_code == 422
        assert c.put(f"{API}/answers/r03", json={"label": "keep"}).status_code == 404  # bob's
        assert c.put(f"{API}/answers/r01", json={"label": "keep"}).status_code == 200
        r = c.post(f"{API}/submit")
        assert r.status_code == 409 and "r02" in r.text
        c.put(f"{API}/answers/r02", json={"label": "unsure", "note": " vague "})
        c.put(f"{API}/answers/r01", json={"label": "drop"})  # the last answer wins
        assert c.post(f"{API}/submit").status_code == 200
        again = c.get(f"{API}/me").json()
        assert again["submitted"] and again["answers"]["r01"]["label"] == "drop"
        # Re-uploading would orphan answers.
        assert _upload(c).status_code == 409
        assert c.get(f"{API}/admin/results").status_code == 401
        res = c.get(f"{API}/admin/results", headers=ADMIN_AUTH).json()["annotators"]
        assert res["alice"]["submitted_at"] and res["alice"]["answers"] == [
            {"item": "r01", "label": "drop", "note": ""},
            {"item": "r02", "label": "unsure", "note": "vague"},
        ]
        assert res["bob"] == {"cards": 1, "submitted_at": None, "answers": []}


def test_cookie_is_bound_to_sample_signature_and_expiry(database_url):
    with _client(database_url) as c:
        _upload(c)
        c.post(f"{API}/session", json={"name": "alice"})
        sample, name, expires, sig = c.cookies.get("womm_label").split("|")
        assert name == "alice" and int(expires) > time.time() + 86400
        for forged in (f"{sample}|bob|{expires}|{sig}", f"{sample}|alice|{int(expires) + 1}|{sig}"):
            c.cookies.clear()
            c.cookies.set("womm_label", forged)
            assert c.get(f"{API}/me").status_code == 401
    stale = labels.session_cookie(ADMIN, SAMPLE, "a", 0)
    assert labels.cookie_annotator(ADMIN, SAMPLE, stale, 1) == "a"
    assert labels.cookie_annotator(ADMIN, SAMPLE, stale, labels.SESSION_S + 1) is None
    assert labels.cookie_annotator(ADMIN, "2026-01-01-00000000", stale, 1) is None


def test_admin_page_keeps_the_token_out_of_the_url(database_url):
    with _client(database_url) as c:
        page = c.get(f"/label/{SAMPLE}/admin")
        assert page.status_code == 200 and SAMPLE in page.text and "__SAMPLE__" not in page.text
        assert "Authorization" in page.text and "?token=" not in page.text
        assert c.get("/label/nope/admin").status_code == 404


def test_sign_in_is_rate_limited(database_url):
    with _client(database_url) as c:
        _upload(c)
        codes = [c.post(f"{API}/session", json={"name": f"x{i}"}).status_code for i in range(11)]
        assert codes[:10] == [401] * 10 and codes[10] == 429
