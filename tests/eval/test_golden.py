from __future__ import annotations

import pytest

from womm.data.fixtures import Fixture, load_fixture
from womm.eval.golden import (
    GOLDEN_DIR,
    ExpectedImpact,
    GoldenCase,
    GoldenError,
    check_against_fixture,
    load_all_golden,
    load_golden,
)

CASE_FILES = {
    "case_01_provider_compliance_costs.yaml": "eval_provider_compliance_costs",
    "case_02_sme_impacts.yaml": "eval_sme_impacts",
}


@pytest.fixture(scope="module")
def fixture() -> Fixture:
    return load_fixture()


@pytest.fixture(scope="module")
def cases() -> list[GoldenCase]:
    return load_all_golden()


@pytest.mark.parametrize(("filename", "scenario_id"), sorted(CASE_FILES.items()))
def test_golden_file_loads_and_matches_scenario(filename: str, scenario_id: str) -> None:
    case = load_golden(GOLDEN_DIR / filename)
    assert case.scenario_id == scenario_id
    assert case.case_id == filename.removesuffix(".yaml")
    assert case.ia_reference.startswith("SWD(2021) 84")
    assert case.notes
    assert case.important_omissions


def test_all_golden_cases_are_loaded(cases: list[GoldenCase]) -> None:
    assert {c.scenario_id for c in cases} == set(CASE_FILES.values())


def test_golden_cases_trace_to_scenario_provisions(
    cases: list[GoldenCase], fixture: Fixture
) -> None:
    for case in cases:
        check_against_fixture(case, fixture)


def test_golden_ids_are_unique(cases: list[GoldenCase]) -> None:
    case_ids = [c.case_id for c in cases]
    assert len(case_ids) == len(set(case_ids))
    item_ids = [
        *(e.expected_id for c in cases for e in c.expected_impacts),
        *(o.omission_id for c in cases for o in c.important_omissions),
    ]
    assert len(item_ids) == len(set(item_ids))


def test_impact_outside_scenario_raises(cases: list[GoldenCase], fixture: Fixture) -> None:
    sme_case = next(c for c in cases if c.scenario_id == "eval_sme_impacts")
    stray = ExpectedImpact(
        expected_id="stray",
        affected_actor="Providers of high-risk AI systems",
        mechanism="Quality management system",
        impact="Audit costs",
        provision_keys=["ai_act/high_risk/quality_management_system"],
        ia_section="SWD(2021) 84 Part 1 §6.1.3",
    )
    bad = sme_case.model_copy(update={"expected_impacts": [*sme_case.expected_impacts, stray]})
    with pytest.raises(GoldenError, match="quality_management_system"):
        check_against_fixture(bad, fixture)


def test_unknown_scenario_raises(cases: list[GoldenCase], fixture: Fixture) -> None:
    bad = cases[0].model_copy(update={"scenario_id": "no_such_scenario"})
    with pytest.raises(GoldenError, match="no_such_scenario"):
        check_against_fixture(bad, fixture)


def test_demo_scenario_is_rejected(cases: list[GoldenCase], fixture: Fixture) -> None:
    bad = cases[0].model_copy(update={"scenario_id": "demo_penalties_amended"})
    with pytest.raises(GoldenError, match="not an evaluation"):
        check_against_fixture(bad, fixture)
