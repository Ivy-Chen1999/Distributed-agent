from __future__ import annotations

import pytest
import yaml

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


def test_existing_cases_default_to_ai_act_train(cases: list[GoldenCase]) -> None:
    assert {(c.fixture, c.split) for c in cases} == {("ai_act", "train")}


def test_case_is_checked_against_its_own_fixture(cases, second_fixture) -> None:
    other = cases[0].model_copy(update={"fixture": "other"})
    check_against_fixture(other)  # loads data/fixtures/other
    with pytest.raises(GoldenError, match="'other', not 'ai_act'"):
        check_against_fixture(other, load_fixture())


def test_unknown_fixture_is_named(cases: list[GoldenCase]) -> None:
    bad = cases[0].model_copy(update={"fixture": "no_such_fixture"})
    with pytest.raises(GoldenError, match="unknown fixture 'no_such_fixture'"):
        check_against_fixture(bad)


def test_fixture_name_cannot_escape_the_fixture_root() -> None:
    data = load_golden(GOLDEN_DIR / "case_02_sme_impacts.yaml").model_dump()
    with pytest.raises(ValueError, match="fixture"):
        GoldenCase.model_validate({**data, "fixture": "../ai_act"})


def _write_case(directory, name: str, **update) -> None:
    data = load_golden(GOLDEN_DIR / "case_02_sme_impacts.yaml").model_dump(mode="json")
    data.update(update)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(yaml.safe_dump(data), encoding="utf-8")


def test_holdout_yaml_is_refused(tmp_path) -> None:
    _write_case(tmp_path, "case_09_sealed.yaml", case_id="case_09_sealed", split="holdout")
    with pytest.raises(GoldenError, match="holdout.*import_holdout_case"):
        load_all_golden(tmp_path)


def test_drafts_are_ignored_and_splits_selected(tmp_path) -> None:
    _write_case(tmp_path, "case_01_a.yaml", case_id="case_01_a")
    _write_case(tmp_path, "case_02_b.yaml", case_id="case_02_b", split="val")
    _write_case(tmp_path / "drafts", "case_03_c.yaml", case_id="case_03_c", split="holdout")
    assert [c.case_id for c in load_all_golden(tmp_path)] == ["case_01_a", "case_02_b"]
    assert [c.case_id for c in load_all_golden(tmp_path, split="val")] == ["case_02_b"]
    with pytest.raises(GoldenError, match="cannot be selected"):
        load_all_golden(tmp_path, split="holdout")


def test_split_must_be_explicit_outside_ai_act() -> None:
    data = load_golden(GOLDEN_DIR / "case_02_sme_impacts.yaml").model_dump(mode="json")
    data.pop("split")
    assert GoldenCase.model_validate(data).split == "train"  # ai_act keeps the default
    with pytest.raises(ValueError, match="split must be explicit"):
        GoldenCase.model_validate({**data, "fixture": "data_act"})
    assert GoldenCase.model_validate({**data, "fixture": "data_act", "split": "val"}).split == "val"


def test_case_file_without_split_for_another_fixture_fails_to_load(tmp_path) -> None:
    data = load_golden(GOLDEN_DIR / "case_02_sme_impacts.yaml").model_dump(mode="json")
    data.pop("split")
    path = tmp_path / "case_05_x.yaml"
    path.write_text(yaml.safe_dump({**data, "fixture": "data_act"}), encoding="utf-8")
    with pytest.raises(ValueError, match="split must be explicit"):
        load_golden(path)
