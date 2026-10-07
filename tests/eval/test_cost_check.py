"""The R6 IA cost check (EU cost plan U6): pre-registered metrics against SWD(2021) 84."""

import pytest
import yaml

from womm.config import REPO_ROOT
from womm.data.corpus import load_corpus
from womm.eval.cost_check import (
    REFERENCE_PATH,
    CostReferenceError,
    format_report,
    kendall_tau_b,
    load_reference,
    score_records,
    score_repetitions,
)
from womm.models.cost import CostRecord

CASE01_KEYS = {
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
}


@pytest.fixture(scope="module")
def corpus():
    return load_corpus()


def _verified(tmp_path, mutate=None):
    data = yaml.safe_load(REFERENCE_PATH.read_text(encoding="utf-8"))
    data.update(status="verified", verified_by="test", verified_on="2026-10-07")
    if mutate:
        mutate(data)
    path = tmp_path / "ref.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def ref(tmp_path, corpus):
    return load_reference(_verified(tmp_path), corpus)


def rec(key, payer, *, one_off=None, recurring=None, oid=None, status="estimated",
        basis="rule_field"):  # fmt: skip
    return CostRecord(
        obligation_id=oid or f"{key}#{payer}#{one_off}#{recurring}", unit_id="u", provision_key=key,
        source_id="s", records_version="com2021_206", status=status, payer=payer,
        payer_basis=basis, effort_type="documentation", one_off=one_off, recurring=recurring,
    )  # fmt: skip


def _matching_all(ref):
    """One record per IA item on its first key, with the item's payer and band."""
    out = []
    for item in ref.items:
        band = item.band or "low"
        kw = {"one_off": band} if item.recurrence in ("one_off", "both") else {}
        if item.recurrence in ("recurring", "both"):
            kw["recurring"] = band
        out.append(rec(item.keys[0], item.payers[0], **kw))
    return out


def test_the_committed_reference_is_unverified_and_refused(corpus):
    with pytest.raises(CostReferenceError, match="unverified"):
        load_reference(REFERENCE_PATH, corpus)


def test_the_committed_reference_loads_when_verified(ref):
    ids = [i.item_id for i in ref.items]
    assert len(ids) == 11 and len(set(ids)) == 11
    by = {i.item_id: i for i in ref.items}
    # Bands are computed from the figures, never typed.
    assert by["ia01_data"].band == "low" and by["ia04_robustness"].band == "medium"
    assert by["ia05_human_oversight"].band == "medium"
    assert by["ia08_nb_documentation_review"].band == "medium"
    assert by["ia09_national_authorities"].band == "high"
    assert by["ia10_eu_coordination"].band == "high"
    assert by["ia06_user_documentation"].band == "negligible"
    assert by["ia07_qms_audit"].band is None  # priced per day: recall only
    assert "ai_act/high_risk/data_governance" in by["ia01_data"].keys
    assert "ai_act/annex/IV" in by["ia02_documentation"].keys
    assert [i.item_id for i in ref.items if i.comparable_unit] == [
        "ia01_data", "ia02_documentation", "ia03_information", "ia04_robustness",
        "ia08_nb_documentation_review",
    ]  # fmt: skip


def test_matching_records_give_recall_and_band_agreement_of_one(ref):
    m = score_records(_matching_all(ref), ref)
    assert m["cost_recall"] == 1.0
    assert m["band_exact"] == 1.0 and m["band_within_one"] == 1.0
    assert m["payer_recurrence_agreement"] == 1.0


def test_human_oversight_on_providers_one_off_fails_payer_recurrence(ref):
    by = {i.item_id: i for i in ref.items}
    key = by["ia05_human_oversight"].keys[0]
    records = [r for r in _matching_all(ref) if r.provision_key != key]
    records.append(rec(key, "provider", one_off="medium"))
    m = score_records(records, ref)
    assert m["items"]["ia05_human_oversight"]["payer_recurrence"] is False
    assert m["items"]["ia05_human_oversight"]["recalled"] is False
    records.append(rec(key, "deployer", one_off="low"))
    m = score_records(records, ref)
    assert m["items"]["ia05_human_oversight"]["recalled"] is True
    assert m["items"]["ia05_human_oversight"]["payer_recurrence"] is False


def test_records_where_the_ia_is_silent_are_reported_not_scored(ref, corpus):
    art51 = next(r.key for r in corpus.index_rows("com2021_206") if r.number == "51")
    base = score_records(_matching_all(ref), ref)
    m = score_records([*_matching_all(ref), rec(art51, "provider", one_off="high")], ref)
    assert m["ia_silent_costly"] == [art51]
    for k in ("cost_recall", "band_exact", "band_within_one", "payer_recurrence_agreement"):
        assert m[k] == base[k]


def test_tau_b_with_all_comparable_items_in_one_band_is_undefined(ref):
    records = [rec(i.keys[0], i.payers[0], one_off="low") for i in ref.items if i.comparable_unit]
    m = score_records(records, ref)
    assert m["rank_tau_b"] is None and m["rank_n"] == 5
    assert "undefined" in m["rank_note"]


def test_kendall_tau_b_with_ties():
    assert kendall_tau_b([1, 2, 3], [10, 20, 30]) == pytest.approx(1.0)
    assert kendall_tau_b([3, 2, 1], [10, 20, 30]) == pytest.approx(-1.0)
    # x has a tie: tau-b = (C - D) / sqrt((n0 - n1)(n0 - n2)) = 2 / sqrt(2 * 3)
    assert kendall_tau_b([1, 1, 2], [1, 2, 3]) == pytest.approx(2 / (6**0.5))
    assert kendall_tau_b([1, 1, 1], [1, 2, 3]) is None


def test_a_scenario_level_check_scores_only_items_in_the_scenario(ref):
    m = score_records(_matching_all(ref), ref, restrict_keys=CASE01_KEYS)
    scored = set(m["items"])
    assert "ia09_national_authorities" not in scored and "ia10_eu_coordination" not in scored
    assert {"ia01_data", "ia05_human_oversight", "ia08_nb_documentation_review"} <= scored
    assert m["cost_recall"] == 1.0


def test_not_estimated_records_count_for_nothing(ref):
    records = [r.model_copy(update={"status": "not_estimated"}) for r in _matching_all(ref)]
    assert score_records(records, ref)["cost_recall"] == 0.0


def test_repetitions_report_mean_and_min_max_and_payer_bases(ref):
    good = _matching_all(ref)
    half = good[: len(good) // 2]
    out = score_repetitions({1: good, 2: half}, ref)
    recall = out["metrics"]["cost_recall"]
    assert recall["max"] == 1.0 and recall["min"] < 1.0
    assert recall["mean"] == pytest.approx((recall["min"] + recall["max"]) / 2)
    assert out["repetitions"] == 2
    assert out["payers_by_basis"]["rule_field"] > 0
    text = format_report(out, header={"reference_sha": "abc", "backend": "claude_code"})
    assert "dev-only" in text and "descriptive, not significant" in text and "abc" in text


def test_an_article_without_an_index_row_fails_loading(tmp_path, corpus):
    def bad(data):
        data["items"][0]["proposal_articles"] = ["999"]

    with pytest.raises(CostReferenceError, match="999"):
        load_reference(_verified(tmp_path, bad), corpus)


def test_unfilled_fields_fail_loading(tmp_path, corpus):
    def unfilled(data):
        data["items"][1]["payers"] = []

    with pytest.raises(CostReferenceError, match="ia02_documentation"):
        load_reference(_verified(tmp_path, unfilled), corpus)


def test_the_reference_lives_under_evals_only():
    assert REFERENCE_PATH.is_relative_to(REPO_ROOT / "evals")
