"""R37 diff regression check (U8): hand-written reference answers, scored through replay,
recorded per version, shown but never gating, and never Planner input."""

import copy
from pathlib import Path

import pytest
import yaml

from womm.eval.golden import GoldenError, load_all_golden, load_golden
from womm.evolve import diff_regression as dr
from womm.evolve.archive import Archive
from womm.evolve.replay import ReplayStore

from ..graph.conftest import fake_sv
from .test_replay import CODE, script, worker

TEMPLATE = yaml.safe_load(dr.REFERENCE_PATH.read_text())


def written(tmp_path: Path, **case_changes) -> Path:
    """The template with every placeholder replaced, as a person would leave it."""
    data = copy.deepcopy(TEMPLATE)
    data.update(status="written", written_by="jdoe", written_on="2026-11-02")

    def fill(value):
        if isinstance(value, str):
            return value.replace("TODO", "hand-written")
        if isinstance(value, list):
            return [fill(v) for v in value]
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        return value

    data["case"] = fill(data["case"]) | case_changes
    path = tmp_path / "reference.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


# ----------------------------------------------------------------------------- reference answers


def test_the_committed_file_is_a_template_and_reports_not_available():
    ref = dr.load_reference()
    assert not ref.available and ref.case is None
    assert "template" in ref.reason and "by hand" in ref.reason
    assert TEMPLATE["status"] == "template" and TEMPLATE["written_by"] is None
    assert "TODO" in dr.REFERENCE_PATH.read_text()


def test_missing_file_and_leftover_placeholders_are_not_available(tmp_path):
    assert "no reference answers" in dr.load_reference(tmp_path / "missing.yaml").reason
    path = written(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["case"]["expected_impacts"][0]["impact"] = "TODO: still open"
    path.write_text(yaml.safe_dump(data))
    ref = dr.load_reference(path)
    assert not ref.available and "1 TODO placeholder" in ref.reason


def test_a_written_reference_is_available_and_validated(tmp_path):
    ref = dr.load_reference(written(tmp_path))
    assert ref.available and ref.case.split == "diff_check"
    assert ref.case.scenario_id == "demo_penalties_amended" and len(ref.case.expected_impacts) == 3
    with pytest.raises(ValueError, match="not in demo_penalties_amended"):
        bad = written(
            tmp_path,
            expected_impacts=[
                {**ref.case.expected_impacts[0].model_dump(), "provision_keys": ["ai_act/x/y"]}
            ],
        )
        dr.load_reference(bad)
    with pytest.raises(ValueError, match="defined on demo_penalties_amended"):
        dr.load_reference(written(tmp_path, scenario_id="eval_sme_impacts"))


def test_the_golden_loaders_never_return_the_diff_case(tmp_path):
    assert all(c.case_id != TEMPLATE["case"]["case_id"] for c in load_all_golden())
    path = tmp_path / "case_99_diff.yaml"
    path.write_text(yaml.safe_dump(yaml.safe_load(written(tmp_path).read_text())["case"]))
    with pytest.raises(ValueError):
        load_golden(path)  # split diff_check is not a golden split


# ----------------------------------------------------------------------------- scoring


@pytest.fixture
async def diff_setup(db, tmp_path):
    ref = dr.load_reference(written(tmp_path))
    sv = fake_sv()
    archive = Archive(db)
    await archive.archive(sv, origin="seed")
    store = ReplayStore(db, load_cases=lambda split: [], diff_check_cases=lambda: [ref.case])
    return sv, archive, store, ref


async def test_a_fake_run_scores_the_diff_case_and_records_diff_check_metrics(
    diff_setup, db, tmp_path
):
    sv, archive, store, ref = diff_setup
    batch = await store.submit(sv.version_id, dr.DIFF_CHECK_SPLIT, 2, judge_sv=sv, code=CODE)
    status = await worker(store, archive, sv, _fake(2), tmp_path).run_batch(batch)
    assert status["complete"] and status["done"] == 2
    score = await dr.diff_check_score(archive, sv.version_id)
    assert score["mean"] == 1.0 and score["n"] == 2 and score["sd"] == 0.0
    assert {m["split"] for m in await archive.metrics(sv.version_id)} == {"diff_check"}
    events, runs = await db.failure_memory(sv.version_id)
    assert events == [] and runs == [], "the diff check never feeds Failure Memory"


def _fake(items):
    from womm.llm.fake import FakeBackend

    return FakeBackend(script(items))


async def test_a_store_without_reference_cases_cannot_enqueue_the_diff_check(db):
    store = ReplayStore(db, load_cases=lambda split: [])
    with pytest.raises(GoldenError, match="no R37 diff-check cases"):
        await store.submit("sv_x", dr.DIFF_CHECK_SPLIT, 1, judge_sv=fake_sv(), code=CODE)


# ----------------------------------------------------------------------------- regression flag


def test_regression_is_more_than_one_pooled_sd_below_the_incumbent():
    inc = {"mean": 0.6, "sd": 0.1, "n": 3}
    assert dr.is_regression({"mean": 0.45, "sd": 0.1, "n": 3}, inc)
    assert not dr.is_regression({"mean": 0.55, "sd": 0.1, "n": 3}, inc)
    assert dr.is_regression({"mean": 0.59, "sd": None, "n": 1}, {"mean": 0.6, "sd": None, "n": 1})


async def test_r37_report_states(db, tmp_path):
    archive = Archive(db)
    not_available = await dr.r37_report(archive, "a", "b", dr.load_reference())
    assert not_available["status"] == "not_available" and not_available["regression"] is None
    ref = dr.load_reference(written(tmp_path))
    not_run = await dr.r37_report(archive, "sv_a", "sv_b", ref)
    assert not_run["status"] == "not_run" and "sv_a, sv_b" in not_run["reason"]

    cand, inc = fake_sv(), fake_sv(experts=["legal", "fiscal"])
    for sv, mean in ((cand, 0.2), (inc, 0.8)):
        await archive.archive(sv, origin="seed")
        row = {"level": "case", "subject": "diff_demo_penalties_amended", "metric": "coverage",
               "n": 3, "mean": mean, "sd": 0.05}  # fmt: skip
        await archive.record_metrics(sv.version_id, "diff_check", [row], batch_id="rb",
                                     judge_version="jv", git_sha="g", full_split=True)  # fmt: skip
    report = await dr.r37_report(archive, cand.version_id, inc.version_id, ref)
    assert report["status"] == "available" and report["regression"] is True
    assert report["candidate"]["mean"] == 0.2 and report["incumbent"]["mean"] == 0.8
