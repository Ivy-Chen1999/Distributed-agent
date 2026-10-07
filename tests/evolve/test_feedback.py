"""Analyst feedback (U11, R31): parsing marks, resolving them against train/val runs, Failure
Memory events of the human kinds, and golden candidates staged into the review gate."""

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from womm.eval.drafting import load_draft
from womm.eval.golden import GoldenError
from womm.eval.golden_review import check_draft
from womm.evolve.failure_memory import CaseRun, format_patterns, patterns
from womm.evolve.feedback import (
    FEEDBACK_KEY,
    MARKS,
    AnalystFeedback,
    FeedbackError,
    parse_feedback,
    report_runs,
    resolve_feedback,
    stage_candidates,
)

from ..eval import draft_factory as df
from .test_failure_memory import K1, K2, _finding, _run, _score

NOW = dt.datetime(2026, 10, 7, tzinfo=dt.UTC)


def _fb(value="missing_impact", comment="impact: SMEs pay twice", fid="fb1", run="t1", **kw):
    return SimpleNamespace(**{
        "id": fid, "run_id": run, "key": FEEDBACK_KEY, "value": value, "score": None,
        "comment": comment, "correction": None, "created_at": NOW,
        "feedback_source": SimpleNamespace(user_name="ana", user_id="u1"), **kw,
    })  # fmt: skip


def _case_run(split="train", run_id="r1", rep=2) -> CaseRun:
    return CaseRun(case_id="case_x", fixture="ai_act", split=split, run_id=run_id,
                   repetition=rep, system_version="sv", judge_version="jv",
                   git_sha="g")  # fmt: skip


BOARD = [_finding("legal", K1, "f1"), _finding("fiscal", K2, "f2")]


# ----------------------------------------------------------------- parsing


def test_parse_reads_the_mark_and_comment_fields():
    comment = (
        "Impact: SMEs pay twice\nprovisions: reg/a/2, reg/a/1\n"
        "category: sme_specific\nactor: SMEs\nseen in two runs"
    )
    fb = parse_feedback(_fb(comment=comment))
    assert (fb.mark, fb.impact, fb.provision_keys, fb.category, fb.affected_actor) == (
        "missing_impact",
        "SMEs pay twice",
        ["reg/a/2", "reg/a/1"],
        "sme_specific",
        "SMEs",
    )
    assert (fb.note, fb.analyst, fb.trace_run_id, fb.created_at) == (
        "seen in two runs",
        "ana",
        "t1",
        NOW,
    )


def test_parse_takes_the_categorical_score_and_a_correction_dict():
    fb = parse_feedback(_fb(value=None, score=MARKS.index("weak_evidence"), comment="finding: f0",
                            correction={"finding": "f2"}))  # fmt: skip
    assert (fb.mark, fb.finding_id) == ("weak_evidence", "f2")


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"key": "coverage"}, "is not"),
        ({"value": "maybe"}, "unknown mark"),
        ({"value": "weak_evidence", "comment": "no finding"}, "names the finding"),
        ({"value": "edit", "comment": "finding: f1"}, "edited text"),
        ({"value": "missing_impact", "comment": "something"}, "describes the impact"),
        ({"comment": "impact: x\ncategory: vibes"}, "unknown category"),
        ({"run_id": None}, "not attached to a run"),
    ],
)
def test_parse_refuses_incomplete_marks(kw, message):
    with pytest.raises(FeedbackError, match=message):
        parse_feedback(_fb(**kw))


# ----------------------------------------------------------------- resolving


def test_missing_impact_on_a_train_run_becomes_one_human_event():
    """Plan U11 happy path: one row with source human, owner from the experts citing the keys."""
    fb = parse_feedback(_fb(comment="impact: SMEs pay twice\nprovisions: reg/a/3"))
    rec = resolve_feedback(fb, _case_run(), _run(board=BOARD))
    e = rec.event
    assert (e.kind, e.source, e.split, e.item_id, e.owner, e.category, e.repetition) == (
        "analyst_missing_impact",
        "human",
        "train",
        "human:reg/a/3",
        "none",
        "uncategorised",
        2,
    )
    assert e.detail["feedback_id"] == "fb1" and e.judge_version == "jv"
    assert rec.golden_candidate == "queued"


def test_missing_impact_owner_is_the_expert_citing_its_keys():
    fb = parse_feedback(_fb(comment="impact: x\nprovisions: reg/a/1\ncategory: sme_specific"))
    e = resolve_feedback(fb, _case_run(), _run(board=BOARD)).event
    assert (e.owner, e.touching_agents, e.category) == ("legal", ["legal"], "sme_specific")


def test_weak_evidence_is_owned_by_the_finding_agent():
    fb = parse_feedback(_fb(value="weak_evidence", comment="finding: f2\nthin quote"))
    rec = resolve_feedback(fb, _case_run(split="val"), _run(board=BOARD))
    e = rec.event
    assert (e.kind, e.item_id, e.owner, e.split) == (
        "analyst_weak_evidence",
        f"fiscal:{K2}",
        "fiscal",
        "val",
    )
    assert e.detail["note"] == "thin quote" and rec.golden_candidate is None


@pytest.mark.parametrize("mark", ["accept", "reject"])
def test_accept_and_reject_are_recorded_without_an_event(mark):
    rec = resolve_feedback(parse_feedback(_fb(value=mark, comment="finding: f1")), _case_run(),
                           _run(board=BOARD))  # fmt: skip
    assert rec.event is None and rec.golden_candidate is None and rec.feedback.mark == mark


def test_a_mark_on_an_unknown_finding_is_refused():
    fb = parse_feedback(_fb(value="edit", comment="finding: f9\nedited: better"))
    with pytest.raises(FeedbackError, match="no finding 'f9'"):
        resolve_feedback(fb, _case_run(), _run(board=BOARD))


def test_feedback_on_a_sealed_run_is_refused():
    """Plan U11 error path."""
    fb = parse_feedback(_fb())
    sealed = _case_run().model_copy(update={"split": "holdout"})
    with pytest.raises(GoldenError, match="train/val runs only"):
        resolve_feedback(fb, sealed, _run())


def _report(tmp_path, split="train", sv="sv", name="eval_1_sv.json"):
    run = _case_run(split=split).model_dump(mode="json")
    scores = [
        {"run_id": "r1", "graph_run_id": "t1"},
        {"run_id": "r_unscored", "graph_run_id": "t2"},
    ]
    data = {"system_version": sv, "metadata": {"split": split, "splits": [split]},
            "case_runs": [run], "scores": scores}  # fmt: skip
    (tmp_path / name).write_text(json.dumps(data))


def test_report_runs_index_scored_runs_by_trace_id(tmp_path):
    _report(tmp_path)
    _report(tmp_path, sv="other", name="eval_2_other.json")
    assert {k: v.run_id for k, v in report_runs(tmp_path, "sv").items()} == {"t1": "r1"}


def test_a_report_naming_a_holdout_split_is_refused(tmp_path):
    _report(tmp_path)
    data = json.loads((tmp_path / "eval_1_sv.json").read_text())
    data["metadata"]["splits"] = ["holdout", "train"]
    (tmp_path / "eval_1_sv.json").write_text(json.dumps(data))
    with pytest.raises(GoldenError, match="holdout"):
        report_runs(tmp_path, "sv")


def test_analyst_rows_appear_in_the_patterns_view_marked_as_human():
    """Plan U11 verification."""
    fb = parse_feedback(_fb(comment="impact: x\nprovisions: reg/a/3"))
    rec = resolve_feedback(fb, _case_run(), _run())
    (row,) = patterns([rec.event], [_case_run()])
    assert (row["kind"], row["source"], row["owner"]) == ("analyst_missing_impact", "human", "none")
    assert "human" in format_patterns([row])


# ----------------------------------------------------------------- golden candidates


def _row(fid="fb1", case_id="case_90_widget_switching", fixture="data_act", **payload):
    return {"feedback_id": fid, "case_id": case_id, "fixture": fixture, "run_id": "r1",
            "system_version": "sv", "analyst": "ana", "note": "seen twice",
            "payload": {"impact": "Widget buyers pay twice", "affected_actor": "Buyers",
                        "provision_keys": [df.KEYS[0]], **payload}}  # fmt: skip


def _drafts(tmp_path, split="train"):
    path = tmp_path / "drafts" / "case_90_widget_switching.yaml"
    df.write(path, df.fully_decided(split))
    return path


def test_a_staged_candidate_is_pending_and_blocks_publication(tmp_path):
    path = _drafts(tmp_path)
    assert check_draft(load_draft(path), set(df.KEYS)) == []  # publishable before staging
    staged, problems = stage_candidates([_row()], path.parent, registry=tmp_path / "none.yaml")
    assert staged == {"fb1": path} and problems == []
    draft = load_draft(path)
    cand = draft.possibly_missing[-1]
    assert (cand.candidate_id, cand.impact, cand.provenance.origin, cand.review.decision) == (
        "c90_fb01",
        "Widget buyers pay twice",
        "human_added",
        "pending",
    )
    assert draft.stats.possibly_missing == 3 and "fb1" in cand.flags[0]
    # Never auto-added: the strict gate refuses the draft until a reviewer decides the item.
    assert any("c90_fb01: pending" in p for p in check_draft(draft, set(df.KEYS)))
    assert check_draft(draft, set(df.KEYS), allow_pending=True) == []
    decided = df.decide(draft.model_dump(mode="json"), "c90_fb01", "rejected", note="dup")
    assert check_draft(df.to_draft(decided), set(df.KEYS)) == []


def test_restaging_is_idempotent_and_a_case_without_a_draft_stays_queued(tmp_path):
    path = _drafts(tmp_path)
    rows = [_row(), _row("fb2", case_id="case_02_sme_impacts", fixture="ai_act")]
    reg = tmp_path / "none.yaml"
    assert stage_candidates(rows, path.parent, registry=reg)[0] == {"fb1": path}
    assert stage_candidates(rows, path.parent, registry=reg)[0] == {"fb1": path}
    assert len(load_draft(path).possibly_missing) == 3


def test_staging_refuses_holdout_drafts_and_holdout_fixtures(tmp_path):
    path = _drafts(tmp_path, split="holdout")
    staged, problems = stage_candidates([_row()], path.parent, registry=tmp_path / "none.yaml")
    assert staged == {} and "train/val runs only" in problems[0]
    path = _drafts(tmp_path)
    registry = tmp_path / "holdout_scenarios.yaml"
    registry.write_text("data_act: {}\n")
    staged, problems = stage_candidates([_row()], path.parent, registry=registry)
    assert staged == {} and "holdout proposal" in problems[0]
    assert len(load_draft(path).possibly_missing) == 2


def test_analyst_feedback_model_requires_created_at():
    with pytest.raises(ValueError):
        AnalystFeedback(feedback_id="x", trace_run_id="t", mark="accept", finding_id="f")


def test_analyst_rows_never_trigger_a_new_expert():
    """Hand-entered marks cannot force the topology stage (runbook honesty rule): the trigger
    takes the judge's missed_impact kind only, even for a persistent unowned human pattern."""
    from womm.evolve.cycle import unowned_patterns

    runs, events = [], []
    for fixture in ("ai_act", "data_act"):
        for n in (1, 2):
            run = _case_run(run_id=f"{fixture}{n}", rep=n).model_copy(
                update={"fixture": fixture, "case_id": f"case_{fixture}"}
            )
            fb = parse_feedback(_fb(fid=f"fb-{fixture}{n}", comment="impact: x\nprovisions: k"))
            events.append(resolve_feedback(fb, run, _run(run.run_id)).event)
            runs.append(run)
    (row,) = patterns(events, runs)
    assert (row["owner"], row["persistent_misses"], len(row["proposals"])) == ("none", 2, 2)
    assert unowned_patterns([row]) == []


# ----------------------------------------------------------------- analyst candidates: train only,
# reviewed by someone else, anchored, and never a topology trigger

ANCHOR = "widget buyers would pay twice for the same switching service under the option"
IA_TEXT = f"6.2.3. Intervention on widget services. {ANCHOR}. Other text follows here."
FILLED = {"ia_section": "6.2.3. Intervention on widget services", "ia_anchor": ANCHOR,
          "affected_actor": "Widget buyers", "mechanism": "Double charging on switching",
          "category": "consumers_users"}  # fmt: skip


def _staged(tmp_path):
    path = _drafts(tmp_path)
    stage_candidates([_row()], path.parent, registry=tmp_path / "none.yaml")
    return load_draft(path).model_dump(mode="json")


def test_a_missing_impact_on_a_val_run_is_never_a_golden_candidate():
    """Val is for selection: an analyst must not be able to change what val scores."""
    fb = parse_feedback(_fb(comment="impact: x\nprovisions: reg/a/3"))
    rec = resolve_feedback(fb, _case_run(split="val"), _run())
    assert rec.event.kind == "analyst_missing_impact" and rec.golden_candidate is None


def test_staging_refuses_val_drafts(tmp_path):
    path = _drafts(tmp_path, split="val")
    staged, problems = stage_candidates([_row()], path.parent, registry=tmp_path / "none.yaml")
    assert staged == {} and "train drafts only" in problems[0]
    assert len(load_draft(path).possibly_missing) == 2


def test_a_staged_candidate_records_the_analyst(tmp_path):
    cand = df.to_draft(_staged(tmp_path)).possibly_missing[-1]
    assert cand.provenance.raised_by == "ana"


@pytest.mark.parametrize(
    ("decision", "edit", "reviewer", "message"),
    [
        ("verified", FILLED, "octo-cat", "only as 'edited'"),
        ("edited", FILLED | {"ia_anchor": ""}, "octo-cat", "ia_anchor is empty"),
        ("edited", FILLED | {"ia_section": " "}, "octo-cat", "ia_section is empty"),
        ("edited", FILLED | {"mechanism": ""}, "octo-cat", "mechanism is empty"),
        ("edited", FILLED | {"affected_actor": ""}, "octo-cat", "affected_actor is empty"),
        ("edited", FILLED, "Ana", "raised it"),
    ],
)
def test_a_human_added_candidate_needs_an_edited_anchored_decision_by_another_person(
    tmp_path, decision, edit, reviewer, message
):
    data = df.decide(_staged(tmp_path), "c90_fb01", decision, reviewer=reviewer, **edit)
    problems = check_draft(df.to_draft(data), set(df.KEYS))
    assert any("c90_fb01" in p and message in p for p in problems), problems


def test_a_human_added_candidate_without_an_analyst_is_refused(tmp_path):
    data = df.decide(_staged(tmp_path), "c90_fb01", "edited", **FILLED)
    data["possibly_missing"][-1]["provenance"]["raised_by"] = None
    problems = check_draft(df.to_draft(data), set(df.KEYS))
    assert any("names nobody who raised it" in p for p in problems), problems


def test_the_anchor_is_checked_against_the_cached_ia_when_available(tmp_path):
    from womm.eval.golden_review import unchecked_human_anchors

    data = df.decide(_staged(tmp_path), "c90_fb01", "edited", **FILLED)
    draft = df.to_draft(data)
    assert check_draft(draft, set(df.KEYS)) == []
    # Without the IA here, the anchor is not blocking but flagged for the reviewer.
    assert [n for n in unchecked_human_anchors(draft, None) if "c90_fb01" in n]
    assert check_draft(draft, set(df.KEYS), ia_text=IA_TEXT) == []
    assert unchecked_human_anchors(draft, IA_TEXT) == []
    other = "an unrelated sentence that the analyst invented for this candidate item"
    problems = check_draft(draft, set(df.KEYS), ia_text=IA_TEXT.replace(ANCHOR, other))
    assert any("c90_fb01" in p and "anchor" in p for p in problems), problems


def test_analyst_candidates_end_to_end_never_drive_the_topology_trigger(tmp_path):
    """A missing impact staged, edited and published becomes an origin-human expected impact;
    persistent unowned misses on it across two proposals never satisfy the new-expert trigger,
    while the same misses on a tool-drafted impact do."""
    from womm.eval.golden_review import AuditResult, build_case
    from womm.evolve.cycle import trigger_events, unowned_patterns
    from womm.evolve.failure_memory import failure_events

    data = df.decide(_staged(tmp_path), "c90_fb01", "edited", **FILLED)
    draft = df.to_draft(data)
    assert check_draft(draft, set(df.KEYS), ia_text=IA_TEXT) == []
    case = build_case(draft, published_on=NOW.date(), audit=AuditResult(0, 0, 0))
    origins = {i.expected_id: i.origin for i in case.expected_impacts}
    assert origins["c90_fb01"] == "human" and origins["c90_e01"] is None
    assert "origin" not in case.expected_impacts[0].model_dump(exclude_none=True)

    events, runs = [], []
    for fixture in ("data_act", "other_act"):
        other = case.model_copy(update={"fixture": fixture, "case_id": f"case_91_{fixture}"})
        for rep in (1, 2):
            run = _run(f"{fixture}{rep}")
            score = _score(other, missed=("c90_fb01", "c90_e01"), run_id=run.run_id)
            events += failure_events(other, score, run, split="train", repetition=rep,
                                     system_version="sv")  # fmt: skip
            runs.append(_case_run(run_id=run.run_id, rep=rep).model_copy(
                update={"fixture": fixture, "case_id": other.case_id}))  # fmt: skip
    human = [e for e in events if e.item_id == "c90_fb01"]
    assert human and all(e.detail.get("golden_origin") == "human" for e in human)
    cases = {f"case_91_{f}": case.model_copy(update={"fixture": f, "case_id": f"case_91_{f}"})
             for f in ("data_act", "other_act")}  # fmt: skip
    kept = trigger_events(events, cases)
    assert {e.item_id for e in kept} == {"c90_e01"}
    only_human = trigger_events(human, cases)
    assert only_human == [] and unowned_patterns(patterns(only_human, runs)) == []
    # Control: the same misses on a tool-drafted impact do trigger.
    assert unowned_patterns(patterns(kept, runs))
