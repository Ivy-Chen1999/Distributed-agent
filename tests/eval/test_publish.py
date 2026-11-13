"""`womm eval-publish`: the compact payload, holdout refusal, and the counts with a range."""

import pytest

from womm.eval.golden import GoldenError, load_all_golden
from womm.eval.publish import PublishError, check_payload, payload, summarize

TENANT = "11111111-2222-4333-8444-555555555555"


@pytest.fixture(scope="module")
def cases():
    return {c.case_id: c for c in load_all_golden()}


def _verdict(covered, expected, addressed=0, omissions=0, grounding=1.0, precision=0.5):
    return {
        "outcome": "scored", "run_status": "succeeded", "grounding": grounding,
        "precision": precision, "coverage": covered / expected,
        "judge": {
            "expected": [{"covered": i < covered} for i in range(expected)],
            "omissions": [{"addressed": i < addressed} for i in range(omissions)],
        },
    }  # fmt: skip


def _report(scores, **meta):
    return {"system_version": "sv_aaaaaaaaaaaa", "aborted": None,
            "metadata": {"git_sha": "abc", "repetitions": 2, **meta}, "scores": scores,
            "case_runs": [{"judge_version": "jv_1"}]}  # fmt: skip


def _two_cases(cases):
    a, b = sorted(c for c, case in cases.items() if case.split == "train")[:2]
    return a, b


def test_payload_counts_verdicts_and_numbers_repetitions(cases):
    a, b = _two_cases(cases)
    data = payload(_report([
        {"case_id": a, **_verdict(3, 4, 1, 2)}, {"case_id": a, **_verdict(2, 4, 2, 2)},
        {"case_id": b, **_verdict(5, 5)},
    ]), cases)  # fmt: skip
    runs = data["runs"]
    assert [(r["case_id"], r["repetition"]) for r in runs] == [(a, 1), (a, 2), (b, 1)]
    assert runs[0]["covered"] == 3 and runs[0]["expected"] == 4 and runs[0]["addressed"] == 1
    assert data["judge_version"] == "jv_1" and data["repetitions"] == 2
    s = summarize(data)
    assert s["reference_impacts"]["found"] == 10 and s["reference_impacts"]["total"] == 13
    assert s["cases"] == 2 and s["runs"] == 3 and s["excluded_runs"] == 0
    low, high = s["reference_impacts"]["range"]
    assert low <= s["reference_impacts"]["value"] <= high


def test_a_quality_failure_counts_zero_of_the_cases_reference_impacts(cases):
    a, _ = _two_cases(cases)
    failed = {"case_id": a, "outcome": "scored", "run_status": "failed", "coverage": 0.0,
              "judge": None, "grounding": None}  # fmt: skip
    data = payload(_report([failed]), cases)
    (run,) = data["runs"]
    assert run["covered"] == 0 and run["expected"] == len(cases[a].expected_impacts)
    assert summarize(data)["reference_impacts"]["value"] == 0.0


def test_errored_and_judge_error_runs_are_excluded_and_counted_apart(cases):
    a, b = _two_cases(cases)
    data = payload(_report([
        {"case_id": a, "outcome": "errored", "error": "rate limit"},
        {"case_id": a, **_verdict(1, 2), "judge_error": "judge skipped ids"},
        {"case_id": b, **_verdict(2, 2)},
    ]), cases)  # fmt: skip
    s = summarize(data)
    assert s["excluded_runs"] == 2 and s["runs"] == 1
    assert (s["reference_impacts"]["found"], s["reference_impacts"]["total"]) == (2, 2)
    assert s["reference_impacts"]["range"] is None  # one case: no range


def test_a_holdout_case_refuses_the_whole_report(cases):
    a, _ = _two_cases(cases)
    sealed = {a: cases[a].model_copy(update={"split": "holdout"})}
    with pytest.raises(GoldenError):
        payload(_report([{"case_id": a, **_verdict(1, 1)}]), sealed)


def test_unknown_cases_and_aborted_reports_are_refused(cases):
    with pytest.raises(PublishError):
        payload(_report([{"case_id": "nope", **_verdict(1, 1)}]), cases)
    with pytest.raises(PublishError):
        payload({**_report([]), "aborted": "rate limit"}, cases)


def test_server_check_refuses_holdout_splits_and_odd_ids():
    run = {"case_id": "c", "split": "train", "repetition": 1, "covered": 1, "expected": 1,
           "addressed": 0, "omissions": 0}  # fmt: skip
    base = {"system_version": "sv_aaaaaaaaaaaa", "runs": [run]}
    check_payload({**base, "experiment_tenant_id": TENANT, "experiment_project_id": TENANT})
    with pytest.raises(PublishError, match="train/val"):
        check_payload({**base, "runs": [run, {**run, "split": "holdout"}]})
    with pytest.raises(PublishError, match="LangSmith id"):
        check_payload({**base, "experiment_tenant_id": "https://evil.example"})
