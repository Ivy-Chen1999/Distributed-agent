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


def test_label_decisions_and_calibration():
    from womm.eval.evaluators import CaseScore, calibration, label_decisions
    from womm.models.decisions import DecisionRecord
    from womm.models.findings import FindingDraft, ImpactFinding, Provenance

    def finding(agent):
        prov = Provenance(agent=agent, system_version="sv", prompt_hash="p", backend="f", model="m")
        draft = FindingDraft(provision_key="k", affected_actor="a", impact="i", mechanism="m",
                             evidence=[], confidence=0.5)  # fmt: skip
        return ImpactFinding.from_draft(draft, run_id="r", index=0, provenance=prov)

    run = _run()
    run.dossier.impacts = [
        DossierImpact(impact_id="I1", summary="s", findings=[finding("legal")], merged=True),
        DossierImpact(impact_id="I2", summary="s", findings=[finding("fiscal")], merged=True),
    ]
    run.decisions = [
        DecisionRecord(
            decision_point="router.relevance", subject=a, input_summary="",
            decision="relevant", probability=p, mode="shadow", decider="jev",
            system_version="sv",
        )
        for a, p in (("legal", 0.9), ("fiscal", 0.8), ("stakeholder", 0.3))
    ]  # fmt: skip
    judge = _judge(covered=1)
    judge.expected[0].impact_id = "I1"  # only I1 (legal) covers an expected impact
    labels = {d["subject"]: d["relevant"] for d in label_decisions(run, judge)}
    assert labels == {"legal": True, "fiscal": False, "stakeholder": False}

    s = CaseScore(case_id="c", scenario_id="s", outcome="scored",
                  decisions=label_decisions(run, judge))  # fmt: skip
    cal = calibration([s])
    assert cal["n"] == 3 and cal["base_rate"] == pytest.approx(1 / 3)
    assert cal["brier"] == pytest.approx(((0.9 - 1) ** 2 + 0.8**2 + 0.3**2) / 3)
    stub = CaseScore(
        case_id="c",
        scenario_id="s",
        outcome="scored",
        decisions=[{"subject": "legal", "probability": None, "relevant": True}],
    )
    assert calibration([stub]) is None


def _judge_matching(impact_ids: list[str | None]) -> JudgeOutput:
    """Expected impact i is covered by impact_ids[i] (None: not covered)."""
    out = _judge(covered=0)
    for v, iid in zip(out.expected, impact_ids, strict=False):
        v.covered, v.impact_id = iid is not None, iid
    return out


async def test_precision_counts_each_matched_impact_once():
    n = len(CASE.expected_impacts)
    assert n >= 3
    # two expected impacts land on I0, one on I1; I2 and I3 match nothing; an unknown id is ignored
    ids = ["I0", "I0", "I1"] + [None] * (n - 3)
    backend = FakeBackend({"judge": [_judge_matching(ids)]})
    score, _ = await score_case(CASE, _run(impacts=4), backend, ROLE, "p")
    assert score.precision == pytest.approx(2 / 4)
    assert score.impact_count == 4
    assert score.impact_chars == 4  # each impact is summary "s" with no findings


async def test_precision_ignores_ids_not_in_the_dossier():
    n = len(CASE.expected_impacts)
    ids = ["I0", "X9"] + [None] * (n - 2)
    backend = FakeBackend({"judge": [_judge_matching(ids)]})
    score, _ = await score_case(CASE, _run(impacts=2), backend, ROLE, "p")
    assert score.precision == pytest.approx(1 / 2)


async def test_impact_chars_counts_summary_and_finding_text():
    from womm.eval.evaluators import impact_chars

    run = _run(impacts=1)
    run.dossier.impacts[0].summary = "abcd"
    assert impact_chars(run.dossier) == 4


async def test_measures_are_null_without_a_judge_or_when_errored():
    score, _ = await score_case(
        CASE, _run(), FakeBackend({"judge": [LLMError("schema_invalid", "bad", attempts=3)]}),
        ROLE, "p",
    )  # fmt: skip
    assert score.precision is None and score.impact_count == 1
    run = _run(status=RunStatus.failed, error="planner failed: [auth] Not logged in")
    score, _ = await score_case(CASE, run, FakeBackend({}), ROLE, "p")
    assert (score.precision, score.impact_count, score.impact_chars) == (None, None, None)


async def test_empty_dossier_counts_zero_impacts_and_scores_zero_precision():
    fails = [ExpertFailure(agent=a, error_kind="schema_invalid", message="t") for a in "abc"]
    run = _run(status=RunStatus.failed, failures=fails, impacts=0)
    score, _ = await score_case(CASE, run, FakeBackend({}), ROLE, "p")
    # zero like coverage, so a quality failure never leaves a holdout case unscored
    assert (score.impact_count, score.impact_chars, score.precision) == (0, 0, 0.0)


class _PricedFake(FakeBackend):
    """Fake judge whose every attempt reports 100 + 20 tokens at $0.01."""

    async def _invoke(self, role_name, system_prompt, user_content, schema, role_cfg, agent):
        out, usage = await super()._invoke(role_name, system_prompt, user_content, schema,
                                           role_cfg, agent)  # fmt: skip
        return out, usage.model_copy(
            update={"input_tokens": 100, "output_tokens": 20, "cost_usd": 0.01}
        )


async def test_judge_usage_is_recorded_apart_from_the_run():
    score, _ = await score_case(CASE, _run(), _PricedFake({"judge": [_judge(covered=2)]}),
                                ROLE, "p")  # fmt: skip
    assert (score.judge_tokens, score.judge_cost_usd) == (120, pytest.approx(0.01))
    assert score.cost_usd == 0.0  # the run's own cost stays separate
    agg = aggregate([score])
    assert agg["judge_cost_usd"] == pytest.approx(0.01)


async def test_no_judge_call_records_no_judge_usage():
    run = _run().model_copy(update={"dossier": None, "status": RunStatus.failed})
    score, _ = await score_case(CASE, run, _PricedFake({}), ROLE, "p")
    assert (score.judge_tokens, score.judge_cost_usd) == (0, 0.0)
