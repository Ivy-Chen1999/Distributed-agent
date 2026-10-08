"""The tie-break judge: rulings, the optional audit sample and the review gate's checks."""

from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from womm.data.fixtures import fixture_dir, load_fixture
from womm.eval.drafting import DraftingConfig, DraftingError, GoldenDraft, item_digest
from womm.eval.golden_review import (
    AuditResult,
    build_case,
    check_draft,
    human_reasons,
    tiebreak_problems,
)
from womm.eval.tiebreak import needs_tiebreak, tiebreak_audit_seed_for, tiebreak_draft
from womm.llm.fake import FakeBackend
from womm.models.system_version import RoleConfig

from .draft_factory import KEYS, decide, draft_dict, seal, to_draft

ROLES = ("drafter", "recall", "judge_anchor", "judge_derivability", "judge_category", "tiebreak")
IA = "widget providers would face cost number 5 under the option, said the assessment."


def config() -> DraftingConfig:
    roles = {n: RoleConfig(backend="fake", model=f"model-{n}", prompt=f"p/{n}.md") for n in ROLES}
    return DraftingConfig(
        roles=roles,
        prompts={f"p/{n}.md": f"SYSTEM PROMPT {n}" for n in ROLES},
        prompt_hashes={f"p/{n}.md": "h" for n in ROLES},
    )


def verdict(item_id, v="keep", category="compliance_cost", reason="the IA states it"):
    return {"item_id": item_id, "verdict": v, "category": category, "reason": reason}


def run(draft: GoldenDraft, verdicts: list[dict]) -> tuple[GoldenDraft, FakeBackend]:
    fake = FakeBackend({"tiebreak": [{"verdicts": verdicts}]})
    fixture = load_fixture(fixture_dir("data_act"))
    out = asyncio.run(tiebreak_draft(draft, fixture, IA, config(), {"fake": fake}))
    return out, fake


def by_id(draft: GoldenDraft) -> dict:
    return {i.item_id: i for i in draft.items()}


def test_open_items_are_the_judge_disagreement_and_the_candidates():
    draft = to_draft(draft_dict())
    assert [i.item_id for i in draft.items() if needs_tiebreak(i)] == [
        "c90_e05", "c90_m01", "c90_m02",
    ]  # fmt: skip


def test_rulings_settle_every_open_item_and_nothing_is_left_for_people():
    draft, fake = run(
        to_draft(draft_dict()),
        [
            verdict("c90_e05", category="market_competition"),
            verdict("c90_m01", "drop", reason="repeats c90_e01"),
            # no ruling for c90_m02: unsure, so dropped
        ],
    )
    items = by_id(draft)
    e05, m01, m02 = items["c90_e05"], items["c90_m01"], items["c90_m02"]
    assert e05.review.decision == "llm_kept" and e05.category == "market_competition"
    assert e05.tiebreak.original_category == "compliance_cost"
    assert e05.provenance.status == "llm_tiebroken" and e05.tiebreak.model == "model-tiebreak"
    assert m01.review.decision == "llm_dropped" and m01.tiebreak.verdict == "drop"
    assert m02.review.decision == "llm_dropped" and m02.tiebreak.verdict == "unsure"
    # The judges' audit items drafted while the audit blocked are auto-accepted again.
    assert items["c90_e06"].review.decision == "auto_accepted" and items["c90_e06"].review.audit
    assert [i.item_id for i in draft.items() if human_reasons(i)] == []
    assert check_draft(draft, set(KEYS)) == []
    tb = draft.provenance.tiebreak_audit
    assert tb.seed == tiebreak_audit_seed_for(draft.case_id)
    assert tb.eligible_ids == ["c90_e05"] and tb.sampled == ["c90_e05"]  # ceil(0.2 x 1)
    assert e05.review.audit
    assert draft.stats.llm_kept == 1 and draft.stats.llm_dropped == 2
    assert draft.stats.human_decisions_needed == 0
    prompt = fake.calls[0].user_content
    assert (
        "c90_e05" in prompt
        and "c90_m02" in prompt
        and "c90_e06" not in prompt.split("Items to rule on")[1]
    )


def test_a_failed_anchor_check_is_dropped_without_asking_the_llm():
    data = draft_dict()
    data["expected_impacts"][4]["flags"] = ["anchor_not_found"]
    draft, fake = run(to_draft(seal(data)), [verdict("c90_m01"), verdict("c90_m02", "drop")])
    e05 = by_id(draft)["c90_e05"]
    assert e05.review.decision == "llm_dropped" and e05.tiebreak.model == "deterministic"
    assert "c90_e05" not in fake.calls[0].user_content.split("Items to rule on")[1]
    assert check_draft(draft, set(KEYS)) == []


def test_holdout_and_a_second_run_are_refused():
    with pytest.raises(DraftingError, match="holdout"):
        run(to_draft(draft_dict("holdout")), [])
    once, _ = run(to_draft(draft_dict()), [verdict("c90_e05")])
    with pytest.raises(DraftingError, match="already tie-broken"):
        run(once, [])


def test_published_case_keeps_tiebreak_items_with_their_provenance():
    draft, _ = run(to_draft(draft_dict()), [verdict("c90_e05"), verdict("c90_m01")])
    case = build_case(draft, published_on=dt.date(2026, 10, 9), audit=AuditResult(0, 0, 0))
    prov = {i.expected_id: i.provenance for i in case.expected_impacts}
    assert prov["c90_e05"] == "llm_tiebroken" and prov["c90_m01"] == "llm_tiebroken"
    assert prov["c90_e01"] == "llm_judged" and "c90_m02" not in prov
    assert "tie-break judge" in case.notes and "c90_m02" in case.notes


def _tampered(draft: GoldenDraft, item_id: str, **review) -> GoldenDraft:
    data = draft.model_dump(mode="json")
    for section in ("expected_impacts", "important_omissions", "possibly_missing"):
        for item in data[section]:
            if item_id in item.values():
                item["review"].update(review)
    return GoldenDraft.model_validate(data)


def test_the_gate_catches_a_flipped_ruling_and_a_forged_llm_decision():
    draft, _ = run(to_draft(draft_dict()), [verdict("c90_e05"), verdict("c90_m01", "drop")])
    flipped = _tampered(draft, "c90_m01", decision="llm_kept")
    assert any("contradicts the tie-break ruling" in p for p in tiebreak_problems(flipped))
    forged = _tampered(to_draft(draft_dict()), "c90_m01", decision="llm_kept")
    assert any("set only by the tie-break judge" in p for p in check_draft(forged, set(KEYS)))


def test_the_gate_catches_an_edited_tiebreak_sample():
    draft, _ = run(to_draft(draft_dict()), [verdict("c90_e05"), verdict("c90_m01")])
    data = draft.model_dump(mode="json")
    data["provenance"]["tiebreak_audit"]["sampled"] = []
    problems = check_draft(GoldenDraft.model_validate(data), set(KEYS))
    assert any("tie-break audit sample does not match" in p for p in problems)


def test_a_person_may_still_decide_a_spot_check_and_it_counts():
    draft, _ = run(to_draft(draft_dict()), [verdict("c90_e05"), verdict("c90_m01")])
    data = decide(draft.model_dump(mode="json"), "c90_e05", "rejected", note="wrong actor")
    decided = GoldenDraft.model_validate(data)
    assert check_draft(decided, set(KEYS)) == []
    assert item_digest(by_id(decided)["c90_e05"]) == decided.provenance.item_digests["c90_e05"]


def test_an_escalated_proposal_sends_tiebreak_items_back_to_people():
    draft, _ = run(to_draft(draft_dict()), [verdict("c90_e05"), verdict("c90_m01")])
    problems = check_draft(draft, set(KEYS), escalated=True)
    assert any("c90_e05" in p and "not llm_kept" in p for p in problems)
