import json

import pytest

from womm.config import REPO_ROOT
from womm.decisions.stub import StubDecisionService
from womm.eval.evaluators import JudgeOutput
from womm.eval.golden import load_all_golden
from womm.eval.run_eval import (
    BaselineRefused,
    check_formal,
    evaluate_cases,
    experiment_metadata,
    sync_dataset,
)
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity

from ..graph.conftest import fake_sv, good_script

CASE = next(c for c in load_all_golden() if c.scenario_id == "eval_sme_impacts")
CLEAN = CodeIdentity(git_sha="abc", dirty=False)


def _judge():
    return JudgeOutput(
        expected=[{"expected_id": e.expected_id, "covered": True, "impact_id": "I1",
                   "justification": "j"} for e in CASE.expected_impacts],
        omissions=[{"omission_id": o.omission_id, "addressed": False, "impact_id": None,
                    "justification": "j"} for o in CASE.important_omissions],
    )  # fmt: skip


def _script(reps=1):
    s = good_script()

    def synth(_system, user):
        """Finding ids depend on the run id, so merge whatever findings synthesis receives."""
        rows = json.loads(user.split("Validated findings:\n", 1)[1])
        ids = [r["finding_id"] for r in rows]
        return {
            "impacts": [{"impact_id": "I1", "summary": "s", "finding_ids": ids}],
            "chains": [],
            "disagreements": [],
            "open_questions": [],
            "discarded": [],
        }

    for key in ("planner", "expert/legal", "expert/fiscal", "expert/stakeholder"):
        s[key] = s[key] * reps
    s["synthesis"] = [synth] * reps
    s["judge"] = [_judge()] * reps
    return s


async def _evaluate(fixture, script, **kw):
    sv = fake_sv()
    return await evaluate_cases(
        [CASE], sv=sv, fixture=fixture, backends={"fake": FakeBackend(script)},
        decisions=StubDecisionService(), code=kw.pop("code", CLEAN), judge_prompt="p", **kw,
    )  # fmt: skip


async def test_end_to_end_fake_eval(fixture):
    report = await _evaluate(fixture, _script())
    s = report.summary
    assert s["scored"] == 1 and s["coverage"] == 1.0 and s["omissions_addressed"] == 0.0
    assert report.metadata["system_version"].startswith("sv_")
    assert report.metadata["backends"] == ["fake"]


async def test_each_case_runs_on_its_own_fixture(second_fixture):
    other = CASE.model_copy(update={"case_id": "case_99_other", "fixture": "other"})
    report = await evaluate_cases(
        [CASE, other], sv=fake_sv(), backends={"fake": FakeBackend(_script(reps=2))},
        decisions=StubDecisionService(), code=CLEAN, judge_prompt="p",
    )  # fmt: skip
    assert [s.case_id for s in report.scores] == [CASE.case_id, "case_99_other"]
    assert all(s.coverage == 1.0 for s in report.scores)
    assert report.metadata["fixtures"] == ["ai_act", "other"]
    assert report.metadata["splits"] == ["train"]


async def test_holdout_case_is_refused(fixture):
    from womm.eval.golden import GoldenError

    sealed = CASE.model_copy(update={"split": "holdout"})
    with pytest.raises(GoldenError, match="holdout"):
        await evaluate_cases(
            [sealed], sv=fake_sv(), fixture=fixture, backends={"fake": FakeBackend({})},
            decisions=StubDecisionService(), code=CLEAN, judge_prompt="p",
        )  # fmt: skip


async def test_repetitions(fixture):
    report = await _evaluate(fixture, _script(reps=2), repetitions=2)
    assert len(report.scores) == 2 and len(set(report.run_ids)) == 2


async def test_rate_limit_aborts_without_summary(fixture):
    script = _script()
    script["judge"] = [LLMError("rate_limit", "usage limit reached")]
    report = await _evaluate(fixture, script)
    assert report.aborted and report.summary is None


async def test_dirty_tree_refuses_baseline(fixture):
    with pytest.raises(BaselineRefused):
        await _evaluate(fixture, _script(), baseline=True,
                        code=CodeIdentity(git_sha="x", dirty=True))  # fmt: skip


def test_baseline_kind_smoke_vs_reference():
    from womm.config import DEFAULT_SYSTEM_VERSION
    from womm.models.system_version import load_system_version

    cc = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT)
    assert experiment_metadata(cc, CLEAN, True, 1)["baseline_kind"] == "smoke"
    assert "baseline_kind" not in experiment_metadata(cc, CLEAN, False, 1)


async def test_two_splits_get_separate_summaries(second_fixture):
    val = CASE.model_copy(update={"case_id": "case_99_val", "fixture": "other", "split": "val"})
    report = await evaluate_cases(
        [CASE, val], sv=fake_sv(), backends={"fake": FakeBackend(_script(reps=2))},
        decisions=StubDecisionService(), code=CLEAN, judge_prompt="p",
    )  # fmt: skip
    assert report.metadata["split"] == "mixed"
    assert report.metadata["splits"] == ["train", "val"]
    by_split = json.loads(report.to_json())["summary_by_split"]
    assert set(by_split) == {"train", "val"}
    assert by_split["train"]["summary"]["cases"] == 1 and by_split["val"]["summary"]["cases"] == 1
    assert "noise" not in by_split["train"]


async def test_single_split_is_named_in_metadata(fixture):
    report = await _evaluate(fixture, _script(reps=2), repetitions=2)
    assert report.metadata["split"] == "train" and "formal" not in report.metadata
    data = json.loads(report.to_json())
    assert data["summary_by_split"]["train"]["noise"][CASE.case_id]["coverage"]["n"] == 2


def _api_sv():
    from womm.models.system_version import load_system_version

    return load_system_version(REPO_ROOT / "system_versions/v0.1-api.yaml", REPO_ROOT)


def test_formal_run_refuses_non_api_backends():
    from womm.config import DEFAULT_SYSTEM_VERSION
    from womm.models.system_version import load_system_version

    cc = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT)
    with pytest.raises(BaselineRefused, match="api backend"):
        experiment_metadata(cc, CLEAN, False, 6, formal=True)
    with pytest.raises(BaselineRefused, match="api backend"):
        check_formal(fake_sv(), 6)
    # --baseline on claude_code is not formal: still allowed, still a smoke baseline
    assert experiment_metadata(cc, CLEAN, True, 6)["baseline_kind"] == "smoke"


def test_formal_run_marks_the_r34_noise_run():
    meta = experiment_metadata(_api_sv(), CLEAN, False, 6, formal=True)
    assert meta["formal"] is True and meta["run_kind"] == "r34_noise"
    with pytest.raises(BaselineRefused, match="at least 6 repetitions"):
        experiment_metadata(_api_sv(), CLEAN, False, 3, formal=True)
    with pytest.raises(BaselineRefused, match="dirty"):
        experiment_metadata(_api_sv(), CodeIdentity(git_sha="x", dirty=True), False, 6, formal=True)


async def test_formal_run_needs_one_split(second_fixture, monkeypatch):
    import womm.eval.run_eval as run_eval

    monkeypatch.setattr(run_eval, "check_formal", lambda sv, reps: None)
    val = CASE.model_copy(update={"case_id": "case_99_val", "fixture": "other", "split": "val"})
    with pytest.raises(BaselineRefused, match="one split"):
        await evaluate_cases(
            [CASE, val], sv=fake_sv(), backends={"fake": FakeBackend({})},
            decisions=StubDecisionService(), code=CLEAN, judge_prompt="p", repetitions=6,
            formal=True,
        )  # fmt: skip


def test_sync_dataset_records_the_split():
    client = _FakeClient()
    sync_dataset(client, [CASE])
    assert next(iter(client.examples.values())).metadata == {
        "case_id": CASE.case_id, "split": "train",
    }  # fmt: skip


class _FakeClient:
    def __init__(self):
        self.examples = {}
        self.created = self.updated = 0

    def has_dataset(self, dataset_name):
        return hasattr(self, "ds")

    def create_dataset(self, dataset_name, description):
        self.ds = type("D", (), {"id": "ds1"})()
        return self.ds

    def read_dataset(self, dataset_name):
        return self.ds

    def list_examples(self, dataset_id):
        return list(self.examples.values())

    def create_example(self, inputs, outputs, metadata, dataset_id):
        self.created += 1
        ex = type("E", (), {})()
        ex.id, ex.inputs, ex.outputs, ex.metadata = f"e{self.created}", inputs, outputs, metadata
        self.examples[ex.id] = ex

    def update_example(self, example_id, inputs, outputs, metadata):
        self.updated += 1
        ex = self.examples[example_id]
        ex.inputs, ex.outputs, ex.metadata = inputs, outputs, metadata


def test_sync_dataset_idempotent():
    client = _FakeClient()
    cases = load_all_golden()
    sync_dataset(client, cases)
    sync_dataset(client, cases)
    assert client.created == len(cases) and client.updated == 0
    changed = [cases[0].model_copy(update={"notes": "changed"}), *cases[1:]]
    sync_dataset(client, changed)
    assert client.updated == 1


async def test_langsmith_recording_marks_aborted_as_partial(fixture, monkeypatch):
    import langsmith

    from womm.eval.run_eval import record_langsmith_experiment

    script = _script()
    script["judge"] = [LLMError("rate_limit", "usage limit reached")]
    report = await _evaluate(fixture, script)
    seen = {}

    async def fake_aevaluate(target, **kw):
        seen.update(kw)
        seen["row"] = await target({"case_id": CASE.case_id})
        with pytest.raises(RuntimeError, match="no computed score"):
            await target({"case_id": CASE.case_id})
        seen["metrics"] = kw["evaluators"][0](seen["row"])
        return type("R", (), {"experiment_name": "exp-1"})()

    monkeypatch.setattr(langsmith, "aevaluate", fake_aevaluate)
    name = await record_langsmith_experiment(report, [CASE], _FakeClient(), prefix="t")
    assert name == "exp-1"
    assert seen["metadata"]["partial"] is True
    assert seen["metadata"]["aborted"].startswith("rate_limit")
    assert {r["key"] for r in seen["metrics"]["results"]} >= {"coverage", "grounding"}


async def test_synthesis_rate_limit_aborts(fixture):
    script = _script()
    script["synthesis"] = [LLMError("rate_limit", "usage limit reached")]
    report = await _evaluate(fixture, script)
    assert report.aborted and report.summary is None


async def test_langsmith_data_only_scored_cases(fixture, monkeypatch):
    import langsmith

    from womm.eval.run_eval import record_langsmith_experiment

    report = await _evaluate(fixture, _script())
    seen = {}

    async def fake_aevaluate(target, **kw):
        seen["cases"] = [ex.metadata["case_id"] for ex in kw["data"]]
        return type("R", (), {"experiment_name": "exp"})()

    monkeypatch.setattr(langsmith, "aevaluate", fake_aevaluate)
    await record_langsmith_experiment(report, load_all_golden(), _FakeClient(), prefix="t")
    assert seen["cases"] == [CASE.case_id]


async def test_report_json_includes_noise_and_failures(fixture):
    single = json.loads((await _evaluate(fixture, _script())).to_json())
    assert single["noise"] is None and isinstance(single["failures"], list)
    double = json.loads((await _evaluate(fixture, _script(reps=2), repetitions=2)).to_json())
    assert double["noise"][CASE.case_id]["coverage"]["n"] == 2
