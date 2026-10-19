"""The dossier cost section: hotspot rankings by band counts (never money) and change marks."""

from womm.cost.hotspots import DELTA_BASIS, NO_PAYER, build_section
from womm.models.cost import CostRecord


def rec(oid, key="k1", *, one_off=None, recurring=None, payer="provider", effort="documentation",
        delta=None, status="estimated"):  # fmt: skip
    return CostRecord(
        obligation_id=oid, unit_id=f"u:{oid}", provision_key=key,
        source_id=f"v/obligations/{key}", records_version="v", status=status, payer=payer,
        payer_basis="rule_field" if payer else "unknown", effort_type=effort,
        one_off=one_off, recurring=recurring, unit_delta=delta,
        changed_after_proposal=delta in ("added", "modified", "split_merge"),
        late_added=delta == "added",
    )  # fmt: skip


def _rank(section, dimension, recurrence):
    return [h for h in section.hotspots if h.dimension == dimension and h.recurrence == recurrence]


def test_three_medium_or_high_records_rank_their_key_first():
    records = [
        rec("a1", "k1", one_off="low"),
        rec("b1", "k2", one_off="medium"),
        rec("b2", "k2", one_off="high"),
        rec("b3", "k2", one_off="medium"),
        rec("c1", "k3", one_off="low"),
        rec("c2", "k3", one_off="low"),
    ]
    section = build_section(records, [])
    ranked = _rank(section, "provision", "one_off")
    assert [h.value for h in ranked] == ["k2", "k3", "k1"]
    assert (ranked[0].medium_or_high, ranked[0].low, ranked[0].records) == (3, 0, 3)
    assert (ranked[1].medium_or_high, ranked[1].low) == (0, 2)


def test_ties_keep_corpus_order():
    records = [rec("a", "k9", one_off="low"), rec("b", "k1", one_off="low")]
    ranked = _rank(build_section(records, []), "provision", "one_off")
    assert [h.value for h in ranked] == ["k9", "k1"]


def test_one_off_and_recurring_are_ranked_separately_and_a_record_counts_in_both():
    records = [
        rec("a", "k1", one_off="high", recurring="low"),
        rec("b", "k2", recurring="high"),
    ]
    section = build_section(records, [])
    assert [h.value for h in _rank(section, "provision", "one_off")] == ["k1"]
    rec_rank = _rank(section, "provision", "recurring")
    assert [h.value for h in rec_rank] == ["k2", "k1"]


def test_rankings_by_payer_and_effort_type():
    records = [
        rec("a", payer="deployer", effort="human_oversight", recurring="medium"),
        rec("b", payer=None, effort="documentation", recurring="low"),
    ]
    section = build_section(records, [])
    assert [h.value for h in _rank(section, "payer", "recurring")] == ["deployer", NO_PAYER]
    assert [h.value for h in _rank(section, "effort_type", "recurring")] == [
        "human_oversight",
        "documentation",
    ]


def test_not_costed_and_not_estimated_records_are_not_in_hotspots():
    records = [
        rec("a", one_off="high", status="not_costed"),
        rec("b", one_off=None, status="not_estimated"),
    ]
    section = build_section(records, ["k7"])
    assert section.hotspots == []
    assert section.coverage.not_covered_keys == ["k7"]
    assert (section.coverage.not_costed, section.coverage.not_estimated) == (1, 1)


def test_late_added_ids_and_the_delta_basis_label():
    records = [
        rec("a", delta="added", one_off="low"),
        rec("b", delta="modified", one_off="low"),
        rec("c", delta="unchanged", one_off="low"),
    ]
    section = build_section(records, [])
    assert section.late_added == ["a"]
    assert section.delta_basis == DELTA_BASIS
    assert "2021" in DELTA_BASIS and "2024" in DELTA_BASIS


def test_a_proposal_only_run_has_no_marks_and_no_delta_basis():
    section = build_section([rec("a", one_off="low")], [])
    assert section.late_added == [] and section.delta_basis is None
    assert not any(r.changed_after_proposal for r in section.records)


def test_the_section_never_carries_money():
    dumped = build_section([rec("a", one_off="high")], []).model_dump_json()
    assert "eur" not in dumped.lower()
