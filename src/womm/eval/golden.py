"""Golden cases (R11): expected impacts written from an official impact assessment subsection.

Golden files live in evals/golden/, never in data/fixtures/: agents must not see them.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import Field

from womm.config import REPO_ROOT
from womm.data.fixtures import Fixture
from womm.models.base import StrictModel

GOLDEN_DIR = REPO_ROOT / "evals" / "golden"


class ExpectedImpact(StrictModel):
    expected_id: str
    affected_actor: str
    mechanism: str
    impact: str
    provision_keys: list[str] = Field(
        min_length=1, description="Scenario provisions this impact follows from."
    )
    ia_section: str = Field(description="Where in the impact assessment this is stated.")


class Omission(StrictModel):
    omission_id: str
    description: str = Field(description="An impact a good assessment must not leave out.")
    provision_keys: list[str] = Field(min_length=1)
    source: str = Field(description="e.g. 'SWD(2021) 84 §6.1.4' or 'RSB opinion'.")


class GoldenCase(StrictModel):
    case_id: str
    scenario_id: str
    ia_reference: str
    notes: str = ""
    expected_impacts: list[ExpectedImpact] = Field(min_length=1)
    important_omissions: list[Omission] = Field(default_factory=list)


class GoldenError(ValueError):
    pass


def load_golden(path: Path) -> GoldenCase:
    return GoldenCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_all_golden(directory: Path = GOLDEN_DIR) -> list[GoldenCase]:
    return [load_golden(p) for p in sorted(directory.glob("case_*.yaml"))]


def check_against_fixture(case: GoldenCase, fixture: Fixture) -> None:
    """Every expected impact and omission must trace to provisions inside the scenario;
    otherwise coverage would be capped by scenario construction, not system quality."""
    try:
        scenario = fixture.scenario(case.scenario_id)
    except Exception as exc:
        raise GoldenError(f"{case.case_id}: {exc}") from None
    if scenario.kind != "evaluation":
        raise GoldenError(f"{case.case_id}: scenario {scenario.scenario_id} is not an evaluation")
    keys = set(scenario.provision_keys)
    for item in [*case.expected_impacts, *case.important_omissions]:
        item_id = getattr(item, "expected_id", None) or item.omission_id
        missing = [k for k in item.provision_keys if k not in keys]
        if missing:
            raise GoldenError(
                f"{case.case_id}/{item_id}: provision_keys {missing} are not in scenario "
                f"{scenario.scenario_id}"
            )
