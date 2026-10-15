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
from womm.eval.holdout import DiagnosticMeans, HoldoutComparison, MetricDelta, PooledNoise
from womm.evolve import promotion as pm
from womm.evolve.planner_view import ALLOWED_TABLES, QUERIES, PlannerView
from womm.models.run import CodeIdentity
from womm.models.system_version import build_system_version, derive_system_version

from ..eval import holdout_factory as hf
from ..eval.test_holdout import CLEAN, _sealed
from ..eval.test_holdout_resume import ProgressStore
from ..graph.conftest import fake_sv

# The committed policy's values, unsigned and unpublished: each test signs (SIGNED) and
# publishes (publish_summary=True) where it needs to.
POLICY = yaml.safe_load((REPO_ROOT / "evals" / "promotion_policy.yaml").read_text()) | {
    "signed_by": None,
    "signed_on": None,
    "publish_summary": False,
}


def policy(**changes) -> pm.PromotionPolicy:
    return pm.PromotionPolicy.model_validate(POLICY | changes)


SIGNED = {"signed_by": "jdoe", "signed_on": "2026-11-01"}
TWO = {"min": 2, "max": 2}  # fixed repetitions for the small synthetic holdout


def api(sv):
    return derive_system_version(sv, Path("."), backends=dict.fromkeys(sv.spec.roles(), "api"))


def versions(backend="fake"):
    base = fake_sv()
    cand = build_system_version(base.spec.model_copy(update={"description": "candidate"}),
                                REPO_ROOT)  # fmt: skip
    return (api(cand), api(base)) if backend == "api" else (cand, base)


def records(jv, agreement=0.9, noise=True, mdd=0.05, **calibration) -> pm.PromotionRecords:
    data = {"judge_calibrations": [], "formal_noise_runs": [], "mdd_reports": []}
    if agreement is not None:
        data["judge_calibrations"].append({"judge_version": jv, "agreement": agreement,
                                           "pairs": 30, "annotators": ["a", "b"],
                                           "inter_annotator_agreement": 0.9,
                                           "recorded_on": "2026-10-20"} | calibration)  # fmt: skip
    if noise:
        data["formal_noise_runs"].append({"system_version": "sv_x", "judge_version": jv,
                                          "split": "val", "repetitions": 6,
                                          "recorded_on": "2026-10-21"})  # fmt: skip
    if mdd is not None:
        data["mdd_reports"].append({"metric": "coverage", "judge_version": jv, "mdd": mdd,
                                    "recorded_on": "2026-10-21", "repetitions": 3,
                                    "holdout_cases": 8, "holdout_proposals": 8,
                                    "icc": 1.0, "alpha": 0.05, "power": 0.8})  # fmt: skip
    return pm.PromotionRecords.model_validate(data)


def cmp(coverage=(0.08, 0.02, 0.14), grounding=(0.0, None, None), omissions=(0.0, None, None),
        noise=0.1, flags=(), aborted=False, n_proposals=5, precision=(0.0, None, None),
        impacts=(10.0, 10.0), se=None, drop=()) -> HoldoutComparison:  # fmt: skip
    def delta(t, s=None):
        return MetricDelta(mean_delta=t[0], ci95_low=t[1], ci95_high=t[2], se=s, n_cases=8)

    return HoldoutComparison(
        candidate_version="sv_cand", baseline_version="sv_inc", judge_version="sv_inc",
        repetitions=3, n_cases=8, n_proposals=n_proposals,
        scored_runs={"candidate": 24, "baseline": 24}, errored_runs={"candidate": 0, "baseline": 0},
        aborted=aborted, failure_policy="abort", missing_cases={"candidate": 0, "baseline": 0},
        flags=list(flags),
        deltas={k: v for k, v in {"coverage": delta(coverage, se), "grounding": delta(grounding),
                "omissions_addressed": delta(omissions), "precision": delta(precision)}.items()
                if k not in drop},
        noise={m: PooledNoise(candidate_sd=noise, baseline_sd=None if noise is None else noise / 2)
               for m in ("coverage", "grounding", "omissions_addressed", "precision")},
        bootstrap={},
        diagnostics={n: DiagnosticMeans(candidate_mean=impacts[0] * k,
                                        baseline_mean=impacts[1] * k, n_cases=8)
                     for n, k in (("impact_count", 1), ("impact_chars", 100))},
    )  # fmt: skip


def decide(c, mode="statistical", deployable=True, **kw):
    choice = pm.ModeChoice(mode=mode, deployable=deployable)
    return pm.decide(c, policy(), choice, gate_id="g", cycle_id="c", policy_sha256="sha",
                     git_sha="abc", **kw)  # fmt: skip


# ----------------------------------------------------------------------------- policy


def test_the_committed_policy_holds_the_plan_defaults():
    p, sha = pm.load_policy()
    assert p.primary_metric == "coverage"  # re-signed by the owner after the 2026-10-14 changes
    assert p.guards["precision"].max_drop_noise_sd == 1.0 and p.weak_min_delta_se == 1.0
    assert (p.repetitions.min, p.repetitions.max) == (3, 6)
    assert [c.date for c in p.changelog][:2] == ["2026-10-12", "2026-10-14"]
    assert p.guards["grounding"].max_drop == 0.02
    assert p.guards["omissions_addressed"].max_drop_noise_sd == 1.0
    assert (p.holdout_budget.per_cycle, p.holdout_budget.total) == (1, 6)
    assert p.publish_summary is True and p.failure_policy == "abort"  # owner 2026-10-15
    assert p.changelog[-1].date == "2026-10-15"
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
        policy(repetitions={"min": 1, "max": 6})
    with pytest.raises(ValueError, match="max"):
        policy(repetitions={"min": 4, "max": 3})
    with pytest.raises(ValueError, match="precision"):
        policy(guards={k: v for k, v in POLICY["guards"].items() if k != "precision"})
    with pytest.raises(ValueError):
        policy(changelog=[])
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

    def __init__(self, sealed, progress=None, used=(0, 0), aborts=0, crash_after=None,
                 counts=None):  # fmt: skip
        super().__init__(sealed, progress, crash_after=crash_after)
        self.used, self.aborts, self.decisions = used, aborts, {}
        self.counts = counts
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

    async def sealed_counts(self):
        if self.counts is not None:
            return self.counts
        # An empty preflight store stands for the 8-case holdout the records() MDD describes.
        return (8, 8) if not self.sealed else await super().sealed_counts()

    async def prior_aborts(self, candidate_version, baseline_version):
        return self.aborts

    async def record_decision(self, gate_id, decision):
        self.decisions[gate_id] = decision


async def _preflight(cand, base, p=None, recs=None, committed=True, store=None):
    return await pm.preflight(p or policy(**SIGNED), recs or records(judge_version(base)), cand,
                              base, store=store or GateStore([], counts=(8, 8)), cycle_id="c1",
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
        ({"recs": "few_pairs"}, "only 20 pairs with a clear human answer"),
        ({"recs": "people_split"}, "agree with each other on 70%, below 80%"),
        ({"recs": "one_person"}, "one annotator only"),
    ],
)
async def test_formal_modes_refuse_before_any_holdout_run(kw, message):
    cand, base = versions("api")
    jv = judge_version(base)
    if kw.get("recs") == "low":
        kw["recs"] = records(jv, agreement=0.8)
    elif kw.get("recs") == "no_noise":
        kw["recs"] = records(jv, noise=False)
    elif kw.get("recs") == "few_pairs":
        kw["recs"] = records(jv, pairs=20)
    elif kw.get("recs") == "people_split":
        kw["recs"] = records(jv, inter_annotator_agreement=0.7)
    elif kw.get("recs") == "one_person":
        kw["recs"] = records(jv, annotators=["a"], inter_annotator_agreement=None)
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
    assert "next: a human commits PROMOTIONS.md" in pm.format_decision(d)
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
    assert d.label == "not rejected (weak threshold: directional)"
    assert decide(cmp(coverage=(0.0, None, None)), mode="weak").reasons == [
        "coverage_below_weak_bar"]  # fmt: skip


def test_insufficient_proposals_downgrade_statistical_to_weak_and_record_it():
    d = decide(cmp(coverage=(0.04, None, None), flags=["insufficient_proposals"], n_proposals=3))
    assert d.mode == "weak" and d.decision == "promoted"
    assert d.label == "not rejected (weak threshold: directional)"
    assert any("downgraded from statistical to weak: insufficient proposals" in n
               for n in d.notes)  # fmt: skip


def test_dev_mode_is_never_deployable():
    d = decide(cmp(coverage=(0.04, None, None)), mode="dev", deployable=False)
    assert d.decision == "promoted" and not d.deployable
    assert d.label == "not rejected (dev-only, not deployable; weak threshold: directional)"
    assert "can never change the deployed default" in pm.format_decision(d)


def test_a_weak_pass_is_never_made_the_deployed_default():
    """Owner decision 2026-10-15: a weak (or dev) pass is "not rejected", never the default;
    only a statistical promotion asks a human to commit the new default."""
    d = decide(cmp(coverage=(0.04, None, None)), mode="weak")
    text = pm.format_decision(d)
    assert "weak pass: directional, not made the deployed default" in text
    assert "PROMOTIONS.md" not in text
    rejected = pm.format_decision(decide(cmp(coverage=(0.0, None, None)), mode="weak"))
    assert "weak pass" not in rejected and "PROMOTIONS.md" not in rejected


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
    p = policy(repetitions=TWO, **SIGNED)
    d = await pm.run_gate(
        candidate=cand, incumbent=base, policy=p, policy_sha256="sha",
        records=pm.PromotionRecords(), store=store, backends=_gate_backends(cand, 4),
        decisions=StubDecisionService(), code=CLEAN, cycle_id="c1", policy_committed=True,
    )  # fmt: skip
    assert d.mode == "dev" and d.decision == "promoted" and not d.deployable
    assert store.decisions == {d.gate_id: d.model_dump(mode="json")}
    (result, sha, tags) = store.audits[0]
    assert (tags["gate_id"], tags["cycle_id"], sha) == (d.gate_id, "c1", "abc")
    assert tags["judge_version"] and set(tags["code_versions"]) == {cand.version_id,
                                                                   base.version_id}  # fmt: skip
    assert len(store.progress) == 8, "every scored run is checkpointed"
    assert "insufficient_proposals" in d.flags


async def test_publish_summary_needs_a_database(roots):
    cand, base = versions("fake")
    store = GateStore(_sealed(roots))
    with pytest.raises(pm.GateRefused, match="no main database"):
        await pm.run_gate(
            candidate=cand, incumbent=base,
            policy=policy(repetitions=TWO, publish_summary=True, **SIGNED), policy_sha256="sha",
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
        policy=policy(repetitions=TWO, publish_summary=publish, **SIGNED), policy_sha256="sha",
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


CROSS = {"label": "agrees", "mean_delta": 0.05, "ci95_low": None, "ci95_high": None,
         "judge": "claude_code:claude-opus-5-5", "gate_delta": 0.06,
         "rescored_on": "2026-10-15"}  # fmt: skip


async def test_the_cross_judge_label_lands_on_the_published_summary_only(db, database_url):
    """``womm evolve rescore`` copies its aggregate label onto the gate's page-4 row; a gate
    without a published summary (publish_summary off) gets no row."""
    d = decide(cmp(coverage=(0.04, None, None)), mode="weak")
    await pm.record_summary(db, d)
    assert await pm.record_cross_judge(db, d.gate_id, CROSS) is True
    assert _rows(database_url, "SELECT cross_judge FROM promotion_decisions") == [(CROSS,)]
    assert await pm.record_cross_judge(db, "gate_unpublished", CROSS) is False
    assert _rows(database_url, "SELECT count(*) FROM promotion_decisions") == [(1,)]


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
        candidate=cand, incumbent=base, policy=kw.pop("p", policy(repetitions=TWO, **SIGNED)),
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
    p = policy(repetitions={"min": 2, "max": 2}, publish_summary=True, **SIGNED)
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


# ----------------------------------------------------------------------------- 2026-10-14 rules


def _se(noise=0.1, reps=3, n=8):
    return (noise**2 / (reps * n) + (noise / 2) ** 2 / (reps * n)) ** 0.5


def test_weak_mode_needs_a_gain_above_one_standard_error():
    se = _se()  # cmp(): candidate SD 0.1, baseline SD 0.05, 3 repetitions, 8 cases
    below = decide(cmp(coverage=(se * 0.9, None, None)), mode="weak")
    assert below.decision == "rejected" and below.reasons == ["coverage_below_weak_bar"]
    above = decide(cmp(coverage=(se * 1.1, None, None)), mode="weak")
    assert above.decision == "promoted"
    assert any("weak bar" in n for n in above.notes)
    dev = decide(cmp(coverage=(se * 0.9, None, None)), mode="dev", deployable=False)
    assert dev.reasons == ["coverage_below_weak_bar"]
    none = decide(cmp(coverage=(0.2, None, None), noise=None), mode="weak")
    assert none.reasons[0] == "coverage_noise_unavailable"


def test_ae4_shape_precision_regression_rejects():
    d = decide(cmp(precision=(-0.15, None, None)))  # noise SD 0.1 (larger arm)
    assert d.decision == "rejected" and d.reasons == ["precision_regression"]
    assert decide(cmp(precision=(-0.05, None, None))).decision == "promoted"


def test_impact_diagnostics_are_noted_not_gated():
    d = decide(cmp(impacts=(13.0, 10.0)))
    assert d.decision == "promoted"
    assert any("impact_count" in n and "judge-sensitive" in n for n in d.notes)
    assert not any("judge-sensitive" in n for n in decide(cmp(impacts=(12.0, 10.0))).notes)


def test_choose_mode_reads_the_mdd_for_the_minimum_repetitions():
    cand, base = versions("api")
    recs = records(judge_version(base), mdd=0.05)
    recs.mdd_reports[0].repetitions = 3
    assert pm.choose_mode(policy(), recs, cand, base).mode == "statistical"


def _attempt(jv, kind="first", step="stop", experiment="e1", **kw):
    return {"judge_version": jv, "system_version": "sv_x", "split": "val",
            "experiment": experiment, "git_sha": "abc", "repetitions": 3, "kind": kind,
            "next_step": step, "stop_inputs": {"expected_gain": 0.08},
            "run_keys": [experiment], "recorded_on": "2026-10-21"} | kw  # fmt: skip


@pytest.mark.parametrize(("attempts", "message"), [
    ([_attempt("JV"), _attempt("JV", experiment="e2")], "more than one first"),
    ([_attempt("JV", step="extend")], "extension"),
])  # fmt: skip
async def test_the_r34_chain_must_be_whole_and_single(attempts, message):
    cand, base = versions("api")
    jv = judge_version(base)
    recs = records(jv)
    recs.noise_attempts = [pm.NoiseAttempt.model_validate(a | {"judge_version": jv})
                           for a in attempts]  # fmt: skip
    with pytest.raises(pm.GateRefused, match=message):
        await _preflight(cand, base, recs=recs)


async def test_a_batch_shift_on_the_r34_run_is_noted():
    cand, base = versions("api")
    recs = records(judge_version(base))
    recs.formal_noise_runs[0].batch_shift = True
    choice = await _preflight(cand, base, recs=recs)
    assert any("batch shift" in n for n in choice.notes)


def test_the_sign_summary_lists_every_rule(tmp_path, capsys):
    import datetime as dt

    from womm import cli

    path = tmp_path / "policy.yaml"
    entry = {"date": dt.date.today().isoformat(), "change": "signed", "reason": "test"}
    path.write_text(yaml.safe_dump(POLICY | {"changelog": [*POLICY["changelog"], entry]}))
    assert cli.main(["policy", "sign", "--by", "jdoe", "--policy", str(path)]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for text in (
        "precision",
        "weak bar",
        "repetitions: 3 to 6",
        "impact count may drop at most 25%",
    ):
        assert text in out


# ----------------------------------------------------------------------------- review fixes


def test_the_weak_bar_uses_the_bootstrap_se_when_there_is_one():
    d = decide(cmp(coverage=(0.04, None, None), se=0.05), mode="weak")  # formula SE ~0.023
    assert d.reasons == ["coverage_below_weak_bar"]
    assert any("bootstrap SE" in n for n in d.notes)
    fallback = decide(cmp(coverage=(0.04, None, None)), mode="weak")
    assert fallback.decision == "promoted"
    assert any("within-run noise" in n for n in fallback.notes)


def test_weak_bar_must_be_positive():
    with pytest.raises(ValueError):
        policy(weak_min_delta_se=0)


def test_a_guard_metric_missing_from_the_comparison_raises():
    with pytest.raises(ValueError, match="precision"):
        decide(cmp(drop=("precision",)))


async def test_an_mdd_report_for_other_repetitions_refuses_the_gate():
    cand, base = versions("api")
    recs = records(judge_version(base))
    recs.mdd_reports[0].repetitions = 6
    with pytest.raises(pm.GateRefused, match="repetitions"):
        await _preflight(cand, base, recs=recs)


@pytest.mark.parametrize("counts", [(9, 8), (8, 7)])
async def test_an_mdd_report_for_another_holdout_refuses_the_gate(counts):
    cand, base = versions("api")
    with pytest.raises(pm.GateRefused, match="holdout"):
        await _preflight(cand, base, store=GateStore([], counts=counts))


async def test_an_mdd_report_without_holdout_counts_refuses_the_gate():
    cand, base = versions("api")
    recs = records(judge_version(base))
    recs.mdd_reports[0].holdout_cases = None
    with pytest.raises(pm.GateRefused, match="holdout"):
        await _preflight(cand, base, recs=recs)


async def test_a_formal_run_outside_the_r34_chain_refuses_the_gate():
    cand, base = versions("api")
    jv = judge_version(base)
    recs = records(jv)
    recs.noise_attempts = [pm.NoiseAttempt.model_validate(_attempt(jv))]
    recs.formal_runs = [pm.FormalRun.model_validate(
        {"experiment": "e9", "report": "eval_9.json", "git_sha": "abc", "judge_version": jv,
         "system_version": "sv_x", "split": "val", "repetitions": 3,
         "finished_at": "2026-10-21T10:00:00"})]  # fmt: skip
    with pytest.raises(pm.GateRefused, match="not part of"):
        await _preflight(cand, base, recs=recs)
    recs.formal_runs[0].experiment = "e1"
    await _preflight(cand, base, recs=recs)


async def test_the_gate_runs_the_repetition_rule_with_the_mdd_design(monkeypatch):
    cand, base = versions("api")
    recs = records(judge_version(base))
    recs.mdd_reports[0].icc, recs.mdd_reports[0].alpha, recs.mdd_reports[0].power = 0.5, 0.1, 0.9
    seen = {}

    async def fake_compare(*a, **kw):
        seen.update(kw)
        return cmp()

    monkeypatch.setattr(pm.holdout, "compare", fake_compare)
    await pm.run_gate(
        candidate=cand, incumbent=base, policy=policy(**SIGNED), policy_sha256="sha",
        records=recs, store=GateStore([], counts=(8, 8)), backends={},
        decisions=StubDecisionService(), code=CLEAN, cycle_id="c1", policy_committed=True,
    )  # fmt: skip
    assert (seen["icc"], seen["alpha"], seen["power"]) == (0.5, 0.1, 0.9)
    assert (seen["max_repetitions"], seen["expected_gain"]) == (6, 0.08)


def test_signing_needs_a_changelog_entry_dated_today(tmp_path, capsys):
    import datetime as dt

    from womm import cli

    path = tmp_path / "policy.yaml"
    old = POLICY | {"changelog": [*POLICY["changelog"][:-1],
                                  POLICY["changelog"][-1] | {"date": "2000-01-01"}]}  # fmt: skip
    path.write_text(yaml.safe_dump(old))
    assert cli.main(["policy", "sign", "--by", "jdoe", "--policy", str(path)]) == cli.EXIT_USAGE
    assert "changelog" in capsys.readouterr().err
    today = POLICY | {"changelog": [*POLICY["changelog"],
                                    {"date": dt.date.today().isoformat(), "change": "signed",
                                     "reason": "test"}]}  # fmt: skip
    path.write_text(yaml.safe_dump(today))
    assert cli.main(["policy", "sign", "--by", "jdoe", "--policy", str(path)]) == cli.EXIT_OK


# ----------------------------------------------------------------------------- impact-count drop


def test_an_impact_count_drop_beyond_the_bound_rejects():
    d = decide(cmp(impacts=(7.0, 10.0)))  # -30%, bound 25%
    assert d.decision == "rejected" and d.reasons == ["impact_count_drop"]


def test_an_impact_count_drop_within_the_bound_passes():
    assert decide(cmp(impacts=(7.5, 10.0))).decision == "promoted"  # exactly -25%
    assert decide(cmp(impacts=(8.0, 10.0))).decision == "promoted"


def test_an_impact_count_rise_never_rejects():
    d = decide(cmp(impacts=(20.0, 10.0)))
    assert d.decision == "promoted" and any("judge-sensitive" in n for n in d.notes)


def test_missing_impact_counts_reject():
    c = cmp()
    c.diagnostics["impact_count"] = DiagnosticMeans(candidate_mean=None, baseline_mean=None,
                                                    n_cases=0)  # fmt: skip
    assert decide(c).reasons == ["impact_count_unavailable"]
    c.diagnostics.pop("impact_count")
    assert decide(c).reasons == ["impact_count_unavailable"]


def test_the_impact_count_guard_is_required_and_a_ratio():
    with pytest.raises(ValueError):
        pm.PromotionPolicy.model_validate({k: v for k, v in POLICY.items()
                                           if k != "impact_count_guard"})  # fmt: skip
    with pytest.raises(ValueError):
        policy(impact_count_guard={"max_drop_ratio": 1.0})
    assert policy().impact_count_guard.max_drop_ratio == 0.25


# ----------------------------------------------------------------------------- target category


def _with_target(c, n_items=6, n_proposals=3, cand=4.5, base=3.0, category="workforce"):
    t = holdout.TargetCategoryCount(category=category, n_items=n_items, n_proposals=n_proposals,
                                    candidate_covered=cand, baseline_covered=base)  # fmt: skip
    return c.model_copy(update={"target_category": t})


def test_the_target_category_is_a_report_only_note():
    plain, noted = decide(cmp()), decide(_with_target(cmp()))
    assert noted.decision == plain.decision == "promoted" and noted.reasons == plain.reasons
    assert noted.notes == [*plain.notes,
                           "target category workforce (the new expert's pattern): covered 4.5 vs "
                           "3.0 of 6 holdout items in 3 proposals, mean over repetitions "
                           "(candidate vs incumbent); report-only, it does not gate"]  # fmt: skip
    rejected = decide(_with_target(cmp(coverage=(0.0, -0.05, 0.05))))
    assert rejected.decision == "rejected" and any("target category" in n for n in rejected.notes)


@pytest.mark.parametrize("n_items, n_proposals", [(2, 2), (6, 1), (0, 0)])
def test_a_thin_target_category_prints_no_numbers(n_items, n_proposals):
    d = decide(_with_target(cmp(), n_items=n_items, n_proposals=n_proposals))
    note = next(n for n in d.notes if n.startswith("target category"))
    assert note == ("target category workforce (the new expert's pattern): too few holdout items "
                    "to read (fewer than 3 items or a single proposal); report-only")  # fmt: skip


def test_no_target_category_no_note():
    assert not any("target category" in n for n in decide(cmp()).notes)


def _arow(vid, diff=None, twin_of=None):
    return {"version_id": vid, "cycle_id": "c", "twin_of": twin_of, "diff": diff}


async def test_the_target_category_comes_from_the_archived_topology_diff():
    topo = {"ops": [], "target_pattern": "missed_impact/social_environmental/none"}
    archive = MemoryArchive(
        _arow("sv_topo", topo), _arow("sv_twin", None, "sv_topo"),
        _arow("sv_gepa", {"ops": [], "rendered": ""}),
        _arow("sv_unc", {"ops": [], "target_pattern": "missed_impact/uncategorised/legal"}),
        _arow("sv_fields", {"ops": [], "target_pattern": {"kind": "missed_impact",
                                                          "category": "workforce",
                                                          "owner": "none"}}),
    )  # fmt: skip
    assert await pm.target_category(archive, "sv_topo") == "social_environmental"
    assert await pm.target_category(archive, "sv_twin") == "social_environmental"
    assert await pm.target_category(archive, "sv_gepa") is None, "a prompt-only candidate"
    assert await pm.target_category(archive, "sv_unc") is None
    # The form womm.api.e2e.seed_evolution archives (and page 4 reads) used to crash the gate.
    assert await pm.target_category(archive, "sv_fields") == "workforce"
    assert await pm.target_category(archive, "sv_missing") is None


async def test_the_gate_passes_the_target_category_to_the_comparison(roots):
    cand, base = versions("fake")
    d = await _signed_gate(cand, base, GateStore(_sealed(roots)), target_category="sme_specific")
    assert d.decision == "promoted"
    assert ("target category sme_specific (the new expert's pattern): covered 8.0 vs 2.0 of 8 "
            "holdout items in 2 proposals, mean over repetitions (candidate vs incumbent); "
            "report-only, it does not gate") in d.notes  # fmt: skip
