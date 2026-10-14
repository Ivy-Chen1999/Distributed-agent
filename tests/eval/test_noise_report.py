"""R34 noise and the holdout minimum detectable delta (`womm noise report`)."""

import math
import statistics

import pytest

from womm.eval import noise_report as nr
from womm.eval.evaluators import CaseScore
from womm.eval.holdout import _pooled_sd
from womm.evolve import promotion as pm

from .calibration_factory import JV, SV, golden, make_report

VALUES = {0: [0.5, 0.7, 0.6], 1: [0.2, 0.2, 0.5], 2: [0.9, 0.9, 0.9]}


def _metric(case_id, metric, rep):
    if metric == "omissions_addressed":
        return None
    return VALUES[int(case_id[6]) % 3][(rep - 1) % 3]


def test_pooled_noise_matches_the_holdout_estimator():
    report = make_report(golden(3), reps=3, metric=_metric)
    got = nr.pooled_noise(report["scores"], "coverage")
    scores = [CaseScore(case_id=s["case_id"], scenario_id="x", outcome="scored",
                        coverage=s["coverage"]) for s in report["scores"]]  # fmt: skip
    assert got["sd"] == pytest.approx(_pooled_sd(scores, "coverage"))
    pooled = sum(statistics.variance(v) * 2 for v in VALUES.values()) / 6
    assert got["sd"] == pytest.approx(math.sqrt(pooled)) and got["dof"] == 6
    assert nr.pooled_noise(report["scores"], "omissions_addressed")["sd"] is None


@pytest.mark.parametrize(("p", "df", "want"), [
    (0.975, 1, 12.7062), (0.975, 7, 2.3646), (0.8, 7, 0.8960), (0.975, 30, 2.0423),
    (0.8, 2, 1.0607), (0.025, 4, -2.7764),
])  # fmt: skip
def test_t_quantiles(p, df, want):
    assert nr.t_quantile(p, df) == pytest.approx(want, abs=1e-4)


def test_mdd_formula():
    m = nr.mdd(0.1, repetitions=3, n_cases=8, n_proposals=8, icc=1.0)
    se = math.sqrt(2 * 0.01 / (3 * 8))
    assert m["deff"] == 1 and m["df"] == 7 and m["se"] == pytest.approx(se)
    assert m["mdd"] == pytest.approx((2.3646 + 0.8960) * se, rel=1e-4)
    clustered = nr.mdd(0.1, repetitions=3, n_cases=8, n_proposals=4, icc=1.0)
    assert clustered["deff"] == 2 and clustered["mdd"] > m["mdd"]
    assert nr.mdd(0.1, repetitions=3, n_cases=8, n_proposals=4, icc=0.0)["deff"] == 1
    assert nr.mdd(0.1, repetitions=6, n_cases=8, n_proposals=8)["mdd"] < m["mdd"]
    with pytest.raises(ValueError):
        nr.mdd(0.1, repetitions=3, n_cases=3, n_proposals=4)


def test_formal_problems_and_records():
    dev = make_report(golden(2), reps=6, metric=_metric)
    assert any("not a formal run" in p for p in nr.formal_problems(dev))
    formal = make_report(golden(2, split="val"), reps=6, metric=_metric, formal=True,
                         backends=("api",))  # fmt: skip
    assert nr.formal_problems(formal) == []
    few = make_report(golden(2), reps=2, metric=_metric, formal=True, backends=("api",))
    assert any("fewer than 3" in p for p in nr.formal_problems(few))
    cc = make_report(golden(2), reps=6, metric=_metric, formal=True, backends=("claude_code",))
    assert any("api only" in p for p in nr.formal_problems(cc))

    result = nr.noise_report(formal, repetitions=3, n_cases=8, n_proposals=4)
    assert any("fewer than 5" in n for n in result["notes"])
    noise_run, mdds = nr.records_for(result, "2026-10-21")
    run = pm.FormalNoiseRun.model_validate(noise_run)
    assert (run.judge_version, run.split, run.repetitions, run.system_version) == (
        JV, "val", 6, SV)  # fmt: skip
    assert run.experiment == "womm-x-val-formal-1234"
    reports = [pm.MddReport.model_validate(m) for m in mdds]
    # omissions has no values; the others have noise
    assert {r.metric for r in reports} == {"coverage", "grounding"}
    assert all(r.repetitions == 3 and r.holdout_proposals == 4 for r in reports)


# ----------------------------------------------------------------------------- data-driven R34


@pytest.mark.parametrize(("p", "df", "want"), [
    (0.10, 6, 2.2041), (0.90, 6, 10.6446), (0.10, 15, 8.5468), (0.90, 15, 22.3071),
    (0.90, 1, 2.7055), (0.50, 2, 1.3863),
])  # fmt: skip
def test_chi2_quantiles(p, df, want):
    assert nr.chi2_quantile(p, df) == pytest.approx(want, abs=1e-3)


def test_sd_interval_and_sd_star():
    lo, hi = nr.sd_interval(0.1, 6, level=0.8)
    assert lo == pytest.approx(0.1 * math.sqrt(6 / 10.6446), rel=1e-3)
    assert hi == pytest.approx(0.1 * math.sqrt(6 / 2.2041), rel=1e-3)
    star = nr.sd_star(0.08, repetitions=3, n_cases=8, n_proposals=8)
    assert nr.mdd(star, repetitions=3, n_cases=8, n_proposals=8)["mdd"] == pytest.approx(0.08)


@pytest.mark.parametrize(("sd", "want"), [(0.04, "stop"), (0.2, "stop"), (0.09, "extend")])
def test_r34_stop_rule(sd, want):
    star = nr.sd_star(0.08, repetitions=3, n_cases=8, n_proposals=8)  # about 0.085
    rule = nr.r34_next_step(sd, 6, star)
    assert rule["next_step"] == want
    assert rule["sd_star"] == star and rule["interval"][0] < sd < rule["interval"][1]


def test_comparison_extends_only_when_the_extra_runs_can_reach_the_gain():
    kw = {"r_min": 3, "r_max": 6, "n_cases": 8, "n_proposals": 8, "expected_gain": 0.08}
    assert nr.comparison_extends(0.05, **kw)["extend"] is False  # 3 already resolves it
    assert nr.comparison_extends(0.10, **kw)["extend"] is True  # 3 cannot, 6 can
    assert nr.comparison_extends(0.30, **kw)["extend"] is False  # even 6 cannot
    assert nr.comparison_extends(None, **kw)["extend"] is False
    assert nr.comparison_extends(0.10, **kw | {"n_proposals": 1})["extend"] is False


def _formal(reps, sha="abc", metric=_metric, **kw):
    report = make_report(golden(3, split="val"), reps=reps, metric=metric, formal=True,
                         backends=("api",), **kw)  # fmt: skip
    report["metadata"]["git_sha"] = sha
    return report


def test_pooling_two_runs_sums_repetitions_and_checks_identity():
    first, second = _formal(3), _formal(3)
    second["metadata"]["langsmith_experiment"] = "womm-x-val-formal-5678"
    pooled = nr.pool_reports([first, second])
    assert pooled["metadata"]["repetitions"] == 6 and len(pooled["scores"]) == 18
    assert pooled["metadata"]["langsmith_experiment"] == (
        "womm-x-val-formal-1234, womm-x-val-formal-5678")  # fmt: skip
    assert nr.formal_problems(pooled) == []
    for field, value in (("git_sha", "zzz"), ("split", "train"), ("system_version", "sv_other")):
        other = _formal(3)
        other["metadata"][field] = value
        if field == "system_version":
            other["system_version"] = value
        with pytest.raises(ValueError, match=field):
            nr.pool_reports([first, other])


def test_batch_shift_is_flagged_when_the_halves_differ_beyond_noise():
    same = nr.batch_shift(_formal(3), _formal(3))
    assert same["flag"] is False and same["shift"] == pytest.approx(0.0)

    def shifted(case_id, metric, rep):
        v = _metric(case_id, metric, rep)
        return None if v is None else min(1.0, v + 0.3)

    moved = nr.batch_shift(_formal(3), _formal(3, metric=shifted))
    assert moved["flag"] is True and moved["shift"] > moved["first_sd"]


def test_null_pass_rate_of_the_weak_bar():
    report = _formal(6)
    zero_bar = nr.null_pass_rate(report["scores"], bar_se=0.0)
    high_bar = nr.null_pass_rate(report["scores"], bar_se=1.0)
    assert 0.3 < zero_bar["rate"] < 0.7  # a version against itself: about half above 0
    assert high_bar["rate"] < zero_bar["rate"]
    assert nr.null_pass_rate(report["scores"], bar_se=1.0) == high_bar  # seeded
