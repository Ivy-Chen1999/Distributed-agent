"""Eval reports carry Failure Memory events (U1) next to the legacy R14b records."""

import json

from womm.decisions.stub import StubDecisionService
from womm.eval.evaluators import JudgeOutput
from womm.eval.run_eval import evaluate_cases
from womm.llm.fake import FakeBackend

from ..graph.conftest import fake_sv
from .test_run_eval_fake import CASE, CLEAN, _script


async def test_train_eval_records_events_and_scored_runs(fixture):
    script = _script(reps=2)
    missed = CASE.expected_impacts[0].expected_id
    judge = JudgeOutput(
        expected=[{"expected_id": e.expected_id, "covered": e.expected_id != missed,
                   "impact_id": "I1", "justification": "j"} for e in CASE.expected_impacts],
        omissions=[{"omission_id": o.omission_id, "addressed": True, "impact_id": "I1",
                    "justification": "j"} for o in CASE.important_omissions],
    )  # fmt: skip
    script["judge"] = [judge, judge]
    report = await evaluate_cases(
        [CASE], sv=fake_sv(), fixture=fixture, backends={"fake": FakeBackend(script)},
        decisions=StubDecisionService(), code=CLEAN, judge_prompt="p", repetitions=2,
    )  # fmt: skip
    missed_rows = [e for e in report.failure_events if e.kind == "missed_impact"]
    assert [(e.item_id, e.repetition) for e in missed_rows] == [(missed, 1), (missed, 2)]
    assert {e.split for e in report.failure_events} == {CASE.split}
    assert [r.repetition for r in report.case_runs] == [1, 2]
    saved = json.loads(report.to_json())
    assert len(saved["failure_events"]) == len(report.failure_events)
    assert len(saved["case_runs"]) == 2
