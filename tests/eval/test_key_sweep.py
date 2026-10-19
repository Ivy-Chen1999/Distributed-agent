"""The fidelity sweep's overlay on a draft (plan 2026-10-16-001): a trim only deletes words from
the fields the page judges, a drop leaves the item out, and the person's decision, the tie-break
and the audit stay as they were."""

import datetime as dt

import pytest

from womm.eval import golden_review as gr

from .draft_factory import fully_decided, to_draft

ON = dt.date(2026, 10, 16)


def _swept(data: dict, item_id: str, action: str, before: dict, **after) -> dict:
    for section in ("expected_impacts", "important_omissions", "possibly_missing"):
        for item in data[section]:
            if (
                item.get("expected_id") or item.get("omission_id") or item.get("candidate_id")
            ) == item_id:
                item.update(after)
                item["sweep"] = {"action": action, "before": before, "reason": "adds a cause",
                                 "model": "claude-sonnet-5", "on": ON.isoformat()}  # fmt: skip
                return data
    raise KeyError(item_id)


def _problems(data: dict) -> list[str]:
    return gr.check_draft(to_draft(data), None)


def test_a_trim_on_a_judge_accepted_item_passes_and_publishes_the_trimmed_text():
    data = fully_decided()
    assert _problems(data) == []
    data = _swept(data, "c90_e01", "trim", {"impact": "Impact 1"}, impact="Impact")
    assert _problems(data) == []
    draft = to_draft(data)
    item = next(i for i in draft.expected_impacts if i.item_id == "c90_e01")
    assert item.review.decision == "auto_accepted"  # the earlier decision stays on record
    case = gr.build_case(draft, published_on=ON, audit=gr.audit_result([draft]))
    assert next(i for i in case.expected_impacts if i.expected_id == "c90_e01").impact == "Impact"
    assert "Corrected after a failed reference check (human feedback)" in case.notes
    assert "trimmed ['c90_e01']" in case.notes and "not re-checked by people" in case.notes


def test_a_drop_leaves_the_item_out_and_keeps_the_decision():
    data = _swept(fully_decided(), "c90_e02", "drop", {})
    draft = to_draft(data)
    assert _problems(data) == []
    assert "c90_e02" not in {i.item_id for i in gr.kept_impacts(draft)}
    case = gr.build_case(draft, published_on=ON, audit=gr.audit_result([draft]))
    assert "c90_e02" not in {i.expected_id for i in case.expected_impacts}
    assert "dropped ['c90_e02']" in case.notes


def test_a_dropped_omission_is_not_published():
    data = _swept(fully_decided(), "c90_o01", "drop", {})
    draft = to_draft(data)
    case = gr.build_case(draft, published_on=ON, audit=gr.audit_result([draft]))
    assert "c90_o01" not in {o.omission_id for o in case.important_omissions}


@pytest.mark.parametrize(
    ("before", "after", "says"),
    [
        ({"impact": "Impact 1"}, {"impact": "Impact 1 and lower prices"}, "not a deletion"),
        ({"impact": "Impact 1"}, {"impact": "Impact 1"}, "not a deletion"),
        ({"impact": "Impact 1"}, {"impact": ""}, "not a deletion"),
        ({"mechanism": "Mechanism 1"}, {"mechanism": "Mechanism"}, "may not edit 'mechanism'"),
        ({}, {}, "names no field"),
    ],
)
def test_a_trim_that_is_not_a_pure_deletion_fails(before, after, says):
    data = _swept(fully_decided(), "c90_e01", "trim", before, **after)
    assert any(says in p for p in _problems(data))


def test_an_edit_without_the_overlay_still_fails_as_before():
    data = fully_decided()
    data["expected_impacts"][0]["impact"] = "Impact"
    assert any("tool-written fields changed" in p for p in _problems(data))


def test_a_drop_with_text_and_a_sweep_on_a_dropped_item_fail():
    data = _swept(fully_decided(), "c90_e01", "drop", {"impact": "Impact 1"})
    assert any("changes no text" in p for p in _problems(data))
    data = _swept(fully_decided(), "c90_m02", "drop", {})  # rejected by the reviewer
    assert any("not kept" in p for p in _problems(data))


def test_is_deletion_ignores_punctuation_and_case():
    before = "Stronger incentives to innovate, bring new products to market and offer lower prices."
    assert gr.is_deletion("Stronger incentives to innovate, bring new products to market.", before)
    assert not gr.is_deletion("Stronger incentives to offer products.", "Stronger incentives.")


# ----------------------------------------------------------------------------- sweep, trim, apply

import asyncio  # noqa: E402
import json  # noqa: E402

from womm.eval import fidelity  # noqa: E402
from womm.eval import key_sweep as ks  # noqa: E402
from womm.eval.drafting import load_draft, write_draft  # noqa: E402
from womm.llm.fake import FakeBackend  # noqa: E402

from .test_tiebreak import config  # noqa: E402

RULINGS = {"sample": ks.SAMPLE, "kind": "reference_check", "ai_right": 20, "ai_wrong": 0,
           "unclear": 4, "open": 0, "key_right": 7, "key_wrong": 12, "key_unclear": 2,
           "key_open": 0}  # fmt: skip


def _verdicts(keys, drop=()):
    return {"verdicts": [{"item_id": k, "verdict": "drop" if k in drop else "keep",
                          "reason": "adds lower prices" if k in drop else "stated"}
                         for k in keys]}  # fmt: skip


def _setup(tmp_path):
    path = write_draft(to_draft(fully_decided()), tmp_path / "drafts")
    keys = [s.key for s in ks.swept_items(path, load_draft(path))]
    return path, keys


def _run(tmp_path, trims, recheck_keep=True):
    path, keys = _setup(tmp_path)
    e01, e02 = "case_90_widget_switching/c90_e01", "case_90_widget_switching/c90_e02"
    fake = FakeBackend({
        fidelity.ROLE: [_verdicts(keys, drop={e01, e02}),
                        _verdicts([e01], drop=() if recheck_keep else {e01})],
        ks.TRIM_ROLE: [{"verdicts": trims}],
    })  # fmt: skip
    out = tmp_path / "out"
    counts = asyncio.run(ks.sweep([path], out, config(), {"fake": fake}, ia_root=tmp_path))
    res = asyncio.run(ks.trim(out, config(), {"fake": fake}, ia_root=tmp_path))
    return path, out, counts, res, fake


TRIMS = [
    {"item_id": "case_90_widget_switching/c90_e01", "action": "trim",
     "fields": {"impact": "Impact"}},
    {"item_id": "case_90_widget_switching/c90_e02", "action": "trim",
     "fields": {"impact": "Impact 2 and lower prices"}},  # adds words: becomes a drop
]  # fmt: skip


def test_sweep_trim_apply_round_trip(tmp_path):
    path, out, counts, res, fake = _run(tmp_path, TRIMS)
    assert counts == {"swept": 8, "keep": 6, "drop": 2}
    assert res == {"flagged": 2, "trim": 1, "drop": 1, "under_minimum": []}
    sent = fake.calls[0].user_content
    for leak in ("auto_accepted", "verified", "tie-break", "judges:"):
        assert leak not in sent
    before = path.read_text()
    edits = json.loads((out / ks.EDITS_FILE).read_text())["edits"]
    assert [e["action"] for e in edits] == ["trim", "drop"]
    assert path.read_text() == before  # nothing written before apply
    record = ks.apply(out, rejected=set(), rulings=RULINGS, wrong_keeps=0, ruled_by="assistant",
                      on=ON)  # fmt: skip
    draft = load_draft(path)
    items = {i.item_id: i for i in draft.items()}
    assert items["c90_e01"].impact == "Impact" and items["c90_e01"].sweep.before == {
        "impact": "Impact 1"
    }
    assert items["c90_e02"].sweep.action == "drop" and items["c90_e02"].impact == "Impact 2"
    assert items["c90_e01"].review.decision == "auto_accepted"
    assert gr.check_draft(draft, None) == []
    assert record["trimmed"] == 1 and record["dropped"] == 1
    assert record["drafts"] == {str(path): ks.digest(path)}
    assert "Impact" not in json.dumps(record)  # counts only, no item text
    assert path.read_text().count("raised_by") == before.count("raised_by")  # layout kept


def test_a_rejected_proposal_leaves_its_item_alone(tmp_path):
    path, out, *_ = _run(tmp_path, TRIMS)
    ks.apply(out, rejected={"case_90_widget_switching/c90_e02"}, rulings=RULINGS, wrong_keeps=0,
             ruled_by="assistant", on=ON)  # fmt: skip
    items = {i.item_id: i for i in load_draft(path).items()}
    assert items["c90_e02"].sweep is None and items["c90_e01"].sweep is not None


def test_a_cut_the_checker_still_rejects_becomes_a_drop(tmp_path):
    *_, res, _ = _run(tmp_path, TRIMS, recheck_keep=False)
    assert res["trim"] == 0 and res["drop"] == 2


@pytest.mark.parametrize(
    ("over", "wrong", "says"),
    [
        ({"ai_wrong": 4}, 0, "fix the fidelity prompt"),
        ({"open": 2}, 0, "open items remain"),
        ({"sample": "2026-11-01-other"}, 0, "not 2026-10-14"),
        ({}, 2, "wrong keeps"),
    ],
)
def test_apply_refuses_outside_the_rules(tmp_path, over, wrong, says):
    path, out, *_ = _run(tmp_path, TRIMS)
    before = path.read_text()
    with pytest.raises(ks.SweepError, match=says):
        ks.apply(out, rejected=set(), rulings=RULINGS | over, wrong_keeps=wrong,
                 ruled_by="assistant", on=ON)  # fmt: skip
    assert path.read_text() == before and not (out / ks.RECORD_FILE).exists()


def test_holdout_drafts_need_the_holdout_switch(tmp_path, monkeypatch):
    from womm.eval import drafting

    monkeypatch.setattr(drafting, "CACHE_DIR", tmp_path)
    data = fully_decided("holdout")
    path = write_draft(to_draft(data), tmp_path / "drafts")
    with pytest.raises(ks.SweepError, match="--holdout"):
        asyncio.run(ks.sweep([path], tmp_path / "o", config(), {}, ia_root=tmp_path))
    path2, _ = _setup(tmp_path / "tv")
    with pytest.raises(ks.SweepError, match="--holdout"):
        asyncio.run(ks.sweep([path2], tmp_path / "o", config(), {}, holdout=True, ia_root=tmp_path))


def test_manual_corrections_write_overlays_or_nothing(tmp_path):
    path, _ = _setup(tmp_path)
    dg = ks.digest(path)
    good = [{"path": str(path), "draft_digest": dg, "item_id": "c90_e01", "action": "trim",
             "after": {"impact": "Impact"}, "reason": "full report: no 1"},
            {"path": str(path), "draft_digest": dg, "item_id": "c90_e02", "action": "drop",
             "reason": "r"}]  # fmt: skip
    bad = good + [{"path": str(path), "draft_digest": dg, "item_id": "c90_e03", "action": "trim",
                   "after": {"impact": "Impact 3 more"}, "reason": "r"}]  # fmt: skip
    before = path.read_text()
    with pytest.raises(ks.SweepError, match="not a deletion"):
        ks.apply_corrections(bad, ruled_by="assistant ruling", on=ON)
    assert path.read_text() == before
    written = ks.apply_corrections(good, ruled_by="assistant ruling", on=ON)
    items = {i.item_id: i for i in load_draft(path).items()}
    assert items["c90_e01"].sweep.model == "assistant ruling"
    assert items["c90_e01"].impact == "Impact"
    assert items["c90_e02"].sweep.action == "drop"
    assert written == {str(path.resolve()): ks.digest(path)}


# ------------------------------------------------------------ binding and batch writes (review)


def test_a_ruling_on_an_older_draft_is_refused(tmp_path):
    path, _ = _setup(tmp_path)
    stale = [{"path": str(path), "draft_digest": ks.digest(path), "item_id": "c90_e02",
              "action": "drop", "reason": "r"}]  # fmt: skip
    path.write_text(path.read_text().replace("Impact 2", "Impact 2 fixed by hand"))
    before = path.read_text()
    with pytest.raises(ks.SweepError, match="changed since the rulings"):
        ks.apply_corrections(stale, ruled_by="a", on=ON)
    assert path.read_text() == before


def test_trim_and_apply_refuse_a_draft_changed_after_the_sweep(tmp_path):
    path, out, *_ = _run(tmp_path, TRIMS)
    path.write_text(path.read_text().replace("Impact 3", "Impact 3 fixed by hand"))
    before = path.read_text()
    with pytest.raises(ks.SweepError, match="changed since the sweep"):
        ks.apply(out, rejected=set(), rulings=RULINGS, wrong_keeps=0, ruled_by="a", on=ON)
    with pytest.raises(ks.SweepError, match="changed since the sweep"):
        asyncio.run(ks.trim(out, config(), {}, ia_root=tmp_path))
    assert path.read_text() == before and not (out / ks.RECORD_FILE).exists()


def test_proposals_from_another_batch_are_refused(tmp_path):
    path, out, *_ = _run(tmp_path, TRIMS)
    edits = json.loads((out / ks.EDITS_FILE).read_text())
    edits["sweep_batch"] = "0000000000000000"
    (out / ks.EDITS_FILE).write_text(json.dumps(edits))
    with pytest.raises(ks.SweepError, match="another sweep batch"):
        ks.apply(out, rejected=set(), rulings=RULINGS, wrong_keeps=0, ruled_by="a", on=ON)


def test_the_record_names_every_swept_draft_even_one_left_unchanged(tmp_path):
    path, out, *_ = _run(tmp_path, TRIMS)
    rejected = {"case_90_widget_switching/c90_e01", "case_90_widget_switching/c90_e02"}
    before = path.read_text()
    record = ks.apply(out, rejected=rejected, rulings=RULINGS, wrong_keeps=0, ruled_by="a", on=ON)
    assert path.read_text() == before  # nothing accepted, nothing written
    assert record["drafts"] == {str(path.resolve()): ks.digest(path)}


def test_a_failure_in_a_later_draft_writes_no_earlier_one(tmp_path):
    first, _ = _setup(tmp_path / "a")
    second, _ = _setup(tmp_path / "b")
    ok = {"path": str(first), "draft_digest": ks.digest(first), "item_id": "c90_e01",
          "action": "trim", "after": {"impact": "Impact"}, "reason": "r"}  # fmt: skip
    broken = {"path": str(second), "draft_digest": ks.digest(second), "item_id": "c90_e01",
              "action": "trim", "after": {"impact": "Impact 1 and more"},
              "reason": "r"}  # fmt: skip
    a, b = first.read_text(), second.read_text()
    with pytest.raises(ks.SweepError, match="nothing written"):
        ks.apply_corrections([ok, broken], ruled_by="a", on=ON)
    assert first.read_text() == a and second.read_text() == b


def test_a_hedge_only_restores_modality():
    before = "Recalls work better and fewer unsafe products stay in consumers' hands."
    after = ("Recalls are expected to work better, and fewer unsafe products are expected to "
             "stay in consumers' hands.")  # fmt: skip
    assert gr.is_hedge(after, before)
    assert not gr.is_hedge("Recalls are expected to work much better.", "Recalls work better.")
    assert not gr.is_hedge("Recalls work.", "Recalls work better.")
    assert not gr.is_hedge(before, before)


def test_a_hedge_overlay_passes_and_a_reworded_one_fails():
    data = _swept(fully_decided(), "c90_e01", "hedge", {"impact": "Impact 1"},
                  impact="Impact may be 1")  # fmt: skip
    assert _problems(data) == []
    data = _swept(fully_decided(), "c90_e01", "hedge", {"impact": "Impact 1"},
                  impact="Impact 1 doubles")  # fmt: skip
    assert any("not a hedge of" in p for p in _problems(data))


def test_a_rewrite_needs_its_sources_and_may_restate_the_mechanism():
    data = _swept(fully_decided(), "c90_e01", "rewrite",
                  {"mechanism": "Mechanism 1", "impact": "Impact 1"},
                  mechanism="Reworded mechanism", impact="Reworded impact")  # fmt: skip
    data["expected_impacts"][0]["sweep"]["sources"] = ["ia_full.txt:1051"]
    assert _problems(data) == []
    case = gr.build_case(to_draft(data), published_on=ON, audit=gr.audit_result([to_draft(data)]))
    assert "rewritten ['c90_e01']" in case.notes
    data["expected_impacts"][0]["sweep"]["sources"] = []
    assert any("needs the text it replaced and its sources" in p for p in _problems(data))
