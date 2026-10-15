"""The formal promotion gate end to end through the real CLI (U7; R28, R29, R30, R36): `womm
policy sign`, `calibrate score --record` and `noise report --record` on copies of the policy and
records files, then `womm evolve promote` on a synthetic five-proposal holdout sealed in a
scratch holdout database, `womm evolve rescore` and page 4 (the evolution API).

Every LLM call is a scripted fake on the api (and, for the re-score, claude_code) backend; no
network, no sealed holdout material, no LLM spend. The only seams: the backend factory, the code
identity (a fixed clean sha, so checkpoints are stable) and the git "committed" check on the tmp
policy and records files (as in test_gate_cli)."""

import asyncio
import json
import shutil

import yaml
from fastapi.testclient import TestClient

from womm import cli
from womm.api.app import create_app
from womm.config import REPO_ROOT
from womm.data import fixtures as fixtures_module
from womm.data.fixtures import load_fixture
from womm.eval import holdout
from womm.eval import judge_calibration as jc
from womm.eval.evaluators import judge_version
from womm.evolve import promotion as pm
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend

from ..api.test_app import AUTH, _settings
from ..eval import holdout_factory as hf
from ..eval.calibration_factory import golden, make_report, write_report
from ..eval.test_holdout import CLEAN
from ..graph.conftest import K71, Q71, fake_sv, finding, good_script
from .test_edits import _add_workforce
from .test_promotion import _rows

CYCLE = "cycle_gate_e2e"
CATEGORY = "sme_specific"  # four items per synthetic holdout case (holdout_factory)
TARGET = f"missed_impact/{CATEGORY}/none"
WF_MARK = "WORKFORCE-SIGNAL"
STEPS = 1000


# ----------------------------------------------------------------------------- setup


async def _seal(roots, holdout_url):
    """Five synthetic proposals (enough for bootstrap CIs) in the scratch holdout database."""
    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    for name in hf.FIVE_PROPOSALS:
        bundle = holdout.prepare_import(hf.handoff(name), hf.scenario_specs(), hf.ia_index(),
                                        golden_dir=roots / "golden")  # fmt: skip
        assert await store.import_case(bundle) == "inserted"
    assert await store.sealed_counts() == (5, 5)


async def _archive(database_url):
    """The seed and its api twin, a topology child adding a Workforce expert for the
    ``sme_specific`` pattern (archived as the topology stage does, with its pattern key) and the
    child's api twin: the gate compares the two api twins."""
    from womm.api.db import Database
    from womm.evolve.archive import Archive, archive_twin, seed_archive
    from womm.evolve.edits import build_candidate, render_diff, validate_diff

    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        archive = Archive(db)
        seed, seed_api = await seed_archive(archive, REPO_ROOT)
        diff = validate_diff(seed, [_add_workforce()])
        topology = build_candidate(seed, diff)
        await archive.archive(
            topology, origin="topology", parent_id=seed.version_id, cycle_id=CYCLE,
            diff={"ops": diff.ops_json(), "rendered": render_diff(seed, topology),
                  "rationale": "SME impacts are missed and no expert owns them.",
                  "target_pattern": TARGET},
            proposer={"model": "m", "prompt_hash": "h"},
        )  # fmt: skip
        twin = await archive_twin(archive, topology, cycle_id=CYCLE)
        return {"topology": topology, "candidate": twin, "incumbent": seed_api}
    finally:
        await db.close()


def _records_files(tmp_path, jv, capsys):
    """Copies of the committed policy and records; the policy re-signed by `womm policy sign`
    (it needs a changelog entry dated today), a calibration recorded by `womm calibrate score
    --record` and a whole R34 chain plus MDD report by `womm noise report --record`."""
    import datetime as dt

    policy, records = tmp_path / "promotion_policy.yaml", tmp_path / "promotion_records.yaml"
    shutil.copy(pm.POLICY_PATH, policy)
    shutil.copy(pm.RECORDS_PATH, records)
    data = yaml.safe_load(policy.read_text())
    entry = {"date": dt.date.today().isoformat(), "change": "signed for the e2e test",
             "reason": "synthetic gate run"}  # fmt: skip
    data |= {"signed_by": None, "signed_on": None, "changelog": [*data["changelog"], entry]}
    policy.write_text(yaml.safe_dump(data, sort_keys=False))
    assert cli.main(["policy", "sign", "--by", "jdoe", "--policy", str(policy)]) == cli.EXIT_OK
    assert pm.load_policy(policy)[0].signed

    # Calibration: 30 sampled pairs, both annotators agree with the judge on every pair.
    cases = golden(8, split="val")
    runs = tmp_path / "cal_runs"
    covered = lambda _c, e, r: (sum(map(ord, e)) + r) % 3 != 0  # noqa: E731
    report = write_report(runs / "eval.json", make_report(cases, covered=covered, jv=jv,
                                                          runs_dir=runs))  # fmt: skip
    out = tmp_path / "cal"
    jc.write_sample(out, system_version="sv_test0000001", judge_version=jv, reports=[report],
                    cases=cases, runs_dirs=[runs], n=30, seed=1,
                    annotators=["ann", "bob"])  # fmt: skip
    key = json.loads((out / jc.KEY_FILE).read_text())
    for name in ("ann", "bob"):
        path = out / f"answers_{name}.yaml"
        answers = yaml.safe_load(path.read_text())
        for row in answers["answers"]:
            judged = key["pairs"][row["pair_id"]]["judge_covered"]
            row["label"] = "covered" if judged else "not_covered"
        path.write_text(yaml.safe_dump(answers))
    rc = cli.main(["calibrate", "score", "--dir", str(out), "--record", "--records", str(records)])
    assert rc == cli.EXIT_OK, capsys.readouterr().err

    # R34: one formal first run of the policy's 3 repetitions with little noise: the stop rule
    # stops, and the noise run and the MDD report for this holdout's design are recorded.
    def metric(case_id, _metric, rep):
        return 0.6 + 0.01 * ((rep + int(case_id[6])) % 3)

    formal = make_report(golden(3, split="val"), reps=3, metric=metric, formal=True,
                         backends=("api",), jv=jv)  # fmt: skip
    formal["metadata"] |= {"langsmith_experiment": "womm-e2e-val-formal-1", "git_sha": "abc"}
    path = write_report(tmp_path / "formal.json", formal)
    rc = cli.main(["noise", "report", "--report", str(path), "--holdout-cases", "5",
                   "--holdout-proposals", "5", "--records", str(records), "--policy",
                   str(policy), "--record"])  # fmt: skip
    assert rc == cli.EXIT_OK, capsys.readouterr().err
    capsys.readouterr()
    loaded = pm.load_records(records)
    calib = loaded.calibration(jv)
    assert calib.agreement == 1.0 and calib.pairs == 30 and not pm.calibration_problems(calib)
    assert [a.next_step for a in loaded.noise_chain(jv)] == ["stop"]
    assert loaded.noise_chain_problems(jv) == []
    mdd = loaded.mdd("coverage", jv)
    assert (mdd.repetitions, mdd.holdout_cases, mdd.holdout_proposals) == (3, 5, 5)
    assert mdd.mdd <= pm.load_policy(policy)[0].expected_gain  # statistical mode
    return policy, records


# ----------------------------------------------------------------------------- fake backends


def _synth(system, user):
    """Labels the dossier CANDIDATE when the Workforce expert's finding reached synthesis (only
    the candidate has that expert), BASELINE otherwise."""
    label = "CANDIDATE" if WF_MARK in user else "BASELINE"
    return hf._synth(label)(system, user)


def _workforce(fail_on=None):
    """The candidate's Workforce expert; with ``fail_on``, its n-th call is rate limited."""
    calls = {"n": 0}
    ok = {"findings": [finding(K71, "com2021_206/art_71", Q71, "workers",
                               impact=f"{WF_MARK}: staffing and skills")]}  # fmt: skip

    def step(_system, _user):
        calls["n"] += 1
        if calls["n"] == fail_on:
            raise LLMError("rate_limit", "usage limit reached")
        return ok

    return step


def _api_backend(fail_on=None) -> FakeBackend:
    """One scripted api backend for both versions (the CLI merges their backends by name): the
    pipeline, the Workforce expert and the coverage judge (hf._judge covers every expected impact
    of a CANDIDATE dossier and only the first of a BASELINE one)."""
    s = good_script()
    script = {k: v * STEPS for k, v in s.items() if k != "synthesis"}
    script |= {"synthesis": [_synth] * STEPS, "judge": [hf._judge] * STEPS,
               "expert/workforce": [_workforce(fail_on)] * STEPS}  # fmt: skip
    return FakeBackend(script)


class Backends:
    """The CLI's backend factory: api roles get ``self.api``; the claude_code re-score judge gets
    ``self.judge``. Every call is logged."""

    def __init__(self, api: FakeBackend):
        self.api = api
        self.judge = FakeBackend({"judge": [hf._judge] * STEPS})
        self.prepared = []

    async def __call__(self, sv, *, skip_self_check=False, settings=None):
        used = {r.backend for r in sv.spec.roles().values()}
        self.prepared.append((sv.version_id, used))
        out = {}
        if "api" in used:
            out["api"] = self.api
        if "claude_code" in used:
            out["claude_code"] = self.judge
        assert used <= {"api", "claude_code"}, used
        return out, None, None


def _planner_calls(backend: FakeBackend) -> int:
    return sum(c.role_name == "planner" for c in backend.calls)


def _env(monkeypatch, tmp_path, database_url, holdout_url, backends):
    monkeypatch.setattr(fixtures_module, "FIXTURES_ROOT", hf.make_fixtures_root(tmp_path))
    (tmp_path / "golden").mkdir()
    monkeypatch.setattr(cli, "prepare_backends", backends)
    monkeypatch.setattr(cli, "code_identity", lambda *_a, **_k: CLEAN)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("HOLDOUT_DATABASE_URL", holdout_url)
    committed = []

    def files_committed(paths, *_a, **_k):
        committed.append([p.name for p in paths])
        return True

    monkeypatch.setattr(pm, "files_committed", files_committed)
    return committed


def _sealed_strings(roots):
    """Every sealed identifier and text that must never leave the holdout side."""
    from ..eval.test_holdout import _sealed

    out = set()
    for case, scenario in _sealed(roots, hf.FIVE_PROPOSALS):
        out |= {case.case_id, case.fixture, case.ia_reference, scenario.scenario_id,
                hf.SECRET_IMPACT}  # fmt: skip
        out |= {e.expected_id for e in case.expected_impacts}
        out |= {o.omission_id for o in case.important_omissions}
    return out


def _summaries(database_url):
    return [r[0] for r in _rows(database_url, "SELECT row_to_json(p) FROM promotion_decisions p"
                                             " ORDER BY created_at")]  # fmt: skip


# ----------------------------------------------------------------------------- happy path


def test_the_formal_gate_runs_end_to_end_through_the_cli(
    database_url, holdout_url, monkeypatch, capsys, tmp_path
):
    backends = Backends(_api_backend())
    committed = _env(monkeypatch, tmp_path, database_url, holdout_url, backends)
    asyncio.run(_seal(tmp_path, holdout_url))
    ids = asyncio.run(_archive(database_url))
    cand, inc = ids["candidate"], ids["incumbent"]
    jv = judge_version(inc)
    policy, records = _records_files(tmp_path, jv, capsys)

    argv = ["evolve", "promote", cand.version_id, "--incumbent", inc.version_id,
            "--policy", str(policy), "--records", str(records)]  # fmt: skip
    assert cli.main(argv) == cli.EXIT_OK, capsys.readouterr().err
    out = capsys.readouterr().out
    assert committed == [[policy.name, records.name]]
    # Both arms on the scripted api backend: 5 cases x 3 repetitions each, no extension (a
    # deterministic fake has no noise).
    assert _planner_calls(backends.api) == 30
    assert backends.prepared == [(cand.version_id, {"api"}), (inc.version_id, {"api"})]

    # The printed decision: statistical mode, promoted, with the target-category note.
    head = f"{cand.version_id} vs {inc.version_id}: promoted (holdout, bootstrap CI)"
    assert out.startswith(head)
    assert "reasons:" not in out
    assert "  coverage: +" in out and "5 cases)" in out
    note = (f"note: target category {CATEGORY} (the new expert's pattern): covered 20.0 vs 5.0 "
            "of 20 holdout items in 5 proposals")  # fmt: skip
    assert note in out, out
    assert "next: a human commits PROMOTIONS.md and the default version" in out

    # The holdout audit: one comparison with its decision; the budget consumed.
    audit_sql = ("SELECT gate_id, cycle_id, judge_version, decision, result"
                 " FROM holdout.compare_audit")  # fmt: skip
    ((gate_id, cycle, audit_jv, decision, result),) = _rows(holdout_url, audit_sql)
    assert cycle == CYCLE and audit_jv == jv
    assert decision["decision"] == "promoted" and decision["mode"] == "statistical"
    assert decision["consumes_budget"] is True and decision["gate_id"] == gate_id
    assert result["target_category"]["n_items"] == 20 and result["n_proposals"] == 5
    assert _rows(holdout_url, "SELECT status FROM holdout.budget_reservations") == [("consumed",)]
    assert _rows(holdout_url, "SELECT count(*) FROM holdout.compare_progress") == [(30,)]

    # Page 4's summary row: the label, the target-category note, aggregates only.
    (row,) = _summaries(database_url)
    assert row["gate_id"] == gate_id and row["candidate_version"] == cand.version_id
    assert row["label"] == "promoted (holdout, bootstrap CI)" and row["deployable"] is True
    assert any(n.startswith(note.removeprefix("note: ")) for n in row["notes"])
    assert row["cross_judge"] is None
    dumped = json.dumps(row)
    leaked = {s for s in _sealed_strings(tmp_path) if s in dumped}
    assert not leaked, leaked

    # The cross-family re-score (claude_code judge, scripted): written to the holdout audit and
    # onto the page-4 row.
    assert cli.main(["evolve", "rescore", gate_id]) == cli.EXIT_OK, capsys.readouterr().err
    out = capsys.readouterr().out
    assert f"page 4: the cross-judge label is on gate {gate_id}'s published summary" in out
    assert len(backends.judge.calls) == 30 and _planner_calls(backends.api) == 30
    (row,) = _summaries(database_url)
    assert row["cross_judge"]["label"] == "agrees" and row["cross_judge"]["mean_delta"] > 0
    assert _rows(holdout_url, "SELECT count(*) FROM holdout.rescore_audit") == [(1,)]
    dumped = json.dumps(row)
    assert not {s for s in _sealed_strings(tmp_path) if s in dumped}

    # Page 4 (the evolution API) shows the decision and the cross-judge label, on the dev
    # candidate and on its api twin.
    app = create_app(
        _settings(database_url), sv=fake_sv(), fixture=load_fixture(),
        backends={"fake": FakeBackend({})}, promotion_policy_path=policy, web_dist=None,
    )  # fmt: skip
    with TestClient(app) as client:
        body = client.get(f"/evolution/candidates/{ids['topology'].version_id}",
                          headers=AUTH).json()  # fmt: skip
        via_twin = client.get(f"/evolution/candidates/{cand.version_id}", headers=AUTH).json()
        lineage = client.get("/evolution/lineage", headers=AUTH).json()
    assert via_twin == body
    holdout_panel = body["holdout"]
    assert holdout_panel["status"] == "published"
    (shown,) = holdout_panel["decisions"]
    assert shown["gate_id"] == gate_id and shown["decision"] == "promoted"
    assert shown["label"] == "promoted (holdout, bootstrap CI)"
    assert shown["cross_judge"]["label"] == "agrees"
    assert any(n.startswith(f"target category {CATEGORY}") for n in shown["notes"])
    pattern = {"kind": "missed_impact", "category": CATEGORY, "owner": "none"}
    assert body["new_expert"]["target_pattern"] == pattern
    node = next(n for n in lineage["nodes"] if n["version_id"] == ids["topology"].version_id)
    assert node["decision"] == "promoted" and "promoted" in node["badges"]
    assert lineage["publish_summary"] is True
    page = json.dumps([body, lineage])
    assert not {s for s in _sealed_strings(tmp_path) if s in page}


# ----------------------------------------------------------------------------- abort and rerun


def test_a_rate_limited_gate_is_inconclusive_and_the_rerun_reuses_its_runs(
    database_url, holdout_url, monkeypatch, capsys, tmp_path
):
    fail_on = 4  # the candidate's 4th run (case 2, repetition 1): 3 runs checkpointed
    backends = Backends(_api_backend(fail_on=fail_on))
    _env(monkeypatch, tmp_path, database_url, holdout_url, backends)
    asyncio.run(_seal(tmp_path, holdout_url))
    ids = asyncio.run(_archive(database_url))
    cand, inc = ids["candidate"], ids["incumbent"]
    policy, records = _records_files(tmp_path, judge_version(inc), capsys)
    argv = ["evolve", "promote", cand.version_id, "--incumbent", inc.version_id,
            "--policy", str(policy), "--records", str(records)]  # fmt: skip

    assert cli.main(argv) == cli.EXIT_OK, capsys.readouterr().err
    out = capsys.readouterr().out
    assert f"{cand.version_id} vs {inc.version_id}: rejected (holdout, bootstrap CI)" in out
    assert "reasons: inconclusive" in out and "target category" not in out
    assert _planner_calls(backends.api) == fail_on, "the comparison stopped at the rate limit"
    ((decision, result),) = _rows(holdout_url,
                                  "SELECT decision, result FROM holdout.compare_audit")  # fmt: skip
    assert result["aborted"] is True and "rate_limited" in result["flags"]
    assert decision["reasons"] == ["inconclusive"] and decision["consumes_budget"] is False
    assert _rows(holdout_url, "SELECT status FROM holdout.budget_reservations") == [("released",)]
    assert _rows(holdout_url, "SELECT count(*) FROM holdout.compare_progress") == [(fail_on - 1,)]
    (summary,) = _summaries(database_url)
    assert summary["decision"] == "rejected" and summary["reasons"] == ["inconclusive"]

    # The rerun (the runbook's `--resume`; nothing is reserved, so it reserves anew) reuses the
    # checkpointed candidate runs and completes.
    backends.api = _api_backend()
    assert cli.main([*argv, "--resume"]) == cli.EXIT_OK, capsys.readouterr().err
    out = capsys.readouterr().out
    assert f"{cand.version_id} vs {inc.version_id}: promoted (holdout, bootstrap CI)" in out
    assert _planner_calls(backends.api) == 30 - (fail_on - 1), "finished runs are not re-run"
    workforce = sum(c.key == "expert/workforce" for c in backends.api.calls)
    assert workforce == 15 - (fail_on - 1)
    assert _rows(holdout_url, "SELECT status FROM holdout.budget_reservations ORDER BY "
                              "created_at") == [("released",), ("consumed",)]  # fmt: skip
    assert _rows(holdout_url, "SELECT count(*) FROM holdout.compare_progress") == [(30,)]
    rows = _summaries(database_url)
    assert [r["decision"] for r in rows] == ["rejected", "promoted"]
    assert rows[1]["reasons"] == [] and rows[1]["n_proposals"] == 5

    # The pair's budget for this cycle is spent: a third run is refused before any backend.
    backends.api = _api_backend()
    assert cli.main(argv) == cli.EXIT_USAGE
    assert f"cycle {CYCLE} already used its 1 holdout comparison(s)" in capsys.readouterr().err
    assert backends.api.calls == []
