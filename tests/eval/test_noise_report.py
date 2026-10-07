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
    few = make_report(golden(2), reps=3, metric=_metric, formal=True, backends=("api",))
    assert any("fewer than 6" in p for p in nr.formal_problems(few))
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
