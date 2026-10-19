"""U6 sealed holdout store on Postgres (throwaway databases; skipped without a server):
holdout-only migrations, idempotent import, the import script, the compare audit table and
AE3 (the train/val failure store never sees a holdout case)."""

import hashlib
import importlib.util
import sys

import psycopg
import pytest
import yaml

from womm.api.db import Database
from womm.config import REPO_ROOT
from womm.data import fixtures as fixtures_module
from womm.decisions.stub import StubDecisionService
from womm.eval import drafting as drafting_module
from womm.eval import holdout
from womm.models.run import CodeIdentity

from ..eval import holdout_factory as hf
from ..graph.conftest import fake_sv
from .conftest import ADMIN_URL

spec = importlib.util.spec_from_file_location(
    "import_holdout_case", REPO_ROOT / "scripts/import_holdout_case.py"
)
importer = importlib.util.module_from_spec(spec)
sys.modules["import_holdout_case"] = importer
spec.loader.exec_module(importer)


@pytest.fixture
def holdout_url(database_url):
    """A second throwaway database, separate from the API's ``database_url``."""
    name = f"womm_holdout_{database_url.rsplit('_', 1)[-1]}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    yield ADMIN_URL.rsplit("/", 1)[0] + f"/{name}"
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures_module, "FIXTURES_ROOT", hf.make_fixtures_root(tmp_path))
    monkeypatch.setattr(drafting_module, "CACHE_DIR", tmp_path / "cache")
    (tmp_path / "golden").mkdir()
    return tmp_path


def _bundle(roots, name="prop_alpha"):
    return holdout.prepare_import(hf.handoff(name), hf.scenario_specs(), hf.ia_index(),
                                  golden_dir=roots / "golden")  # fmt: skip


def _rows(url, sql):
    with psycopg.connect(url) as conn:
        return conn.execute(sql).fetchall()


async def test_holdout_migrations_are_separate_from_the_api(database_url, holdout_url):
    api = Database(database_url)
    await api.open()
    try:
        await api.migrate()
    finally:
        await api.close()
    assert _rows(database_url, "SELECT 1 FROM pg_namespace WHERE nspname = 'holdout'") == []
    store = holdout.HoldoutStore(holdout_url)
    applied = [
        "001_holdout",
        "002_compare_progress",
        "003_budget_reservations",
        "004_compare_progress_run",
        "005_rescore_audit",
    ]
    assert await store.migrate() == applied
    assert await store.migrate() == []
    assert _rows(holdout_url, "SELECT to_regclass('public.runs')") == [(None,)]


async def test_import_is_idempotent_and_stores_case_scenario_and_ia_ids(roots, holdout_url):
    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    bundle = _bundle(roots)
    assert await store.import_case(bundle) == "inserted"
    assert await store.import_case(bundle) == "unchanged"
    assert await store.import_case(_bundle(roots)) == "unchanged"
    assert _rows(holdout_url, "SELECT count(*) FROM holdout.cases") == [(1,)]
    (ia,) = _rows(holdout_url, "SELECT fixture, ia_celex, rsb_ref FROM holdout.ia_references")
    assert ia == ("prop_alpha", hf.ia_index()["prop_alpha"].ia_celex,
                  hf.ia_index()["prop_alpha"].rsb_ref)  # fmt: skip
    (sc,) = _rows(holdout_url, "SELECT scenario_id, articles FROM holdout.scenarios")
    assert sc == (hf.SCENARIO_ID, hf.ARTICLES)

    edited = hf.handoff("prop_alpha")
    edited["case"]["notes"] = "re-verified"
    changed = holdout.prepare_import(edited, hf.scenario_specs(), hf.ia_index(),
                                     golden_dir=roots / "golden")  # fmt: skip
    assert await store.import_case(changed) == "updated"
    assert _rows(holdout_url, "SELECT body->>'notes' FROM holdout.cases") == [("re-verified",)]
    counts = dict(_rows(holdout_url, "SELECT category, items FROM holdout.category_counts"))
    assert sum(counts.values()) == len(bundle.case.expected_impacts)


async def test_sealed_counts_are_cases_and_proposals(roots, holdout_url):
    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    assert await store.sealed_counts() == (0, 0)
    await store.import_case(_bundle(roots))
    assert await store.sealed_counts() == (1, 1)


async def test_a_changed_ia_record_is_not_unchanged(roots, holdout_url):
    """P3-2: 'unchanged' needs the IA record to match too, not only the case and scenario."""
    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    assert await store.import_case(_bundle(roots)) == "inserted"
    index = hf.ia_index()
    index["prop_alpha"] = index["prop_alpha"].model_copy(update={"rsb_ref": "SEC(2099) 77"})
    moved = holdout.prepare_import(hf.handoff("prop_alpha"), hf.scenario_specs(), index,
                                   golden_dir=roots / "golden")  # fmt: skip
    assert await store.import_case(moved) == "updated"
    assert _rows(holdout_url, "SELECT rsb_ref FROM holdout.ia_references") == [("SEC(2099) 77",)]
    assert await store.import_case(moved) == "unchanged"


def test_import_script_seals_then_deletes_the_plaintext(roots, holdout_url, monkeypatch):
    private = roots / "private"
    drafts = roots / "cache" / "drafts"
    drafts.mkdir(parents=True)
    cid = hf.case_id("prop_alpha")
    draft = drafts / f"{cid}.yaml"
    draft.write_text("plaintext holdout draft\n")
    (drafts / f"{cid}.decisions.yaml").write_text("x: {decision: verified}\n")
    sha = hashlib.sha256(draft.read_bytes()).hexdigest()
    handoff = roots / "cache" / "holdout_import" / f"{cid}.yaml"
    handoff.parent.mkdir(parents=True)
    handoff.write_text(yaml.safe_dump(hf.handoff("prop_alpha", draft_sha=sha)))
    argv = [str(handoff), "--scenarios", str(hf.write_scenarios(private)),
            "--private-dir", str(private), "--ia-index", str(hf.write_ia_index(private)),
            "--golden-dir", str(roots / "golden"), "--drafts-dir", str(drafts)]  # fmt: skip
    monkeypatch.setenv("HOLDOUT_DATABASE_URL", holdout_url)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert importer.main(argv) == 0
    assert not handoff.exists() and not draft.exists()
    assert not (drafts / f"{cid}.decisions.yaml").exists()
    assert _rows(holdout_url, "SELECT case_id, verified_by FROM holdout.cases") == [(cid, "jdoe")]

    # Re-importing the same verified case (a fresh handoff) changes nothing.
    handoff.write_text(yaml.safe_dump(hf.handoff("prop_alpha", draft_sha=sha)))
    assert importer.main(argv) == 0
    assert _rows(holdout_url, "SELECT count(*) FROM holdout.cases") == [(1,)]


def test_import_script_refuses_without_its_own_database(roots, monkeypatch, capsys):
    monkeypatch.delenv("HOLDOUT_DATABASE_URL", raising=False)
    assert importer.main([str(roots / "missing.yaml")]) == 2
    assert "HOLDOUT_DATABASE_URL" in capsys.readouterr().err


def test_import_script_keeps_the_handoff_when_validation_fails(roots, monkeypatch, capsys):
    """Every handoff is validated before any database connection or deletion."""
    private = roots / "private"
    handoff = roots / "cache" / "handoff.yaml"
    handoff.parent.mkdir(parents=True)
    bad = hf.handoff("prop_alpha")
    bad["case"]["scenario_id"] = "eval_unknown"
    handoff.write_text(yaml.safe_dump(bad))
    monkeypatch.setenv("HOLDOUT_DATABASE_URL", ADMIN_URL.rsplit("/", 1)[0] + "/unused")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    argv = [str(handoff), "--scenarios", str(hf.write_scenarios(private)),
            "--private-dir", str(private), "--ia-index", str(hf.write_ia_index(private)),
            "--golden-dir", str(roots / "golden")]  # fmt: skip
    assert importer.main(argv) == 2
    assert handoff.exists() and "no local holdout scenario" in capsys.readouterr().err


async def test_compare_on_the_store_writes_only_the_audit_table(roots, database_url, holdout_url):
    """Happy path end to end, and AE3: after a holdout scoring run the train/val failure store
    (the Improvement Planner's source) holds no holdout case id or expected-impact text."""
    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    for name in hf.PROPOSALS:
        await store.import_case(_bundle(roots, name))
    api = Database(database_url)
    await api.open()
    await api.migrate()
    await api.close()

    cand, base = fake_sv(), fake_sv()
    backends = hf.compare_backends(runs_per_version=2)
    result = await holdout.compare(
        cand, base, 1, store=store,
        backends=lambda sv: backends["candidate"] if sv is cand else backends["baseline"],
        decisions=StubDecisionService(), code=CodeIdentity(git_sha="abc", dirty=False),
        n_boot=100,
    )  # fmt: skip
    assert result.n_cases == 2 and result.deltas["coverage"].mean_delta > 0

    (audit,) = _rows(holdout_url, "SELECT candidate_version, git_sha, result FROM "
                                  "holdout.compare_audit")  # fmt: skip
    assert audit[0] == cand.version_id and audit[1] == "abc"
    assert audit[2] == result.model_dump(mode="json")

    api = Database(database_url)
    await api.open()
    try:
        assert await api.list_failures(cand.version_id) == []
    finally:
        await api.close()
    dump = str(_rows(database_url, "SELECT row_to_json(f) FROM failures f")) + str(
        _rows(database_url, "SELECT row_to_json(r) FROM runs r")
    )
    for name in hf.PROPOSALS:
        assert hf.case_id(name) not in dump
    assert hf.SECRET_IMPACT not in dump


async def test_a_gate_comparison_stores_its_runs_for_a_rescore(roots, holdout_url):
    from womm.models.run import RunResult

    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    for name in hf.PROPOSALS:
        await store.import_case(_bundle(roots, name))
    cand, base = fake_sv(), fake_sv().model_copy(update={"version_id": "sv_base_x"})
    backends = hf.compare_backends(runs_per_version=2)
    code = CodeIdentity(git_sha="abc", dirty=False)
    await holdout.compare(
        cand, base, 1, store=store,
        backends=lambda sv: backends["candidate"] if sv is cand else backends["baseline"],
        decisions=StubDecisionService(), code=code, n_boot=50, checkpoint=True, gate_id="g1",
    )  # fmt: skip
    stored = await store.stored_runs("g1")
    assert stored["audit"]["repetitions"] == 1 and stored["audit"]["judge_version"]
    assert len(stored["rows"]) == 4
    for row in stored["rows"]:
        assert row["score"] is not None
        RunResult.model_validate(row["run"])
    assert await store.stored_runs("g_missing") is None

    # A row saved before runs were stored: not reused by the cache, reported as run None,
    # filled in by the next save, never overwritten once present.
    _rows(holdout_url, "UPDATE holdout.compare_progress SET run = NULL WHERE version_id ="
                       " 'sv_base_x' RETURNING 1")  # fmt: skip
    stored = await store.stored_runs("g1")
    assert sum(r["run"] is None for r in stored["rows"]) == 2
    jv, cv = stored["audit"]["judge_version"], stored["audit"]["code_versions"]["sv_base_x"]
    assert await store.load_progress(["sv_base_x"], jv, cv) == {}
    row = next(r for r in stored["rows"] if r["version_id"] == "sv_base_x")
    from womm.eval.evaluators import CaseScore

    score = CaseScore.model_validate(row["score"])
    await store.save_progress("sv_base_x", jv, cv, row["case_key"], 1, score, run={"x": 1})
    await store.save_progress("sv_base_x", jv, cv, row["case_key"], 1, score, run={"x": 2})
    again = {(r["case_key"], r["version_id"]): r["run"] for r in
             (await store.stored_runs("g1"))["rows"]}  # fmt: skip
    assert again[(row["case_key"], "sv_base_x")] == {"x": 1}


async def test_rescore_progress_and_audit_on_the_store(holdout_url):
    from womm.eval.evaluators import CaseScore

    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    score = CaseScore(case_id="kabc", scenario_id="s", outcome="scored", coverage=0.5)
    await store.save_rescore_progress("g1", "jv_c", "sv_a", "kabc", 1, score)
    await store.save_rescore_progress("g1", "jv_c", "sv_a", "kabc", 1, score)  # idempotent
    assert await store.load_rescore_progress("g1", "jv_c") == {("sv_a", "kabc", 1): score}
    assert await store.load_rescore_progress("g1", "jv_other") == {}
    assert await store.current_rescore("g1") is None
    await store.record_rescore("g1", "jv_c", {"label": "agrees"})
    assert (await store.current_rescore("g1"))["label"] == "agrees"
    with pytest.raises(holdout.HoldoutError, match="already"):
        await store.record_rescore("g1", "jv_c", {"label": "agrees"})
    await store.record_rescore("g1", "jv_c", {"label": "cross-judge-sensitive"}, replace=True)
    assert (await store.current_rescore("g1"))["label"] == "cross-judge-sensitive"
    assert _rows(holdout_url, "SELECT count(*) FILTER (WHERE superseded) FROM"
                              " holdout.rescore_audit") == [(1,)]  # fmt: skip
    await store.clear_rescore_progress("g1", "jv_c")
    assert await store.load_rescore_progress("g1", "jv_c") == {}


def test_import_script_refuses_a_handoff_outside_the_cache(roots, monkeypatch, capsys):
    handoff = roots / "handoff.yaml"
    handoff.write_text(yaml.safe_dump(hf.handoff("prop_alpha")))
    monkeypatch.setenv("HOLDOUT_DATABASE_URL", ADMIN_URL.rsplit("/", 1)[0] + "/unused")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    private = roots / "private"
    argv = [str(handoff), "--scenarios", str(hf.write_scenarios(private)),
            "--private-dir", str(private), "--ia-index", str(hf.write_ia_index(private)),
            "--golden-dir", str(roots / "golden")]  # fmt: skip
    assert importer.main(argv) == 2
    assert handoff.exists() and "only under .cache/" in capsys.readouterr().err


# Applied holdout migrations are never edited either (HoldoutStore.migrate skips applied ones).
FROZEN_HOLDOUT_MIGRATIONS = {
    "001_holdout.sql": "75bc8c722a906520cd5be9f32fc85134e443c21cd1ba173dd0dc4f03a3649cab",
    "002_compare_progress.sql": "9e4a3767bc12c4f5a2da1d61b621c8d5c51102dd96f94671eb8aca132ab2a54e",
    "003_budget_reservations.sql": (
        "f6f9c310fbedd22324d4e7317a7592f0009bcf90893a681dbeaf58f0730a3a2f"
    ),
    "004_compare_progress_run.sql": (
        "572a8f6c421ec0de2a959f3bb8f2a3312c6c28251a52a3fbf8519f78f903de60"
    ),
    "005_rescore_audit.sql": ("e804b88e1cc4bd8d219c2e283a5eb81437185c2a9db7dbdd5d5032152b281913"),
}


def test_applied_holdout_migrations_are_frozen():
    for name, digest in FROZEN_HOLDOUT_MIGRATIONS.items():
        actual = hashlib.sha256((holdout.MIGRATIONS_DIR / name).read_bytes()).hexdigest()
        assert actual == digest, f"{name} changed: add a new holdout migration instead"


async def test_gate_bookkeeping_on_the_store(roots, holdout_url):
    """U7: compare progress round-trips, decisions sit on the gate's audit row, the budget
    counts only budget-consuming decisions, and aborted pairs are counted."""
    from womm.eval.evaluators import CaseScore

    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    score = CaseScore(case_id="kabc", scenario_id="s", outcome="scored", coverage=0.5)
    await store.save_progress("sv_a", "jv", "sha", "kabc", 1, score, run={"run_id": "r"})
    await store.save_progress("sv_a", "jv", "sha", "kabc", 1, score, run={"run_id": "r"})
    assert await store.load_progress(["sv_a", "sv_b"], "jv", "sha") == {("sv_a", "kabc", 1): score}
    assert await store.load_progress(["sv_a"], "jv", "other") == {}

    for n, (aborted, consumes) in enumerate([(False, True), (True, False), (False, True)]):
        cand, base = fake_sv(), fake_sv()
        result = holdout._summarise(cand, base, 1, {"candidate": [], "baseline": []}, {}, aborted,
                                    10, 0)  # fmt: skip
        await store.record_audit(result, "sha", gate_id=f"g{n}", cycle_id="c1" if n else "c0")
        await store.record_decision(f"g{n}", {"decision": "rejected", "consumes_budget": consumes})
    # A comparison outside the gate carries no decision and consumes nothing.
    await store.record_audit(result, "sha")
    assert await store.budget_used("c1") == (2, 1)
    assert await store.budget_used("c9") == (2, 0)
    assert await store.prior_aborts(cand.version_id, base.version_id) == 1
    assert [d["consumes_budget"] for d in await store.decisions_for(cand.version_id)] == [
        True, False, True,
    ]  # fmt: skip
    with pytest.raises(holdout.HoldoutError, match="no comparison"):
        await store.record_decision("g_missing", {})
