"""Evolution page API (U9; R36 page 4, R37 visibility): lineage, candidate detail and config
diff, read from the archive and the published decision summaries only."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from womm.api.app import create_app
from womm.api.db import Database
from womm.api.e2e import PROMPT_RATIONALE, WORKFORCE_GLOSS, seed_evolution
from womm.data.fixtures import load_fixture
from womm.llm.fake import FakeBackend

from ..graph.conftest import fake_sv
from .test_app import AUTH, _settings


async def _seed(database_url):
    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        return await seed_evolution(db)
    finally:
        await db.close()


def _client(database_url, tmp_path, publish=False, seed=True):
    ids = asyncio.run(_seed(database_url)) if seed else {}
    policy = tmp_path / "policy.yaml"
    policy.write_text(f"publish_summary: {str(publish).lower()}\n")
    app = create_app(
        _settings(database_url), sv=fake_sv(), fixture=load_fixture(),
        backends={"fake": FakeBackend({})}, promotion_policy_path=policy,
    )  # fmt: skip
    return TestClient(app), ids


def test_lineage_collapses_twins_and_badges_each_candidate(database_url, tmp_path):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        body = c.get("/evolution/lineage", headers=AUTH).json()
    nodes = {n["version_id"]: n for n in body["nodes"]}
    assert set(nodes) == {ids["seed"], ids["prompt"], ids["topology"], ids["rejected"]}
    assert nodes[ids["seed"]]["twins"] == [ids["seed_api"]] and nodes[ids["seed"]]["badges"] == [
        "seed"
    ]
    topo = nodes[ids["topology"]]
    assert topo["parent_id"] == ids["prompt"] and topo["twins"] == [ids["topology_api"]]
    # A weak pass is "not rejected" on the page (owner 2026-10-15); the machine field stays.
    assert topo["badges"] == ["topology", "not-rejected"] and topo["new_expert"] == "workforce"
    assert topo["decision"] == "promoted"
    assert topo["label"] == "not rejected (weak threshold: directional)"
    assert topo["r37_regression"] is False
    rejected = nodes[ids["rejected"]]
    assert rejected["badges"] == ["gepa", "rejected", "dev-only"]
    assert rejected["r37_regression"] is True
    assert (
        nodes[ids["prompt"]]["decision"] is None and nodes[ids["prompt"]]["r37_regression"] is None
    )
    assert body["publish_summary"] is False


def test_candidate_detail_with_a_new_expert_and_a_published_decision(database_url, tmp_path):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        body = c.get(f"/evolution/candidates/{ids['topology']}", headers=AUTH).json()
        via_twin = c.get(f"/evolution/candidates/{ids['topology_api']}", headers=AUTH).json()
    assert via_twin == body, "an api twin opens its dev candidate"
    expert = body["new_expert"]
    assert expert["id"] == "workforce" and expert["router_gloss"] == WORKFORCE_GLOSS
    target = {"kind": "missed_impact", "category": "social_environmental", "owner": "none"}
    assert expert["target_pattern"] == target
    assert "Workforce expert" in expert["prompt_text"]
    assert expert["rationale"] == body["rationale"] and "Workforce" in body["rationale"]
    assert body["prompt_rationales"] == {}
    assert body["summary"]["experts_added"] == ["workforce"]
    assert [e["id"] for e in body["experts"]][-1] == "workforce"
    (val,) = body["metrics"]
    assert val["split"] == "val" and val["metrics"]["coverage"]["mean"] == pytest.approx(0.7)
    assert val["metrics"]["coverage"]["noise_sd"] == pytest.approx(0.02)
    holdout = body["holdout"]
    assert holdout["status"] == "published"
    (decision,) = holdout["decisions"]
    assert decision["label"] == "not rejected (weak threshold: directional)"
    assert decision["cross_judge"] == {
        "label": "agrees", "mean_delta": 0.05, "ci95_low": None, "ci95_high": None,
        "judge": "claude_code:claude-opus-5-5", "gate_delta": 0.06, "rescored_on": "2026-10-15",
    }  # fmt: skip
    assert decision["candidate_version"] == ids["topology_api"]
    assert body["r37"]["status"] == "available" and body["r37"]["regression"] is False
    assert body["r37"]["reference_version"] == ids["seed"]


def test_a_prompt_candidate_gives_one_reason_per_edited_role(database_url, tmp_path):
    """The prompt stage stores {role: reason} (gepa_adapter); the detail must not 500 on it."""
    client, ids = _client(database_url, tmp_path)
    with client as c:
        resp = c.get(f"/evolution/candidates/{ids['prompt']}", headers=AUTH)
        rejected = c.get(f"/evolution/candidates/{ids['rejected']}", headers=AUTH).json()
    assert resp.status_code == 200
    body = resp.json()
    assert body["prompt_rationales"] == {"expert:fiscal": PROMPT_RATIONALE}
    assert body["rationale"] is None and body["new_expert"] is None
    assert rejected["prompt_rationales"] == {} and rejected["rationale"] is None, "none stored"


def test_a_malformed_rationale_is_dropped_not_an_error(database_url, tmp_path):
    import psycopg

    client, ids = _client(database_url, tmp_path)
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "UPDATE sv_archive SET diff = diff || %s::jsonb WHERE version_id = %s",
            (json.dumps({"rationale": {"planner": "", "expert:legal": 3, "synthesis": "ok"}}),
             ids["prompt"]),
        )  # fmt: skip
    with client as c:
        body = c.get(f"/evolution/candidates/{ids['prompt']}", headers=AUTH).json()
    assert body["prompt_rationales"] == {"synthesis": "ok"}


def test_r37_regression_is_visible_and_the_decision_unchanged(database_url, tmp_path):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        body = c.get(f"/evolution/candidates/{ids['rejected']}", headers=AUTH).json()
    assert body["r37"]["regression"] is True and "decision is unchanged" in body["r37"]["message"]
    assert body["holdout"]["decisions"][0]["reasons"] == ["grounding_regression"]
    assert body["holdout"]["decisions"][0]["cross_judge"] is None, "not run"
    assert body["node"]["decision"] == "rejected"


def test_only_a_statistical_promotion_is_badged_promoted(database_url, tmp_path):
    import psycopg

    client, ids = _client(database_url, tmp_path)
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "UPDATE promotion_decisions SET mode = 'statistical',"
            " label = 'promoted (holdout, bootstrap CI)' WHERE decision = 'promoted'"
        )
    with client as c:
        body = c.get("/evolution/lineage", headers=AUTH).json()
    nodes = {n["version_id"]: n for n in body["nodes"]}
    assert nodes[ids["topology"]]["badges"] == ["topology", "promoted"]


def test_an_aborted_comparison_is_badged_inconclusive(database_url, tmp_path):
    import psycopg

    client, ids = _client(database_url, tmp_path)
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "UPDATE promotion_decisions SET decision = 'rejected',"
            " reasons = '[\"inconclusive\"]'::jsonb WHERE decision = 'promoted'"
        )
    with client as c:
        body = c.get("/evolution/lineage", headers=AUTH).json()
    nodes = {n["version_id"]: n for n in body["nodes"]}
    assert nodes[ids["topology"]]["badges"] == ["topology", "inconclusive"]


@pytest.mark.parametrize(("publish", "status", "text"), [
    (False, "sealed", "not submitted to holdout"),
    (True, "not_submitted", "Not submitted to holdout."),
])  # fmt: skip
def test_a_candidate_without_a_decision(database_url, tmp_path, publish, status, text):
    client, ids = _client(database_url, tmp_path, publish=publish)
    with client as c:
        body = c.get(f"/evolution/candidates/{ids['prompt']}", headers=AUTH).json()
    assert body["holdout"]["status"] == status and text in body["holdout"]["message"]
    assert body["holdout"]["decisions"] == [] and body["metrics"]
    assert body["r37"]["status"] == "not_run" and body["new_expert"] is None


def test_config_diff_and_unknown_ids(database_url, tmp_path):
    client, ids = _client(database_url, tmp_path)
    with client as c:
        diff = c.get(f"/evolution/candidates/{ids['prompt']}/diff", headers=AUTH).json()
        assert diff["parent_id"] == ids["seed"] and list(diff["prompts"]) == ["expert:fiscal"]
        assert "+Quantify every cost you name." in diff["prompts"]["expert:fiscal"]
        assert diff["summary"]["prompts_changed"] == ["expert:fiscal"]
        for path in ("/evolution/candidates/sv_nope", "/evolution/candidates/sv_nope/diff"):
            assert c.get(path, headers=AUTH).status_code == 404
        assert c.get("/evolution/lineage").status_code == 401


def test_an_empty_archive(database_url, tmp_path):
    client, _ = _client(database_url, tmp_path, seed=False)
    with client as c:
        assert c.get("/evolution/lineage", headers=AUTH).json()["nodes"] == []


def test_holdout_fields_carry_no_case_ids(database_url, tmp_path):
    """Schema check: the decision summary and every response are aggregates; no field is a
    case id or a scenario id, and no value names one."""
    from womm.api.evolution import CandidateDetail, DecisionOut, Lineage

    def fields(model, seen=None):
        seen = seen or set()
        out = set()
        for name, info in model.model_fields.items():
            out.add(name)
            ann = info.annotation
            for arg in (*getattr(ann, "__args__", ()), ann):
                if isinstance(arg, type) and hasattr(arg, "model_fields") and arg not in seen:
                    seen.add(arg)
                    out |= fields(arg, seen)
        return out

    for model in (DecisionOut, Lineage, CandidateDetail):
        names = fields(model)
        assert not {n for n in names if "case_id" in n or "scenario" in n}, model
    client, ids = _client(database_url, tmp_path)
    with client as c:
        dumped = json.dumps([c.get(f"/evolution/candidates/{v}", headers=AUTH).json()["holdout"]
                             for v in ids.values()])  # fmt: skip
    assert "case_" not in dumped and "eval_" not in dumped


async def _add_diff_score(database_url, version_id, mean, judge):
    from womm.evolve.archive import Archive

    db = Database(database_url)
    await db.open()
    try:
        row = {"level": "case", "subject": "diff_demo_penalties_amended", "metric": "coverage",
               "n": 3, "mean": mean, "sd": 0.01}  # fmt: skip
        await Archive(db).record_metrics(
            version_id, "diff_check", [row], batch_id=f"rb_{judge}", judge_version=judge,
            git_sha="g", full_split=True,
        )  # fmt: skip
    finally:
        await db.close()


def test_r37_on_the_page_compares_under_a_shared_judge_only(database_url, tmp_path):
    """A newer score under a judge the reference never had does not hide the regression the
    shared judge shows; with no shared judge there is no flag."""
    client, ids = _client(database_url, tmp_path)
    asyncio.run(_add_diff_score(database_url, ids["rejected"], 0.95, "jv_zz_new"))
    with client as c:
        r37 = c.get(f"/evolution/candidates/{ids['rejected']}", headers=AUTH).json()["r37"]
        assert r37["judge_version"] == "jv_e2e" and r37["regression"] is True
        assert r37["score"]["mean"] == pytest.approx(0.31)
    asyncio.run(_add_diff_score(database_url, ids["prompt"], 0.1, "jv_zz_new"))
    with client as c:
        r37 = c.get(f"/evolution/candidates/{ids['prompt']}", headers=AUTH).json()["r37"]
    assert r37["status"] == "available" and r37["regression"] is None
    assert r37["judge_version"] is None and "judge both versions share" in r37["message"]


def test_the_lineage_reads_no_spec_diff_or_train_val_bodies(database_url, tmp_path):
    """The lineage query carries neither prompt diffs nor specs, and only diff-check metrics;
    a candidate's spec, diff and train/val metrics are fetched for that candidate only."""
    ids = asyncio.run(_seed(database_url))

    async def read():
        db = Database(database_url)
        await db.open()
        try:
            mine = await db.evolution_metrics(version_ids=[ids["topology"]], splits=["val"])
            return (await db.evolution_archive(), await db.evolution_metrics(),
                    await db.evolution_candidate(ids["topology"]), mine)  # fmt: skip
        finally:
            await db.close()

    rows, metrics, full, mine = asyncio.run(read())
    assert all({"spec", "diff", "proposer"}.isdisjoint(r) for r in rows)
    assert {r["version_id"]: r["new_expert"] for r in rows}[ids["topology"]] == "workforce"
    assert {m["split"] for m in metrics} == {"diff_check"}
    assert full["diff"]["ops"][0]["op"] == "add_expert" and full["spec"]["experts"]
    assert {(m["version_id"], m["split"]) for m in mine} == {(ids["topology"], "val")}


def test_a_target_pattern_archived_as_its_key_is_shown_as_fields(database_url, tmp_path):
    """The topology stage archives ``target_pattern`` as ``kind/category/owner``."""
    import psycopg

    client, ids = _client(database_url, tmp_path)
    with psycopg.connect(database_url) as conn:
        conn.execute(
            "UPDATE sv_archive SET diff = jsonb_set(diff, '{target_pattern}',"
            " '\"missed_impact/social_environmental/none\"') WHERE version_id = %s",
            (ids["topology"],),
        )
    with client as c:
        body = c.get(f"/evolution/candidates/{ids['topology']}", headers=AUTH).json()
    assert body["new_expert"]["target_pattern"] == {
        "kind": "missed_impact", "category": "social_environmental", "owner": "none"
    }  # fmt: skip


def test_lineage_nodes_carry_val_scores_against_their_parent(database_url, tmp_path):
    """Each tree node shows its val coverage and citation rate next to its parent's under a judge
    both share, so a candidate that gains coverage but loses citations reads as such; a change
    within the run-to-run noise is marked as such, never as a gain or a loss."""
    client, ids = _client(database_url, tmp_path)
    with client as c:
        body = c.get("/evolution/lineage", headers=AUTH).json()
    nodes = {n["version_id"]: n["score"] for n in body["nodes"]}
    seed = nodes[ids["seed"]]
    assert seed["split"] == "val" and seed["judge_version"] == "jv_e2e"
    cov = seed["metrics"]["coverage"]
    assert cov["mean"] == pytest.approx(0.55) and cov["noise"] == pytest.approx(0.05)
    assert cov["parent_mean"] is None and cov["delta"] is None and cov["beyond_noise"] is None
    prompt = nodes[ids["prompt"]]["metrics"]["coverage"]
    assert prompt["parent_mean"] == pytest.approx(0.55) and prompt["delta"] == pytest.approx(0.07)
    assert prompt["beyond_noise"] is True  # 0.07 > the larger noise band, the seed's 0.05
    topo = nodes[ids["topology"]]["metrics"]["coverage"]
    assert topo["parent_mean"] == pytest.approx(0.62) and topo["delta"] == pytest.approx(0.08)
    rejected = nodes[ids["rejected"]]["metrics"]
    assert rejected["coverage"]["delta"] == pytest.approx(0.10)
    assert rejected["grounding"]["mean"] == pytest.approx(0.81)
    assert rejected["grounding"]["delta"] == pytest.approx(-0.10)
    assert rejected["grounding"]["beyond_noise"] is True


def test_a_node_score_change_within_noise_or_under_another_judge(database_url, tmp_path):
    """No delta across two different judges; a delta inside the noise band is not a change."""
    client, ids = _client(database_url, tmp_path)
    asyncio.run(_add_val_scores(database_url, ids["prompt"], {"coverage": [0.57, 0.55, 0.59]}))
    asyncio.run(_add_val_scores(database_url, ids["rejected"], {"coverage": [0.9] * 3}, "jv_zz"))
    with client as c:
        nodes = {n["version_id"]: n["score"] for n in c.get("/evolution/lineage", headers=AUTH)
                 .json()["nodes"]}  # fmt: skip
    prompt = nodes[ids["prompt"]]["metrics"]["coverage"]
    assert prompt["delta"] == pytest.approx(0.02) and prompt["beyond_noise"] is False
    # The rejected candidate's newest val batch is under jv_zz, which the seed never had: its
    # score is shown under the judge it shares with the seed, so the delta stays comparable.
    rejected = nodes[ids["rejected"]]
    assert rejected["judge_version"] == "jv_e2e"
    assert rejected["metrics"]["coverage"]["delta"] == pytest.approx(0.10)
    assert rejected["metrics"]["coverage"]["beyond_noise"] is True


async def _add_val_scores(database_url, version_id, values, judge="jv_e2e", split="val"):
    from womm.api.e2e import _split_rows
    from womm.evolve.archive import Archive

    db = Database(database_url)
    await db.open()
    try:
        await Archive(db).record_metrics(
            version_id, split, _split_rows(values), batch_id=f"rb_{split}_new_{judge}",
            judge_version=judge, git_sha="e2e", full_split=True,
        )  # fmt: skip
    finally:
        await db.close()


def test_node_scores_fall_back_to_twins_and_train_and_need_both_noise_bands(database_url, tmp_path):
    """A node scored only on its api twin uses the twin's scores; with no judge shared on val it
    compares on train when train shares one; a change is called beyond noise only when both
    versions have a noise band, never on one side's band alone."""
    client, ids = _client(database_url, tmp_path)
    # The topology node's newest val is under a judge its parent never had; both have train
    # under jv_e2e, so the tree compares on train.
    asyncio.run(_add_val_scores(database_url, ids["topology"], {"coverage": [0.9] * 3}, "jv_new",
                                split="train"))  # fmt: skip
    asyncio.run(_delete_scores(database_url, ids["topology"]))
    asyncio.run(_add_val_scores(database_url, ids["topology"], {"coverage": [0.8] * 3}, "jv_zz"))
    asyncio.run(_add_val_scores(database_url, ids["topology"], {"coverage": [0.66, 0.7, 0.68]},
                                split="train"))  # fmt: skip
    asyncio.run(_add_val_scores(database_url, ids["prompt"], {"coverage": [0.6, 0.62, 0.61]},
                                split="train"))  # fmt: skip
    # The rejected node's parent (the seed) has one val run only on a fresh judge: no band.
    asyncio.run(_add_val_scores(database_url, ids["seed"], {"coverage": [0.5]}, "jv_one"))
    asyncio.run(_add_val_scores(database_url, ids["rejected"], {"coverage": [0.6, 0.62, 0.61]},
                                "jv_one"))  # fmt: skip
    with client as c:
        nodes = {n["version_id"]: n["score"] for n in c.get("/evolution/lineage", headers=AUTH)
                 .json()["nodes"]}  # fmt: skip
    topo = nodes[ids["topology"]]
    assert topo["split"] == "train" and topo["judge_version"] == "jv_e2e"
    assert topo["metrics"]["coverage"]["delta"] == pytest.approx(0.07)
    rejected = nodes[ids["rejected"]]
    assert rejected["judge_version"] == "jv_one"
    cov = rejected["metrics"]["coverage"]
    assert cov["delta"] == pytest.approx(0.11) and cov["noise"] == pytest.approx(0.01)
    assert cov["beyond_noise"] is None  # the seed's single run gives no band to compare against


def test_a_node_scored_on_its_twin_only(database_url, tmp_path):
    client, ids = _client(database_url, tmp_path)
    asyncio.run(_delete_scores(database_url, ids["topology"]))
    asyncio.run(_add_val_scores(database_url, ids["topology_api"], {"coverage": [0.7, 0.72, 0.68]}))
    with client as c:
        nodes = {n["version_id"]: n["score"] for n in c.get("/evolution/lineage", headers=AUTH)
                 .json()["nodes"]}  # fmt: skip
    cov = nodes[ids["topology"]]["metrics"]["coverage"]
    assert cov["mean"] == pytest.approx(0.70) and cov["parent_mean"] == pytest.approx(0.62)


def test_a_rounds_to_zero_change_is_never_beyond_noise(database_url, tmp_path):
    """Identical runs give a zero band; the floor keeps a 0.001 change from reading as a gain."""
    client, ids = _client(database_url, tmp_path)
    asyncio.run(_add_val_scores(database_url, ids["seed"], {"coverage": [0.6] * 3}, "jv_flat"))
    asyncio.run(_add_val_scores(database_url, ids["prompt"], {"coverage": [0.601] * 3}, "jv_flat"))
    with client as c:
        nodes = {n["version_id"]: n["score"] for n in c.get("/evolution/lineage", headers=AUTH)
                 .json()["nodes"]}  # fmt: skip
    cov = nodes[ids["prompt"]]["metrics"]["coverage"]
    assert cov["delta"] == pytest.approx(0.001) and cov["beyond_noise"] is False


def test_the_lineage_reads_one_pooled_noise_row_per_metric_not_case_rows(database_url, tmp_path):
    ids = asyncio.run(_seed(database_url))

    async def read():
        db = Database(database_url)
        await db.open()
        try:
            return await db.evolution_node_scores()
        finally:
            await db.close()

    rows = asyncio.run(read())
    keys = [(r["version_id"], r["split"], r["judge_version"], r["metric"]) for r in rows]
    assert len(keys) == len(set(keys))  # one row per version, split, judge and metric
    seed = {r["metric"]: r for r in rows if r["version_id"] == ids["seed"]}
    assert set(seed) == {"coverage", "grounding"}
    assert seed["coverage"]["noise"] == pytest.approx(0.05)


async def _delete_scores(database_url, version_id):
    db = Database(database_url)
    await db.open()
    try:
        async with db.pool.connection() as conn:
            await conn.execute(
                "DELETE FROM sv_metrics WHERE version_id = %s AND split = 'val'", (version_id,)
            )
    finally:
        await db.close()
