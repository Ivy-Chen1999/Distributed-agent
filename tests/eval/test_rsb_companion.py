"""RSB companion drafts (plan 2026-10-12-001, U3): the RSB opinion's omissions are drafted next
to a case without changing it, and checked and published with it."""

import datetime as dt
import re

import pytest
from pydantic import ValidationError

from womm.eval.drafting import (
    DraftingError,
    audit_seed_for,
    draft_rsb_companion,
    load_draft,
    write_draft,
)
from womm.eval.golden_review import (
    ReviewError,
    audit_result,
    build_case,
    check_draft,
    integrity_problems,
)
from womm.llm.fake import FakeBackend

from .draft_factory import draft_dict, to_draft
from .test_drafting import (
    CASE,
    KEYS,
    all_agree,
    config,
    data_act,  # noqa: F401  (fixture)
    impact,
    inputs,
    omission,
)

OPINION = (
    "The Board finds that the report does not show how the switching deadlines affect small "
    "widget resellers, who would lose their margin on bundled exit services."
)
Q1 = "small widget resellers, who would lose their margin on bundled exit services"


def companion_inputs(fixture):
    return inputs(fixture).model_copy(
        update={"rsb_text": OPINION, "rsb_status": "opinion found in Cellar (pi_com PDF)"}
    )


def script(omissions, judges=all_agree):
    return {
        "drafter": [{"expected_impacts": [impact(Q1)], "important_omissions": omissions}],
        "judge_anchor": [judges],
        "judge_derivability": [judges],
        "judge_category": [judges],
    }


async def draft_companion(fixture, omissions, case=None, judges=all_agree):
    case = case or to_draft(draft_dict("train"))
    fake = FakeBackend(script(omissions, judges))
    comp = await draft_rsb_companion(case, companion_inputs(fixture), config(), {"fake": fake})
    return case, comp, fake


async def test_companion_keeps_rsb_omissions_numbered_after_the_case(data_act, tmp_path):  # noqa: F811
    case = to_draft(draft_dict("train"))
    before = write_draft(case, tmp_path / "case").read_bytes()
    _, comp, fake = await draft_companion(
        data_act, [omission(Q1, "rsb"), omission("Widget providers with proprietary", "ia")], case
    )
    last = max(int(o.omission_id.rsplit("_o", 1)[1]) for o in case.important_omissions)
    assert comp.companion == "rsb" and comp.case_id == CASE and comp.expected_impacts == []
    assert [o.omission_id for o in comp.important_omissions] == [f"c90_o{last + 1:02d}"]
    o = comp.important_omissions[0]
    assert o.source == "rsb" and o.anchor.status == "verified" and o.anchor.against == "rsb"
    assert o.review.decision == "auto_accepted"
    # The case is untouched, and written exactly as before (no companion key).
    assert write_draft(case, tmp_path / "case").read_bytes() == before
    assert b"companion" not in before
    # The drafter saw the case's points as already in the key.
    message = fake.calls[0].user_content
    assert "Already in the key" in message and "Omission 1" in message
    path = write_draft(comp, tmp_path)
    assert path.name == f"{CASE}.rsb.yaml" and load_draft(path) == comp


async def test_companion_samples_and_checks_are_its_own(data_act):  # noqa: F811
    _, comp, _ = await draft_companion(data_act, [omission(Q1, "rsb")])
    assert comp.seed_id == f"{CASE}#rsb"
    assert comp.provenance.audit.seed == audit_seed_for(f"{CASE}#rsb")
    assert integrity_problems(comp) == []
    # No expected impacts is fine for a companion: it holds omissions only.
    problems = check_draft(comp, set(KEYS), reviewer_pattern=None)
    assert not any("expected impacts would be kept" in p for p in problems)


async def test_an_anchor_outside_the_opinion_is_flagged_for_the_tie_break(data_act):  # noqa: F811
    _, comp, _ = await draft_companion(data_act, [omission("words nowhere in the opinion", "rsb")])
    o = comp.important_omissions[0]
    assert o.anchor.status != "verified" and o.flags and o.review.decision == "pending"


async def test_no_opinion_or_a_companion_as_case_is_refused(data_act):  # noqa: F811
    case = to_draft(draft_dict("train"))
    empty = companion_inputs(data_act).model_copy(update={"rsb_text": "  "})
    with pytest.raises(DraftingError, match="no RSB opinion"):
        await draft_rsb_companion(case, empty, config(), {"fake": FakeBackend({})})
    _, comp, _ = await draft_companion(data_act, [omission(Q1, "rsb")])
    with pytest.raises(DraftingError, match="drafted from its case draft"):
        await draft_rsb_companion(comp, companion_inputs(data_act), config(), {})


def test_a_companion_holds_rsb_omissions_only():
    data = draft_dict("train") | {"companion": "rsb", "expected_impacts": []}
    with pytest.raises(ValidationError, match="important omissions only"):
        to_draft(data)  # the factory's draft has possibly-missing candidates
    with pytest.raises(ValidationError, match="RSB-sourced omissions only"):
        to_draft(data | {"possibly_missing": []})  # its omissions are IA-sourced
    with pytest.raises(ValidationError, match="pattern"):
        to_draft(draft_dict("train") | {"companion": "other"})


async def test_publishing_merges_the_companion_omissions(data_act):  # noqa: F811
    case, comp, _ = await draft_companion(data_act, [omission(Q1, "rsb")])
    today = dt.date(2026, 10, 20)
    published = build_case(case, published_on=today, audit=audit_result([case]),
                           companions=[comp])  # fmt: skip
    alone = build_case(case, published_on=today, audit=audit_result([case]))
    added = [o for o in published.important_omissions if o not in alone.important_omissions]
    assert [o.omission_id for o in added] == [comp.important_omissions[0].omission_id]
    assert added[0].source.startswith("RSB opinion (companion), ")
    assert added[0].provenance == "llm_judged"


async def test_a_foreign_or_clashing_companion_is_refused(data_act):  # noqa: F811
    case, comp, _ = await draft_companion(data_act, [omission(Q1, "rsb")])
    today = dt.date(2026, 10, 20)
    other = comp.model_copy(update={"case_id": "case_91_other"})
    with pytest.raises(ReviewError, match="not a companion of"):
        build_case(case, published_on=today, audit=audit_result([case]), companions=[other])
    with pytest.raises(ReviewError, match="repeats an omission id"):
        build_case(case, published_on=today, audit=audit_result([case]), companions=[comp, comp])


async def test_the_tie_break_rules_a_companion_with_its_own_seed_and_the_opinion(data_act):  # noqa: F811
    from womm.eval.golden_review import tiebreak_problems
    from womm.eval.tiebreak import tiebreak_audit_seed_for, tiebreak_draft

    from .test_tiebreak import config as tb_config
    from .test_tiebreak import verdict

    def disagree(_system, user):
        ids = sorted(set(re.findall(r'"item_id": "([^"]+)"', user)))
        return {"verdicts": [{"item_id": i, "verdict": "disagree", "reason": "no"} for i in ids]}

    _, comp, _ = await draft_companion(data_act, [omission(Q1, "rsb")], judges=disagree)
    item = comp.important_omissions[0]
    assert item.review.decision == "pending"
    fake = FakeBackend({"tiebreak": [{"verdicts": [verdict(item.item_id)]}]})
    ruled = await tiebreak_draft(comp, data_act, "IA text", tb_config(), {"fake": fake},
                                 rsb_text=OPINION)  # fmt: skip
    assert ruled.important_omissions[0].review.decision == "llm_kept"
    assert ruled.provenance.tiebreak_audit.seed == tiebreak_audit_seed_for(f"{CASE}#rsb")
    assert tiebreak_problems(ruled) == [] and integrity_problems(ruled) == []
    # The tie-break saw the opinion around the quote, not only the quote.
    assert "the board finds that the report does not show" in fake.calls[0].user_content
