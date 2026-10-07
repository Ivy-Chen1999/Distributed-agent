"""The cost step: relevant records, batches, validation of the LLM answer, and failures."""

import re

import pytest

from womm.cost.estimate import (
    estimate_costs,
    make_batches,
    relevant_records,
    render_batch,
    validate_batch,
)
from womm.data.corpus import Corpus, load_corpus
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend, fake_cost_batch
from womm.models.cost import CostBatch
from womm.models.system_version import CostConfig

PROPOSAL, FINAL, CONSOLIDATED = "com2021_206", "reg2024_1689", "reg2024_1689_c20260727"
CASE01_KEYS = [
    "ai_act/high_risk/compliance_with_requirements",
    "ai_act/high_risk/risk_management",
    "ai_act/high_risk/data_governance",
    "ai_act/high_risk/technical_documentation",
    "ai_act/high_risk/record_keeping",
    "ai_act/high_risk/transparency_information",
    "ai_act/high_risk/human_oversight",
    "ai_act/high_risk/accuracy_robustness_cybersecurity",
    "ai_act/high_risk/provider_obligations",
    "ai_act/high_risk/quality_management_system",
    "ai_act/high_risk/conformity_assessment",
]
ROLE = CostConfig(backend="fake", model="fake-1", prompt="prompts/v1/cost.md")


@pytest.fixture(scope="module")
def corpus() -> Corpus:
    return load_corpus()


@pytest.fixture(scope="module")
def case01(corpus):
    return relevant_records(corpus, PROPOSAL, CASE01_KEYS)


def test_case01_has_103_relevant_duties_on_the_proposal(case01):
    assert len(case01.items) == 103  # measured 2026-10-07
    assert {i.record.statement_type for i in case01.items} <= {"duty", "prohibition"}
    assert case01.not_covered == []
    item = case01.items[0]
    assert item.source_id.startswith(f"{PROPOSAL}/obligations/")
    assert item.records_version == PROPOSAL


def test_permissions_and_powers_are_not_relevant(corpus):
    keys = list(corpus.obligations[FINAL])
    rel = relevant_records(corpus, FINAL, keys)
    assert len(rel.items) == 975
    assert {i.record.statement_type for i in rel.items} == {"duty", "prohibition"}


def test_a_consolidated_run_lists_omnibus_amended_keys_as_not_covered(corpus):
    keys = ["ai_act/art/26", "ai_act/high_risk/data_governance"]  # Art 10 was amended in 2026
    rel = relevant_records(corpus, CONSOLIDATED, keys)
    assert rel.not_covered == ["ai_act/high_risk/data_governance"]
    assert {i.key for i in rel.items} == {"ai_act/art/26"}
    assert {i.records_version for i in rel.items} == {FINAL}


def test_unknown_keys_are_not_covered(corpus):
    rel = relevant_records(corpus, PROPOSAL, ["ai_act/art/999"])
    assert rel.items == [] and rel.not_covered == ["ai_act/art/999"]


def test_batches_are_bounded_stable_and_keep_keys_whole(case01):
    batches = make_batches(case01.items, 60)
    assert [len(b.items) for b in batches] and max(len(b.items) for b in batches) <= 60
    assert sum(len(b.items) for b in batches) == 103
    again = make_batches(case01.items, 60)
    assert [b.batch_id for b in batches] == [b.batch_id for b in again]
    first_keys = {i.key for i in batches[0].items}
    second_keys = {i.key for i in batches[1].items}
    assert not first_keys & second_keys  # no key split when it fits


def test_a_key_larger_than_a_batch_is_split(case01):
    batches = make_batches(case01.items, 5)
    assert all(len(b.items) <= 5 for b in batches)
    assert sum(len(b.items) for b in batches) == 103


def test_rendered_batch_shows_ids_payers_and_no_delta(corpus):
    rel = relevant_records(corpus, FINAL, ["ai_act/art/26", "ai_act/high_risk/data_governance"])
    text = render_batch(make_batches(rel.items, 200)[0])
    ids = re.findall(r"^\[([^\]]+)\]$", text, flags=re.M)
    assert ids == [i.record.obligation_id for i in rel.items]
    assert "payer: deployer (named in the record)" in text
    assert "payer: provider (Art 16(a)" in text
    assert "unit_delta" not in text and "split_merge" not in text
    assert "payer: not identified" in text or all(i.payer.basis != "unknown" for i in rel.items)


def _batch(case01, n=3):
    return make_batches(case01.items[:n], 60)[0]


def _answer(batch, **override):
    rows = []
    for item in batch.items:
        row = {"obligation_id": item.record.obligation_id, "effort_type": "documentation",
               "one_off": "low", "rationale": "r"}  # fmt: skip
        row.update(override.get(item.record.obligation_id, {}))
        rows.append(row)
    return CostBatch.model_validate({"records": rows})


def test_one_off_only_and_both_bands_validate(case01):
    batch = _batch(case01)
    a, b, _ = (i.record.obligation_id for i in batch.items)
    out = validate_batch(batch, _answer(batch, **{b: {"recurring": "medium"}}))
    assert [r.status for r in out.records] == ["estimated"] * 3
    rec_b = next(r for r in out.records if r.obligation_id == b)
    assert (rec_b.one_off, rec_b.recurring) == ("low", "medium")
    rec = out.records[0]
    assert rec.payer == "provider" and rec.payer_basis == "rule_table"
    assert rec.sector == "private" and rec.ia_category == "administrative_burden"
    assert rec.unit_id == batch.items[0].record.unit_id


def test_estimated_record_without_band_is_dropped_and_listed_not_estimated(case01):
    batch = _batch(case01)
    a = batch.items[0].record.obligation_id
    out = validate_batch(batch, _answer(batch, **{a: {"one_off": None}}))
    assert out.invalid == 1
    rec = next(r for r in out.records if r.obligation_id == a)
    assert rec.status == "not_estimated" and "invalid" in rec.reason


def test_omitted_ids_are_not_estimated_and_foreign_ids_are_dropped(case01):
    batch = _batch(case01, 4)
    answer = _answer(batch)
    rows = answer.records[2:] + [answer.records[0].model_copy(update={"obligation_id": "zz#9"})]
    out = validate_batch(batch, CostBatch(records=rows))
    missing = {i.record.obligation_id for i in batch.items[:2]}
    assert {r.obligation_id for r in out.records if r.status == "not_estimated"} == missing
    assert out.invalid == 1
    assert "zz#9" not in {r.obligation_id for r in out.records}
    assert [r.obligation_id for r in out.records] == [i.record.obligation_id for i in batch.items]


def test_duplicate_answers_keep_the_first(case01):
    batch = _batch(case01, 1)
    answer = _answer(batch)
    dup = answer.records[0].model_copy(update={"one_off": "high"})
    out = validate_batch(batch, CostBatch(records=[answer.records[0], dup]))
    assert len(out.records) == 1 and out.records[0].one_off == "low" and out.invalid == 1


def test_not_costed_needs_a_reason(case01):
    batch = _batch(case01, 2)
    a, b = (i.record.obligation_id for i in batch.items)
    over = {
        a: {"status": "not_costed", "effort_type": None, "one_off": None, "reason": "prohibition"},
        b: {"status": "not_costed", "effort_type": None, "one_off": None},
    }
    out = validate_batch(batch, _answer(batch, **over))
    got = {r.obligation_id: r for r in out.records}
    assert got[a].status == "not_costed" and got[a].ia_category is None
    assert got[b].status == "not_estimated" and out.invalid == 1


def _unknown_payer_batch(corpus):
    rel = relevant_records(corpus, PROPOSAL, ["ai_act/art/5"])
    items = [i for i in rel.items if i.payer.basis == "unknown"]
    assert items
    return make_batches(items[:1], 60)[0]


def test_inferred_payer_with_a_verbatim_quote_is_kept(corpus):
    batch = _unknown_payer_batch(corpus)
    item = batch.items[0]
    quote = " ".join(item.record.span.split()[:5])
    oid = item.record.obligation_id
    answer = _answer(batch, **{oid: {"inferred_payer": "member_state", "payer_quote": quote}})
    (rec,) = validate_batch(batch, answer).records
    assert (rec.payer, rec.payer_basis, rec.payer_quote) == ("member_state", "inferred", quote)
    assert rec.sector == "public" and rec.ia_category == "public_enforcement_cost"


def test_inferred_payer_whose_quote_is_not_verbatim_falls_back_to_unknown(corpus):
    batch = _unknown_payer_batch(corpus)
    oid = batch.items[0].record.obligation_id
    answer = _answer(
        batch, **{oid: {"inferred_payer": "provider", "payer_quote": "words never in the span"}}
    )
    (rec,) = validate_batch(batch, answer).records
    assert rec.status == "estimated"
    assert (rec.payer, rec.payer_basis, rec.payer_quote) == (None, "unknown", None)


@pytest.mark.parametrize(
    ("payer", "quote_words"),
    [("martian", 5), ("provider", 2)],
)
def test_unknown_actor_or_too_short_quote_falls_back_to_unknown(corpus, payer, quote_words):
    batch = _unknown_payer_batch(corpus)
    item = batch.items[0]
    quote = " ".join(item.record.span.split()[:quote_words])
    answer = _answer(batch, **{item.record.obligation_id: {"inferred_payer": payer,
                                                            "payer_quote": quote}})  # fmt: skip
    (rec,) = validate_batch(batch, answer).records
    assert rec.payer_basis == "unknown"


def test_a_rule_payer_is_never_overridden_by_an_inference(case01):
    batch = _batch(case01, 1)
    oid = batch.items[0].record.obligation_id
    quote = " ".join(batch.items[0].record.span.split()[:4])
    answer = _answer(batch, **{oid: {"inferred_payer": "deployer", "payer_quote": quote}})
    (rec,) = validate_batch(batch, answer).records
    assert (rec.payer, rec.payer_basis) == ("provider", "rule_table")


def test_unit_delta_marks_changed_and_late_added_records(corpus):
    rel = relevant_records(corpus, FINAL, list(corpus.obligations[FINAL]))
    by_delta = {}
    for item in rel.items:
        by_delta.setdefault(item.record.unit_delta, item)
    batch = make_batches(list(by_delta.values()), 60)[0]
    out = validate_batch(batch, _answer(batch))
    got = {r.unit_delta: r for r in out.records}
    assert got["added"].late_added and got["added"].changed_after_proposal
    for kind in ("modified", "split_merge"):
        assert got[kind].changed_after_proposal and not got[kind].late_added
    for kind in ("minor_edit", "unchanged"):
        assert not got[kind].changed_after_proposal and not got[kind].late_added


def test_proposal_records_are_never_marked(case01):
    batch = _batch(case01)
    out = validate_batch(batch, _answer(batch))
    assert not any(r.changed_after_proposal or r.late_added for r in out.records)


def test_withheld_dates_stay_withheld(corpus):
    rel = relevant_records(corpus, FINAL, ["ai_act/art/26"])
    batch = make_batches(rel.items, 60)[0]
    out = validate_batch(batch, _answer(batch))
    for rec in out.records:
        assert rec.applies_from is None and rec.date_withheld == "date_moved_2026"


async def test_estimate_costs_runs_every_batch_on_the_fake_backend(case01):
    backend = FakeBackend({"cost": [fake_cost_batch] * 5})
    est = await estimate_costs(
        case01.items, backend=backend, role=ROLE.model_copy(update={"max_records_per_call": 60}),
        prompt="p", max_parallel=2,
    )  # fmt: skip
    assert len(est.records) == 103 and len(backend.calls) == 2
    assert {r.status for r in est.records} <= {"estimated", "not_costed"}
    assert [u.role for u in est.usage] == ["cost", "cost"]
    assert est.notes == []


async def test_a_timed_out_batch_gives_not_estimated_records_and_a_note(case01):
    backend = FakeBackend({"cost": [fake_cost_batch, LLMError("timeout", "too slow")]})
    est = await estimate_costs(case01.items, backend=backend, role=ROLE, prompt="p", max_parallel=1)
    failed = [r for r in est.records if r.status == "not_estimated"]
    assert failed and all(r.reason == "timeout" for r in failed)
    assert len(est.records) == 103
    assert len(est.notes) == 1 and "timeout" in est.notes[0]
    assert str(len(failed)) in est.notes[0]
