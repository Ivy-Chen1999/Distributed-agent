"""POST /eval-reports and GET /eval-reports/latest: train/val only, latest per version."""

import psycopg
import pytest

from womm.api.db import Database

from .test_app import AUTH
from .test_console_api import _client

TENANT = "11111111-2222-4333-8444-555555555555"
PROJECT = "66666666-7777-4888-9999-000000000000"


def _run(case, rep, covered, expected, split="train"):
    return {"case_id": case, "split": split, "repetition": rep, "covered": covered,
            "expected": expected, "addressed": 1, "omissions": 2, "grounding": 1.0}  # fmt: skip


def _report(version="sv_aaaaaaaaaaaa", runs=None, **kw):
    return {"system_version": version, "git_sha": "abc", "judge_version": "jv_1",
            "repetitions": 2, "experiment_name": "womm-x", "experiment_tenant_id": TENANT,
            "experiment_project_id": PROJECT,
            "runs": runs or [_run("c1", 1, 3, 4), _run("c1", 2, 4, 4), _run("c2", 1, 1, 3)],
            **kw}  # fmt: skip


def test_a_published_report_is_the_latest_for_its_version(database_url):
    with _client(database_url, {}) as c:
        r = c.post("/eval-reports", json=_report(), headers=AUTH)
        assert r.status_code == 201, r.text
        latest = c.get("/eval-reports/latest?system_version=sv_aaaaaaaaaaaa", headers=AUTH)
        body = latest.json()
        assert latest.status_code == 200
        ref = body["summary"]["reference_impacts"]
        assert (ref["found"], ref["total"]) == (8, 11) and ref["range"] is not None
        assert body["summary"]["cases"] == 2 and body["repetitions"] == 2
        assert body["experiment_url"] == (
            f"https://smith.langchain.com/o/{TENANT}/projects/p/{PROJECT}"
        )
        newer = _report(runs=[_run("c1", 1, 1, 1)])
        c.post("/eval-reports", json=newer, headers=AUTH)
        body = c.get("/eval-reports/latest?system_version=sv_aaaaaaaaaaaa", headers=AUTH).json()
        assert body["summary"]["reference_impacts"]["total"] == 1
        other = c.get("/eval-reports/latest?system_version=sv_bbbbbbbbbbbb", headers=AUTH)
        assert other.status_code == 404


def test_a_holdout_run_refuses_the_whole_report(database_url):
    with _client(database_url, {}) as c:
        runs = [_run("c1", 1, 1, 1), _run("h1", 1, 1, 1, split="holdout")]
        r = c.post("/eval-reports", json=_report("sv_cccccccccccc", runs), headers=AUTH)
        assert r.status_code == 422
        got = c.get("/eval-reports/latest?system_version=sv_cccccccccccc", headers=AUTH)
        assert got.status_code == 404


def test_publishing_needs_the_token_and_a_well_formed_report(database_url):
    with _client(database_url, {}) as c:
        assert c.post("/eval-reports", json=_report()).status_code == 401
        bad = _report(experiment_tenant_id="https://evil.example/x")
        assert c.post("/eval-reports", json=bad, headers=AUTH).status_code == 422
        empty = {**_report(), "runs": []}
        assert c.post("/eval-reports", json=empty, headers=AUTH).status_code == 422


async def test_the_table_refuses_a_holdout_row(database_url):
    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        report_id = await db.insert_eval_report(
            {**_report("sv_dddddddddddd"), "runs": [_run("c1", 1, 1, 1)]}, {"cases": 1}
        )
        async with db.pool.connection() as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "INSERT INTO eval_report_runs (report_id, case_id, split, repetition, counts)"
                    " VALUES (%s, 'h', 'holdout', 1, '{}')",
                    (report_id,),
                )
    finally:
        await db.close()
