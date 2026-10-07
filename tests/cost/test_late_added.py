"""R7 (EU cost plan U7): costs the ex-ante IA could not see, reported and never scored."""

from womm.cost.late_added import NOT_SCORED, format_late_added, late_added_report
from womm.data.fixtures import load_fixture
from womm.eval.golden import SELECTABLE_SPLITS, load_all_golden
from womm.models.cost import CostRecord


def rec(n, delta, *, payer="provider", band="medium", effort="documentation", **kw):
    data = {
        "obligation_id": f"o{n}", "unit_id": f"u{n}", "provision_key": f"k{n % 3}",
        "source_id": "reg2024_1689/obligations/x", "records_version": "reg2024_1689",
        "status": "estimated", "payer": payer, "payer_basis": "rule_field",
        "effort_type": effort, "one_off": band, "unit_delta": delta,
        "changed_after_proposal": delta in ("added", "modified", "split_merge"),
        "late_added": delta == "added",
    }  # fmt: skip
    data.update(kw)
    return CostRecord(**data)


def _ten():
    deltas = ["added", "unchanged", "added", "modified", "split_merge", "minor_edit",
              "added", "unchanged", "modified", "unchanged"]  # fmt: skip
    payers = ["deployer", "provider", "deployer", "provider", "provider", "provider",
              "ai_office", "provider", "provider", "provider"]  # fmt: skip
    return [rec(i, d, payer=p) for i, (d, p) in enumerate(zip(deltas, payers, strict=True))]


def test_exactly_the_added_records_are_listed_grouped_by_payer():
    report = late_added_report(_ten())
    assert [r.obligation_id for r in report.records] == ["o0", "o2", "o6"]
    assert report.by_payer == {"deployer": 2, "ai_office": 1}


def test_modified_and_split_merge_are_context_only():
    report = late_added_report(_ten())
    assert report.changed_not_added == 3
    assert "o3" not in {r.obligation_id for r in report.records}
    text = format_late_added(report)
    assert text.splitlines()[0] == NOT_SCORED
    assert "3 more records were changed after the proposal" in text


def test_share_of_costly_records_in_added_text():
    records = [*_ten(), rec(10, "added", band="low"), rec(11, "unchanged", band="negligible")]
    report = late_added_report(records)
    # medium/high: o0..o9 (10) are all medium; added among them: 3.
    assert report.costly_total == 10 and report.costly_added == 3
    assert report.by_payer_costly["deployer"] == {"added": 2, "ia_could_see": 0}
    assert report.by_payer_costly["provider"] == {"added": 0, "ia_could_see": 7}


def test_a_withheld_date_is_never_shown():
    r = rec(1, "added", applies_from=None, date_withheld="amended_2026")
    text = format_late_added(late_added_report([r]))
    assert "date withheld (amended 2026)" in text
    labelled = rec(2, "added", applies_from="2025-08-02", date_label="as adopted (2024)")
    text = format_late_added(late_added_report([labelled]))
    assert "2025-08-02 (as adopted (2024))" in text


def test_not_costed_and_not_estimated_records_are_counted_apart():
    records = [rec(1, "added"), rec(2, "added", status="not_costed", one_off=None, reason="x")]
    report = late_added_report(records)
    assert len(report.records) == 1 and report.added_not_costed == 1


async def test_a_final_vs_proposal_run_marks_the_records_r7_reports():
    from womm.data.corpus import load_default_corpus
    from womm.decisions.stub import StubDecisionService
    from womm.graph.build import explore_inputs, run_scenario
    from womm.llm.fake import FakeBackend, fake_cost_batch
    from womm.models.run import CodeIdentity

    from ..graph.test_cost_node import _fake

    fixture, corpus = load_fixture(), load_default_corpus()
    diff = explore_inputs(fixture.scenario("final_vs_proposal"), fixture, corpus)["diff"]
    final = corpus.obligations["reg2024_1689"]
    key = next(
        c.provision_key
        for c in diff.changes
        if any(r.unit_delta == "added" for r in final.get(c.provision_key, []))
    )
    area = {"provision_keys": [key], "question": "q", "rationale": "r"}
    empty = {"impacts": [], "chains": [], "disagreements": [], "open_questions": [],
             "discarded": []}  # fmt: skip
    script = {"planner": [{"focus_areas": [area]}], "expert": [{"findings": []}] * 3,
              "synthesis": [empty], "cost": [fake_cost_batch] * 5}  # fmt: skip
    result = await run_scenario(
        "final_vs_proposal", sv=_fake("v1.0-cost.yaml", cost=True), fixture=fixture,
        backends={"fake": FakeBackend(script)}, decisions=StubDecisionService(),
        code_identity=CodeIdentity(git_sha="t", dirty=False), run_id="run_r7", corpus=corpus,
    )  # fmt: skip
    section = result.dossier.costs
    added = {r.obligation_id for r in section.records if r.unit_delta == "added"}
    assert added and set(section.late_added) == added
    reported = {r.obligation_id for r in late_added_report(section.records).records}
    assert reported <= added
    assert reported == {r.obligation_id for r in section.records
                        if r.late_added and r.status == "estimated"}  # fmt: skip


def test_final_vs_proposal_is_an_explore_demo_and_never_a_golden_or_evolution_case():
    s = load_fixture().scenario("final_vs_proposal")
    assert (s.mode, s.kind, s.before_version, s.after_version) == (
        "explore", "demo", "com2021_206", "reg2024_1689",
    )  # fmt: skip
    assert s.ia_reference is None
    assert all(c.scenario_id != "final_vs_proposal" for c in load_all_golden())
    for split in SELECTABLE_SPLITS:
        assert all(c.scenario_id != "final_vs_proposal" for c in load_all_golden(split=split))
