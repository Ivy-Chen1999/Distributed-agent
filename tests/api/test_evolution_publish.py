"""POST /evolution/archive and `womm evolve publish`: a local evolution archive copied to a
hosted console shows the same Evolution page; train/val only, self-contained, content-addressed,
shaped as the page reads it, and never overwriting rows already stored."""

import asyncio
import copy
import json

import psycopg
import pytest

from womm import cli
from womm.api.db import Database
from womm.api.e2e import EVOLUTION_CYCLE
from womm.evolve.publish import export_bundle, post

from .test_app import AUTH, TOKEN
from .test_evolution_api import _client


async def _export(database_url, cycle_id=None):
    db = Database(database_url)
    await db.open()
    try:
        return await export_bundle(db, cycle_id)
    finally:
        await db.close()


def _clear(database_url):
    with psycopg.connect(database_url) as conn:
        conn.execute("DELETE FROM sv_metrics")
        conn.execute("DELETE FROM sv_archive")
        conn.execute("DELETE FROM sv_prompts")


def _counts(database_url):
    with psycopg.connect(database_url) as conn:
        return tuple(
            conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("sv_archive", "sv_metrics", "sv_prompts")
        )


def _pages(c, ids):
    out = {"lineage": c.get("/evolution/lineage", headers=AUTH).json()}
    for key in ("prompt", "topology", "rejected"):
        r = c.get(f"/evolution/candidates/{ids[key]}", headers=AUTH)
        assert r.status_code == 200, r.text
        out[key] = r.json()
    return out


def _version(bundle, vid):
    return next(v for v in bundle["versions"] if v["version_id"] == vid)


def test_a_published_archive_shows_the_same_evolution_page(database_url, tmp_path):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        before = _pages(c, ids)
        bundle = asyncio.run(_export(database_url))
        totals = _counts(database_url)
        _clear(database_url)
        assert c.get("/evolution/lineage", headers=AUTH).json()["nodes"] == []

        r = c.post("/evolution/archive", json=bundle, headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
        assert tuple(body[t]["inserted"] for t in ("versions", "metrics", "prompts")) == totals
        assert _pages(c, ids) == before, "scores, twins and rationales survive the copy"

        again = c.post("/evolution/archive", json=bundle, headers=AUTH).json()
        assert all(t["inserted"] == 0 and t["different"] == 0 for t in again.values())
        assert tuple(again[t]["identical"] for t in ("versions", "metrics", "prompts")) == totals
    assert _counts(database_url) == totals


def test_a_stored_row_is_kept_and_reported_when_it_differs(database_url, tmp_path):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        bundle = asyncio.run(_export(database_url))
        with psycopg.connect(database_url) as conn:
            conn.execute("UPDATE sv_archive SET proposer = '{\"model\": \"kept\"}'"
                         " WHERE version_id = %s", (ids["prompt"],))  # fmt: skip
            conn.execute("UPDATE sv_metrics SET mean = 0.123 WHERE version_id = %s",
                         (ids["prompt"],))  # fmt: skip
        r = c.post("/evolution/archive", json=bundle, headers=AUTH)
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["versions"]["different"] == 1 and body["versions"]["differing"] == [ids["prompt"]]
    assert body["metrics"]["different"] > 0 and body["metrics"]["differing"]
    with psycopg.connect(database_url) as conn:
        proposer = conn.execute("SELECT proposer FROM sv_archive WHERE version_id = %s",
                                (ids["prompt"],)).fetchone()[0]  # fmt: skip
        means = conn.execute("SELECT DISTINCT mean FROM sv_metrics WHERE version_id = %s",
                             (ids["prompt"],)).fetchall()  # fmt: skip
    assert proposer == {"model": "kept"} and means == [(0.123,)], "never overwritten"


def test_one_cycle_brings_its_ancestors_and_twins(database_url, tmp_path):
    _, ids = _client(database_url, tmp_path)
    bundle = asyncio.run(_export(database_url, EVOLUTION_CYCLE))
    got = {v["version_id"] for v in bundle["versions"]}
    assert {ids["seed"], ids["seed_api"], ids["prompt"], ids["topology"],
            ids["topology_api"]} <= got  # fmt: skip
    assert ids["rejected"] not in got, "another cycle's candidate stays out"
    assert {m["version_id"] for m in bundle["metrics"]} <= got
    with pytest.raises(Exception, match="no archived version belongs to cycle"):
        asyncio.run(_export(database_url, "cycle_unknown"))


def _refused(c, bundle):
    r = c.post("/evolution/archive", json=bundle, headers=AUTH)
    assert r.status_code == 422, r.text
    return r.text


def test_a_bundle_is_refused_whole_unless_it_is_train_val_and_self_contained(
    database_url, tmp_path
):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        good = asyncio.run(_export(database_url))
        _clear(database_url)
        assert c.post("/evolution/archive", json=good).status_code == 401, "needs the token"

        holdout = copy.deepcopy(good)
        holdout["metrics"][0]["split"] = "holdout"
        _refused(c, holdout)

        tampered = copy.deepcopy(good)
        tampered["prompts"][0]["text"] += " (edited)"
        assert "does not match its text" in _refused(c, tampered)

        orphan = copy.deepcopy(good)
        orphan["versions"].remove(_version(orphan, ids["seed"]))
        assert "is not in the bundle" in _refused(c, orphan)

        no_prompt = copy.deepcopy(good)
        no_prompt["prompts"] = no_prompt["prompts"][1:]
        assert "prompt" in _refused(c, no_prompt)

        partial = copy.deepcopy(good)
        next(m for m in partial["metrics"] if m["level"] == "split")["full_split"] = False
        assert "partial batch" in _refused(c, partial)

        loop = copy.deepcopy(good)
        _version(loop, ids["seed"])["parent_id"] = ids["prompt"]
        assert "cycle" in _refused(c, loop)

        extra = copy.deepcopy(good)
        extra["versions"][0]["sealed"] = True
        _refused(c, extra)

        for field, value in (("sd", 1e200), ("sd", -0.1), ("mean", 1e200), ("n", 2**31 - 1),
                             ("mean", float("nan"))):  # fmt: skip
            huge = copy.deepcopy(good)
            next(m for m in huge["metrics"] if m["level"] == "case")[field] = value
            # Raw text: a client can send NaN, which the test client's json= refuses to encode.
            r = c.post("/evolution/archive", content=json.dumps(huge),
                       headers={**AUTH, "Content-Type": "application/json"})  # fmt: skip
            assert r.status_code == 422, (field, value, r.text)
            assert c.get("/evolution/lineage", headers=AUTH).status_code == 200

        nul = copy.deepcopy(good)
        nul["metrics"][0]["subject"] = "a\x00b"
        _refused(c, nul)
    assert _counts(database_url) == (0, 0, 0), "nothing written by a refused bundle"


def test_a_version_must_be_what_its_id_says(database_url, tmp_path):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        good = asyncio.run(_export(database_url))
        _clear(database_url)

        renamed = copy.deepcopy(good)
        _version(renamed, ids["prompt"])["name"] = "forged"
        assert "is not the spec's" in _refused(c, renamed)

        forged = copy.deepcopy(good)
        v = _version(forged, ids["prompt"])
        v["name"] = v["spec"]["name"] = "forged"
        assert "rebuild as" in _refused(c, forged)

        swapped = copy.deepcopy(good)
        pm = _version(swapped, ids["prompt"])["prompt_map"]
        path = next(iter(pm))
        pm[path] = next(p["sha256"] for p in swapped["prompts"] if p["sha256"] != pm[path])
        assert "rebuild as" in _refused(c, swapped), "same path, another stored text"

        unlisted = copy.deepcopy(good)
        _version(unlisted, ids["seed"])["prompt_map"]["prompts/extra.md"] = pm[path]
        assert "does not list the spec's prompts" in _refused(c, unlisted)
    assert _counts(database_url) == (0, 0, 0)


@pytest.mark.parametrize(
    "break_diff",
    [
        lambda d: d.update(ops={"op": "add_expert"}),
        lambda d: d.update(ops=[{"id": "x"}]),
        lambda d: d.update(rendered="x"),
        lambda d: d.update(rendered={"prompts": {"expert:fiscal": 3}}),
        lambda d: d.update(rationale=["x"]),
        lambda d: next(o for o in d["ops"] if o["op"] == "add_expert").pop("domain"),
    ],
    ids=[
        "ops-object",
        "op-unnamed",
        "rendered-string",
        "prompt-diff-not-text",
        "rationale-list",
        "expert-no-domain",
    ],  # fmt: skip
)
def test_a_diff_the_page_cannot_read_is_refused(database_url, tmp_path, break_diff):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        bad = asyncio.run(_export(database_url))
        _clear(database_url)
        break_diff(_version(bad, ids["topology"])["diff"])
        _refused(c, bad)
        assert c.get("/evolution/lineage", headers=AUTH).status_code == 200


def test_the_cli_lists_what_it_sends_and_posts_with_the_token(
    database_url, tmp_path, capsys, monkeypatch
):
    client, ids = _client(database_url, tmp_path)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    totals = _counts(database_url)
    assert cli.main(["evolve", "publish", "--dry-run", "--json"]) == cli.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert (out["versions"], out["metrics"], out["prompts"]) == totals
    assert "@" not in out["source"] and out["source"].endswith(database_url.rsplit("/", 1)[1])
    assert {v["version_id"] for v in out["version_list"]} >= {ids["seed"], ids["topology"]}

    assert cli.main(["evolve", "publish", "--dry-run"]) == cli.EXIT_OK
    text = capsys.readouterr().out
    assert ids["topology"] in text and "not posted" in text

    monkeypatch.delenv("WOMM_API_URL", raising=False)
    assert cli.main(["evolve", "publish"]) == cli.EXIT_USAGE
    assert "WOMM_API_URL" in capsys.readouterr().err

    monkeypatch.setenv("WOMM_API_TOKEN", TOKEN)
    with client as c:
        posted = post(asyncio.run(_export(database_url)), "http://testserver", client=c)
    assert posted["versions"]["identical"] == totals[0] and posted["versions"]["inserted"] == 0


def test_the_cli_says_when_there_is_nothing_to_publish(database_url, capsys, monkeypatch):
    db = Database(database_url)

    async def migrate():
        await db.open()
        try:
            await db.migrate()
        finally:
            await db.close()

    asyncio.run(migrate())
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["evolve", "publish", "--dry-run"]) == cli.EXIT_USAGE
    assert "nothing to publish" in capsys.readouterr().err
