import pytest

from womm.eval.evaluators import JudgeOutput, aggregate, score_case
from womm.eval.golden import load_all_golden
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend
from womm.models.dossier import DossierImpact, ImpactDossier
from womm.models.findings import ExpertFailure
from womm.models.run import CodeIdentity, GroundingStats, RunResult, RunStatus
from womm.models.system_version import RoleConfig

CASE = next(c for c in load_all_golden() if c.case_id.startswith("case_02"))
ROLE = RoleConfig(backend="fake", model="m", prompt="prompts/judge_coverage.md")


def _run(status=RunStatus.succeeded, impacts=1, failures=(), error=None) -> RunResult:
    dossier = ImpactDossier(
        run_id="r", scenario_id=CASE.scenario_id, status="succeeded", system_version="sv",
        impacts=[DossierImpact(impact_id=f"I{i}", summary="s", findings=[], merged=True)
                 for i in range(impacts)],
    )  # fmt: skip
    return RunResult(
        run_id="r", scenario_id=CASE.scenario_id, status=status, system_version="sv",
        code_identity=CodeIdentity(git_sha="x", dirty=False), dossier=dossier,
        failures=list(failures), grounding=GroundingStats(passed=3, total=4), error=error,
    )  # fmt: skip


def _judge(covered: int) -> JudgeOutput:
    return JudgeOutput(
        expected=[
            {"expected_id": e.expected_id, "covered": i < covered, "impact_id": None,
             "justification": "j"}
            for i, e in enumerate(CASE.expected_impacts)
        ],
        omissions=[
            {"omission_id": o.omission_id, "addressed": True, "impact_id": None,
             "justification": "j"}
            for o in CASE.important_omissions
        ],
    )  # fmt: skip


async def test_coverage_fraction():
    backend = FakeBackend({"judge": [_judge(covered=2)]})
    score, _ = await score_case(CASE, _run(), backend, ROLE, "p")
    assert score.outcome == "scored"
    assert score.coverage == pytest.approx(2 / len(CASE.expected_impacts))
    assert score.omissions_addressed == 1.0
    assert score.grounding == 0.75


async def test_judge_failure_gives_null_not_zero():
    backend = FakeBackend({"judge": [LLMError("schema_invalid", "bad", attempts=3)]})
    score, _ = await score_case(CASE, _run(), backend, ROLE, "p")
    assert score.outcome == "scored"
    assert score.coverage is None and score.judge_error


async def test_judge_skipping_ids_gives_null():
    partial = _judge(covered=1)
    partial.expected = partial.expected[:1]
    score, _ = await score_case(CASE, _run(), FakeBackend({"judge": [partial]}), ROLE, "p")
    assert score.coverage is None and score.judge_error == "judge skipped ids"


async def test_auth_failure_is_errored_and_excluded():
    run = _run(status=RunStatus.failed, error="planner failed: [auth] Not logged in")
    score, _ = await score_case(CASE, run, FakeBackend({}), ROLE, "p")
    assert score.outcome == "errored"
    agg = aggregate([score])
    assert agg["scored"] == 0 and agg["coverage"] is None and agg["partial"]


async def test_all_experts_infra_failure_is_errored():
    fails = [ExpertFailure(agent=a, error_kind="timeout", message="t") for a in "abc"]
    run = _run(status=RunStatus.failed, failures=fails)
    score, _ = await score_case(CASE, run, FakeBackend({}), ROLE, "p")
    assert score.outcome == "errored"


async def test_quality_failure_scores_zero():
    fails = [ExpertFailure(agent=a, error_kind="schema_invalid", message="t") for a in "abc"]
    run = _run(status=RunStatus.failed, failures=fails, impacts=0)
    score, _ = await score_case(CASE, run, FakeBackend({}), ROLE, "p")
    assert (score.outcome, score.coverage) == ("scored", 0.0)


def test_aggregate_skips_none():
    from womm.eval.evaluators import CaseScore

    a = CaseScore(case_id="a", scenario_id="s", outcome="scored", coverage=0.5, grounding=1.0)
    b = CaseScore(case_id="b", scenario_id="s", outcome="scored", coverage=None, grounding=0.5)
    agg = aggregate([a, b])
    assert agg["coverage"] == 0.5 and agg["grounding"] == 0.75 and agg["partial"]


async def test_schema_error_mentioning_authority_is_not_infra():
    """'authority' contains 'auth'; only the '[auth]' tag marks an infrastructure error."""
    run = _run(
        status=RunStatus.failed,
        impacts=0,
        error="planner failed: [schema_invalid] input_value='competent authority shall...'",
    )
    score, _ = await score_case(CASE, run, FakeBackend({}), ROLE, "p")
    assert (score.outcome, score.coverage) == ("scored", 0.0)


async def test_duplicate_judge_verdicts_give_null():
    out = _judge(covered=len(CASE.expected_impacts))
    out.expected = out.expected[:-1] + [out.expected[0]]
    score, _ = await score_case(CASE, _run(), FakeBackend({"judge": [out]}), ROLE, "p")
    assert score.coverage is None


async def test_synthesis_auth_failure_is_errored():
    run = _run(status=RunStatus.degraded)
    run.synthesis_error = "[auth] Not logged in"
    score, _ = await score_case(CASE, run, FakeBackend({}), ROLE, "p")
    assert score.outcome == "errored"


def test_noise_and_failure_records():
    from womm.eval.evaluators import CaseScore, failure_records, noise

    judge = _judge(covered=1)
    scores = [
        CaseScore(case_id="c", scenario_id="s", outcome="scored", coverage=0.4, grounding=1.0,
                  omissions_addressed=1.0, judge=judge, expert_failures={"fiscal": "timeout"}),
        CaseScore(case_id="c", scenario_id="s", outcome="scored", coverage=0.8, grounding=0.8,
                  omissions_addressed=1.0),
        CaseScore(case_id="c", scenario_id="s", outcome="errored", error="[auth]"),
    ]  # fmt: skip
    n = noise(scores)["c"]["coverage"]
    assert (
        n["n"] == 2
        and n["mean"] == pytest.approx(0.6)
        and n["stdev"] == pytest.approx(0.2828, 1e-3)
    )
    cats = sorted(r["category"] for r in failure_records(scores))
    assert cats == ["expert_timeout", "low_coverage", "low_grounding", "missed_expected_impacts"]
    missed = next(r for r in failure_records(scores) if r["category"] == "missed_expected_impacts")
    assert len(missed["detail"]["expected_ids"]) == len(CASE.expected_impacts) - 1


def test_failure_records_judge_error():
    from womm.eval.evaluators import CaseScore, failure_records

    s = CaseScore(case_id="c", scenario_id="s", outcome="scored", judge_error="judge skipped ids")
    (r,) = failure_records([s])
    assert r["category"] == "judge_error" and r["detail"]["error"] == "judge skipped ids"
