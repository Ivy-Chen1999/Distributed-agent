"""Promotion gate (U7; R28, R29, AE4): pre-registered policy, mode before results, the
preconditions, the decision rule and its two records."""

import json
from pathlib import Path

import psycopg
import pytest
import yaml

from womm.config import REPO_ROOT
from womm.data import fixtures as fixtures_module
from womm.decisions.stub import StubDecisionService
from womm.eval import holdout
from womm.eval.evaluators import judge_version
from womm.eval.holdout import HoldoutComparison, MetricDelta, PooledNoise
from womm.evolve import promotion as pm
from womm.evolve.planner_view import ALLOWED_TABLES, QUERIES, PlannerView
from womm.models.run import CodeIdentity
from womm.models.system_version import build_system_version, derive_system_version

from ..eval import holdout_factory as hf
from ..eval.test_holdout import CLEAN, _sealed
from ..eval.test_holdout_resume import ProgressStore
from ..graph.conftest import fake_sv

POLICY = yaml.safe_load((REPO_ROOT / "evals" / "promotion_policy.yaml").read_text())


def policy(**changes) -> pm.PromotionPolicy:
    return pm.PromotionPolicy.model_validate(POLICY | changes)


SIGNED = {"signed_by": "jdoe", "signed_on": "2026-11-01"}


def api(sv):
    return derive_system_version(sv, Path("."), backends=dict.fromkeys(sv.spec.roles(), "api"))


def versions(backend="fake"):
    base = fake_sv()
    cand = build_system_version(base.spec.model_copy(update={"description": "candidate"}),
                                REPO_ROOT)  # fmt: skip
    return (api(cand), api(base)) if backend == "api" else (cand, base)


def records(jv, agreement=0.9, noise=True, mdd=0.05) -> pm.PromotionRecords:
    data = {"judge_calibrations": [], "formal_noise_runs": [], "mdd_reports": []}
    if agreement is not None:
        data["judge_calibrations"].append({"judge_version": jv, "agreement": agreement,
                                           "pairs": 30, "annotators": ["a", "b"],
                                           "recorded_on": "2026-10-20"})  # fmt: skip
    if noise:
        data["formal_noise_runs"].append({"system_version": "sv_x", "judge_version": jv,
                                          "split": "val", "repetitions": 6,
                                          "recorded_on": "2026-10-21"})  # fmt: skip
    if mdd is not None:
        data["mdd_reports"].append({"metric": "coverage", "judge_version": jv, "mdd": mdd,
                                    "recorded_on": "2026-10-21"})  # fmt: skip
    return pm.PromotionRecords.model_validate(data)


def cmp(coverage=(0.08, 0.02, 0.14), grounding=(0.0, None, None), omissions=(0.0, None, None),
        noise=0.1, flags=(), aborted=False, n_proposals=5) -> HoldoutComparison:  # fmt: skip
    def delta(t):
        return MetricDelta(mean_delta=t[0], ci95_low=t[1], ci95_high=t[2], n_cases=8)

    return HoldoutComparison(
        candidate_version="sv_cand", baseline_version="sv_inc", judge_version="sv_inc",
        repetitions=3, n_cases=8, n_proposals=n_proposals,
        scored_runs={"candidate": 24, "baseline": 24}, errored_runs={"candidate": 0, "baseline": 0},
        aborted=aborted, failure_policy="abort", missing_cases={"candidate": 0, "baseline": 0},
        flags=list(flags),
        deltas={"coverage": delta(coverage), "grounding": delta(grounding),
                "omissions_addressed": delta(omissions)},
        noise={m: PooledNoise(candidate_sd=noise, baseline_sd=None if noise is None else noise / 2)
               for m in ("coverage", "grounding", "omissions_addressed")},
        bootstrap={},
    )  # fmt: skip


def decide(c, mode="statistical", deployable=True, **kw):
    choice = pm.ModeChoice(mode=mode, deployable=deployable)
    return pm.decide(c, policy(), choice, gate_id="g", cycle_id="c", policy_sha256="sha",
                     git_sha="abc", **kw)  # fmt: skip


# ----------------------------------------------------------------------------- policy


def test_the_committed_policy_holds_the_plan_defaults_and_is_unsigned():
    p, sha = pm.load_policy()
    assert p.primary_metric == "coverage" and not p.signed
    assert p.guards["grounding"].max_drop == 0.02
    assert p.guards["omissions_addressed"].max_drop_noise_sd == 1.0
    assert (p.holdout_budget.per_cycle, p.holdout_budget.total) == (1, 6)
    assert p.publish_summary is False and p.failure_policy == "abort"
    assert len(sha) == 64
    assert pm.load_records() == pm.PromotionRecords()


@pytest.mark.parametrize("field", sorted(POLICY))
def test_an_incomplete_policy_is_refused(tmp_path, field):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump({k: v for k, v in POLICY.items() if k != field}))
    with pytest.raises(pm.GateRefused, match="incomplete or invalid"):
        pm.load_policy(path)


def test_policy_validation_rules(tmp_path):
    with pytest.raises(ValueError, match="primary metric"):
        policy(guards={"coverage": {"max_drop": 0.1}})
    with pytest.raises(ValueError, match="exactly one"):
        policy(guards={"grounding": {"max_drop": 0.1, "max_drop_noise_sd": 1}})
    with pytest.raises(ValueError):
        policy(repetitions=1)
    with pytest.raises(pm.GateRefused, match="no promotion policy"):
        pm.load_policy(tmp_path / "missing.yaml")


# ----------------------------------------------------------------------------- mode


def test_any_role_off_the_api_backend_is_dev_mode():
    cand, base = versions("fake")
    choice = pm.choose_mode(policy(**SIGNED), records("x"), cand, base)
    assert choice.mode == "dev" and choice.deployable is False


def test_formal_mode_comes_from_the_mdd_report_before_any_result():
    cand, base = versions("api")
    jv = judge_version(base)
    assert pm.choose_mode(policy(), records(jv, mdd=0.05), cand, base).mode == "statistical"
    weak = pm.choose_mode(policy(), records(jv, mdd=0.12), cand, base)
    assert weak.mode == "weak" and "exceeds the expected gain" in weak.notes[0]
    assert pm.choose_mode(policy(), records(jv, mdd=None), cand, base).mode == "weak"
    assert pm.choose_mode(policy(formal_mode="weak"), records(jv), cand, base).mode == "weak"


# ----------------------------------------------------------------------------- preconditions


class GateStore(ProgressStore):
    """ProgressStore plus the gate's bookkeeping, in memory."""

    def __init__(self, sealed, progress=None, used=(0, 0), aborts=0, crash_after=None):
        super().__init__(sealed, progress, crash_after=crash_after)
        self.used, self.aborts, self.decisions = used, aborts, {}
        self.compared = 0
        self.reserved, self.released = [], []

    async def reserve_budget(self, gate_id, cycle_id, candidate_version, baseline_version, *,
                             per_cycle, total, resume=False):  # fmt: skip
        self.reserved.append(gate_id)
        return gate_id

    async def release_budget(self, gate_id):
        self.released.append(gate_id)

    async def _sealed(self):
        self.compared += 1
        return self.sealed

    async def budget_used(self, cycle_id):
        return self.used

    async def prior_aborts(self, candidate_version, baseline_version):
        return self.aborts

    async def record_decision(self, gate_id, decision):
        self.decisions[gate_id] = decision


async def _preflight(cand, base, p=None, recs=None, committed=True, store=None):
    return await pm.preflight(p or policy(**SIGNED), recs or records(judge_version(base)), cand,
                              base, store=store or GateStore([]), cycle_id="c1",
                              policy_committed=committed)  # fmt: skip


async def test_dev_mode_runs_uncalibrated_and_without_r34():
    cand, base = versions("fake")
    choice = await _preflight(cand, base, recs=pm.PromotionRecords())
    assert choice.mode == "dev"


@pytest.mark.parametrize(("kw", "message"), [
    ({"p": policy()}, "not signed"),
    ({"committed": False}, "uncommitted"),
])  # fmt: skip
async def test_dev_mode_on_the_sealed_holdout_needs_a_signed_committed_policy(kw, message):
    """User decision: every gate mode reads the sealed holdout, so every mode, dev included,
    runs only under a signed, committed policy (dev still skips calibration and R34)."""
    cand, base = versions("fake")
    store = GateStore([])
    with pytest.raises(pm.GateRefused, match=message):
        await _preflight(cand, base, recs=pm.PromotionRecords(), store=store, **kw)
    assert store.compared == 0


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"p": policy()}, "not signed"),
        ({"committed": False}, "uncommitted"),
        ({"recs": pm.PromotionRecords()}, "no coverage-judge calibration"),
        ({"recs": "low"}, "80%, below 85%"),
        ({"recs": "no_noise"}, "R34 formal noise run"),
    ],
)
async def test_formal_modes_refuse_before_any_holdout_run(kw, message):
    cand, base = versions("api")
    jv = judge_version(base)
    if kw.get("recs") == "low":
        kw["recs"] = records(jv, agreement=0.8)
    elif kw.get("recs") == "no_noise":
        kw["recs"] = records(jv, noise=False)
    store = GateStore([])
    with pytest.raises(pm.GateRefused, match=message):
        await _preflight(cand, base, store=store, **kw)
    assert store.compared == 0


async def test_a_calibration_of_another_judge_does_not_count():
    cand, base = versions("api")
    with pytest.raises(pm.GateRefused, match="no coverage-judge calibration"):
        await _preflight(cand, base, recs=records("jv_other"))


async def test_the_budget_and_backends_are_checked():
    cand, base = versions("api")
    with pytest.raises(pm.GateRefused, match="budget is exhausted"):
        await _preflight(cand, base, store=GateStore([], used=(6, 0)))
    with pytest.raises(pm.GateRefused, match="already used its 1"):
        await _preflight(cand, base, store=GateStore([], used=(2, 1)))
    dev_cand, _ = versions("fake")
    with pytest.raises(pm.GateRefused, match="same backends"):
        await _preflight(dev_cand, base)
    with pytest.raises(pm.GateRefused, match="is the incumbent"):
        await _preflight(base, base)


def topology(base, **role):
    """A topology candidate: ``base`` plus a Workforce expert (role overrides for the new one)."""
    from womm.evolve.edits import build_candidate, validate_diff
    from womm.models.system_version import build_system_version_from_texts

    from .test_edits import _add_workforce

    cand = build_candidate(base, validate_diff(base, [_add_workforce()]))
    if not role:
        return cand
    data = cand.spec.model_dump()
    data["experts"][-1]["role"].update(role)
    return build_system_version_from_texts(type(cand.spec).model_validate(data), cand.prompts)


@pytest.mark.parametrize("backend", ["fake", "api"])
async def test_a_new_expert_candidate_is_compared_with_its_incumbent(backend):
    """The added role (``expert:workforce``) has no counterpart in the incumbent: the shared
    roles match, and the new role is on the candidate's existing backend and model."""
    _, base = versions(backend)
    choice = await _preflight(topology(base), base)
    assert choice.mode == ("dev" if backend == "fake" else "statistical")


@pytest.mark.parametrize("role", [{"backend": "claude_code"}, {"model": "other-model"}])
async def test_a_new_expert_on_another_backend_or_model_is_refused(role):
    _, base = versions("api")
    with pytest.raises(pm.GateRefused, match="expert:workforce"):
        await _preflight(topology(base, **role), base)


# ----------------------------------------------------------------------------- decision rule


def test_ae4_coverage_gain_with_a_grounding_drop_is_rejected():
    d = decide(cmp(coverage=(0.08, 0.03, 0.13), grounding=(-0.05, -0.08, -0.02)))
    assert d.decision == "rejected" and d.reasons == ["grounding_regression"]
    assert d.label == "rejected (holdout, bootstrap CI)"
    dumped = d.model_dump_json()
    assert "case_" not in dumped and "eval_" not in dumped


def test_statistical_promotes_on_a_ci_above_zero_with_guards_within_tolerance():
    d = decide(cmp(grounding=(-0.01, None, None), omissions=(-0.05, None, None)))
    assert d.decision == "promoted" and d.mode == "statistical" and d.reasons == []
    assert d.label == "promoted (holdout, bootstrap CI)" and d.consumes_budget
    assert d.deltas["omissions_addressed"].noise_sd == pytest.approx(0.1)
    assert decide(cmp(coverage=(0.05, -0.01, 0.1))).reasons == ["coverage_ci_not_above_zero"]


def test_omissions_guard_is_one_pooled_noise_sd():
    assert decide(cmp(omissions=(-0.11, None, None))).reasons == ["omissions_addressed_regression"]
    assert decide(cmp(omissions=(-0.09, None, None))).decision == "promoted"
    unavailable = decide(cmp(omissions=(-0.01, None, None), noise=None))
    assert "omissions_addressed_noise_unavailable" in unavailable.reasons


def test_weak_mode_promotes_on_a_positive_mean_and_says_directional():
    d = decide(cmp(coverage=(0.04, None, None)), mode="weak")
    assert d.decision == "promoted" and d.mode == "weak"
    assert d.label == "promoted (weak threshold: directional)"
    assert decide(cmp(coverage=(0.0, None, None)), mode="weak").reasons == ["coverage_not_improved"]


def test_insufficient_proposals_downgrade_statistical_to_weak_and_record_it():
    d = decide(cmp(coverage=(0.04, None, None), flags=["insufficient_proposals"], n_proposals=3))
    assert d.mode == "weak" and d.decision == "promoted"
    assert any("downgraded from statistical to weak: insufficient proposals" in n
               for n in d.notes)  # fmt: skip


def test_dev_mode_is_never_deployable():
    d = decide(cmp(coverage=(0.04, None, None)), mode="dev", deployable=False)
    assert d.decision == "promoted" and not d.deployable
    assert d.label == "promoted (dev-only, not deployable; weak threshold: directional)"
    assert "can never change the deployed default" in pm.format_decision(d)


def test_an_aborted_comparison_is_inconclusive_and_free():
    d = decide(cmp(coverage=(None, None, None), aborted=True))
    assert d.decision == "rejected" and d.reasons == ["inconclusive"] and not d.consumes_budget
    again = decide(cmp(coverage=(None, None, None), aborted=True), prior_aborts=1)
    assert again.reasons == ["inconclusive", "repeated_abort"]
    assert any("a human should look" in n for n in again.notes)


def test_r37_is_copied_and_never_changes_the_decision():
    r37 = {"status": "available", "regression": True, "candidate": {"mean": 0.2},
           "incumbent": {"mean": 0.6}}  # fmt: skip
    with_r37 = decide(cmp(), r37=r37)
    assert with_r37.decision == decide(cmp()).decision == "promoted"
    assert with_r37.r37 == r37 and "regression; not gating" in pm.format_decision(with_r37)


# ----------------------------------------------------------------------------- the gate, end to end


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures_module, "FIXTURES_ROOT", hf.make_fixtures_root(tmp_path))
    (tmp_path / "golden").mkdir()
    return tmp_path


def _gate_backends(cand, n_runs):
    raw = hf.compare_backends(runs_per_version=n_runs)
    return lambda sv: raw["candidate"] if sv is cand else raw["baseline"]


async def test_a_dev_gate_promotes_and_records_in_the_audit_only(roots):
    cand, base = versions("fake")
    store = GateStore(_sealed(roots))  # 2 proposals: insufficient for CIs
    p = policy(repetitions=2, **SIGNED)
    d = await pm.run_gate(
        candidate=cand, incumbent=base, policy=p, policy_sha256="sha",
        records=pm.PromotionRecords(), store=store, backends=_gate_backends(cand, 4),
        decisions=StubDecisionService(), code=CLEAN, cycle_id="c1", policy_committed=True,
    )  # fmt: skip
    assert d.mode == "dev" and d.decision == "promoted" and not d.deployable
    assert store.decisions == {d.gate_id: d.model_dump(mode="json")}
    (result, sha, tags) = store.audits[0]
    assert tags == {"gate_id": d.gate_id, "cycle_id": "c1"} and sha == "abc"
    assert len(store.progress) == 8, "every scored run is checkpointed"
    assert "insufficient_proposals" in d.flags


async def test_publish_summary_needs_a_database(roots):
    cand, base = versions("fake")
    store = GateStore(_sealed(roots))
    with pytest.raises(pm.GateRefused, match="no main database"):
        await pm.run_gate(
            candidate=cand, incumbent=base,
            policy=policy(repetitions=2, publish_summary=True, **SIGNED), policy_sha256="sha",
            records=pm.PromotionRecords(), store=store, backends=_gate_backends(cand, 4),
            decisions=StubDecisionService(), code=CLEAN, cycle_id="c1", policy_committed=True,
        )  # fmt: skip
    assert store.compared == 0 and store.audits == []


# ----------------------------------------------------------------------------- Postgres


async def _sealed_store(roots, holdout_url):
    store = holdout.HoldoutStore(holdout_url)
    await store.migrate()
    for name in hf.PROPOSALS:
        bundle = holdout.prepare_import(hf.handoff(name), hf.scenario_specs(), hf.ia_index(),
                                        golden_dir=roots / "golden")  # fmt: skip
        await store.import_case(bundle)
    return store


def _rows(url, sql):
    with psycopg.connect(url) as conn:
        return conn.execute(sql).fetchall()


@pytest.mark.parametrize("publish", [False, True])
async def test_gate_records_on_postgres(roots, db, database_url, holdout_url, publish, tmp_path):
    """One audit row with the decision; a promotion_decisions row only with publish_summary;
    the summary has no case id and the Planner cannot read it (AE4, Planner side)."""
    store = await _sealed_store(roots, holdout_url)
    cand, base = versions("fake")
    d = await pm.run_gate(
        candidate=cand, incumbent=base,
        policy=policy(repetitions=2, publish_summary=publish, **SIGNED), policy_sha256="sha",
        records=pm.PromotionRecords(), store=store, backends=_gate_backends(cand, 4),
        decisions=StubDecisionService(), code=CodeIdentity(git_sha="abc", dirty=False),
        cycle_id="c1", policy_committed=True,
        summary_db=db,
    )  # fmt: skip
    (audit,) = _rows(holdout_url, "SELECT gate_id, cycle_id, decision FROM holdout.compare_audit")
    assert audit == (d.gate_id, "c1", d.model_dump(mode="json"))
    assert _rows(holdout_url, "SELECT count(*) FROM holdout.compare_progress") == [(8,)]
    assert await store.budget_used("c1") == (1, 1)
    summary = _rows(database_url, "SELECT row_to_json(p) FROM promotion_decisions p")
    if not publish:
        assert summary == []
        return
    (row,) = summary
    assert row[0]["decision"] == "promoted" and row[0]["mode"] == "dev"
    dumped = json.dumps(row[0])
    for case, scenario in _sealed(roots):
        assert case.case_id not in dumped and scenario.scenario_id not in dumped
    assert "promotion_decisions" not in ALLOWED_TABLES
    assert not any("promotion_decisions" in q for q in QUERIES.values())
    async with PlannerView(database_url, runs_dir=tmp_path, env={}) as view:
        assert await view.candidate(cand.version_id) is None
        assert await view.metrics(cand.version_id) == []


def test_an_r37_error_is_shown_and_never_changes_the_decision():
    r37 = {"status": "error", "reason": "R37 reference answers unusable: bad", "regression": None}
    d = decide(cmp(), r37=r37)
    assert d.decision == "promoted" and d.r37 == r37
    assert "R37 diff check: error (R37 reference answers unusable: bad; not gating)" in (
        pm.format_decision(d)
    )


# ----------------------------------------------------------------------------- budget and records


def _signed_gate(cand, base, store, **kw):
    return pm.run_gate(
        candidate=cand, incumbent=base, policy=kw.pop("p", policy(repetitions=2, **SIGNED)),
        policy_sha256="sha", records=pm.PromotionRecords(), store=store,
        backends=_gate_backends(cand, 4), decisions=StubDecisionService(), code=CLEAN,
        cycle_id="c1", policy_committed=True, **kw,
    )  # fmt: skip


async def test_the_budget_is_reserved_before_the_comparison_and_released_on_a_crash(roots):
    from ..eval.test_holdout_resume import Crash

    cand, base = versions("fake")
    store = GateStore(_sealed(roots), crash_after=1)
    with pytest.raises(Crash):
        await _signed_gate(cand, base, store)
    assert len(store.reserved) == 1 and store.released == store.reserved
    assert store.decisions == {}


async def test_a_summary_that_fails_after_the_decision_still_returns_the_decision(roots):
    class BrokenDb:
        @property
        def pool(self):
            raise RuntimeError("main database down")

    cand, base = versions("fake")
    store = GateStore(_sealed(roots))
    p = policy(repetitions=2, publish_summary=True, **SIGNED)
    with pytest.raises(pm.SummaryNotRecorded, match="main database down") as info:
        await _signed_gate(cand, base, store, p=p, summary_db=BrokenDb())
    decision = info.value.decision
    assert store.decisions == {decision.gate_id: decision.model_dump(mode="json")}
    assert store.released == [], "the decision was recorded: the budget is consumed"


class MemoryArchive:
    def __init__(self, *rows):
        self.rows = {r["version_id"]: r for r in rows}

    async def get(self, version_id):
        return self.rows.get(version_id)

    async def twins(self, version_id):
        return [r for r in self.rows.values() if r["twin_of"] == version_id]


def _row(vid, cycle=None, twin_of=None):
    return {"version_id": vid, "cycle_id": cycle, "twin_of": twin_of}


async def test_the_cycle_id_is_the_candidates_archived_cycle():
    archive = MemoryArchive(
        _row("sv_dev", "cycle_a"), _row("sv_api", None, "sv_dev"),
        _row("sv_twin_cycled", "cycle_b", "sv_dev"), _row("sv_manual"),
    )  # fmt: skip
    assert await pm.gate_cycle_id(archive, "sv_dev", None) == "cycle_a"
    assert await pm.gate_cycle_id(archive, "sv_api", None) == "cycle_a"
    assert await pm.gate_cycle_id(archive, "sv_api", "cycle_a") == "cycle_a"
    assert await pm.gate_cycle_id(archive, "sv_dev", "cycle_b") == "cycle_b", "its api twin's"
    with pytest.raises(pm.GateRefused, match="not the candidate's archived cycle"):
        await pm.gate_cycle_id(archive, "sv_dev", "cycle_fresh")
    with pytest.raises(pm.GateRefused, match="no cycle id in the archive"):
        await pm.gate_cycle_id(archive, "sv_manual", "cycle_fresh")


async def test_budget_reservations_on_postgres(holdout_url):
    """Reservations are atomic under an advisory lock: two concurrent gates in one cycle get one
    slot; an in-flight reservation counts; release frees it; --resume takes over a stopped
    run's reservation for the same pair."""
    import asyncio

    store = holdout.HoldoutStore(holdout_url)
    assert "003_budget_reservations" in await store.migrate()
    caps = {"per_cycle": 1, "total": 6}
    results = await asyncio.gather(
        *(store.reserve_budget(f"g{i}", "c1", "sv_cand", "sv_inc", **caps) for i in range(4)),
        return_exceptions=True,
    )
    won = [r for r in results if isinstance(r, str)]
    assert len(won) == 1 and all(isinstance(r, holdout.BudgetRefused) for r in results
                                 if not isinstance(r, str))  # fmt: skip
    assert await store.budget_used("c1") == (1, 1)
    with pytest.raises(holdout.BudgetRefused, match="in progress"):
        await store.reserve_budget("g9", "c1", "sv_cand", "sv_inc", **caps)
    assert await store.reserve_budget("g9", "c1", "sv_cand", "sv_inc", **caps, resume=True) == "g9"
    assert await store.budget_used("c1") == (1, 1), "the stopped run's slot was taken over"
    await store.release_budget("g9")
    assert await store.budget_used("c1") == (0, 0)
    with pytest.raises(holdout.BudgetRefused, match="exhausted"):
        await store.reserve_budget("g10", "c2", "a", "b", per_cycle=1, total=0)


async def test_a_recorded_decision_converts_its_reservation(roots, holdout_url):
    store = await _sealed_store(roots, holdout_url)
    cand, base = versions("fake")
    d = await _signed_gate(cand, base, store)
    assert _rows(holdout_url, "SELECT gate_id, status FROM holdout.budget_reservations") == [
        (d.gate_id, "consumed")
    ]
    assert await store.budget_used("c1") == (1, 1)
    with pytest.raises(pm.GateRefused, match="already used its 1"):
        await _signed_gate(cand, base, store)
