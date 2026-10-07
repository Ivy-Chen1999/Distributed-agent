"""Golden cases (R11): expected impacts written from an official impact assessment subsection.

Golden files live in evals/golden/, never in data/fixtures/: agents must not see them. Each case
names the fixture it is scored against (``data/fixtures/<fixture>``) and its split. Holdout
cases are never stored as YAML in this repository (R23): the loader refuses them, and only the
top-level ``case_*.yaml`` files are read, so drafts under ``evals/golden/drafts/`` are never
scored.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from womm.config import REPO_ROOT
from womm.data.fixtures import DEFAULT_FIXTURE, Fixture, FixtureError, fixture_dir, load_fixture
from womm.models.base import StrictModel

GOLDEN_DIR = REPO_ROOT / "evals" / "golden"
Split = Literal["train", "val", "holdout"]
SELECTABLE_SPLITS = ("train", "val")
HOLDOUT_REFUSAL = (
    "holdout cases are never stored under evals/; they are sealed in the holdout database "
    "through scripts/import_holdout_case.py"
)


# How a published item was decided (plan Revision 2026-10-04). Hand-written cases have none.
ItemProvenance = Literal[
    "llm_judged", "human_verified", "human_edited", "human_confirmed_candidate"
]


class ExpectedImpact(StrictModel):
    expected_id: str
    affected_actor: str
    mechanism: str
    impact: str
    provision_keys: list[str] = Field(
        min_length=1, description="Scenario provisions this impact follows from."
    )
    ia_section: str = Field(description="Where in the impact assessment this is stated.")
    category: str | None = Field(default=None, description="Drafting category, if drafted.")
    provenance: ItemProvenance | None = None
    origin: Literal["human"] | None = Field(
        default=None,
        description="'human' when a person (an analyst) raised the item rather than the drafting "
        "tool; misses on such items never drive the new-expert trigger.",
    )


class Omission(StrictModel):
    omission_id: str
    description: str = Field(description="An impact a good assessment must not leave out.")
    provision_keys: list[str] = Field(min_length=1)
    source: str = Field(description="e.g. 'SWD(2021) 84 §6.1.4' or 'RSB opinion'.")
    category: str | None = None
    provenance: ItemProvenance | None = None


class GoldenCase(StrictModel):
    case_id: str
    scenario_id: str
    ia_reference: str
    fixture: str = Field(
        default=DEFAULT_FIXTURE,
        pattern=r"^[a-z0-9][a-z0-9_]*$",
        description="Fixture directory under data/fixtures/ the case is scored against.",
    )
    split: Split = Field(
        default="train",
        description="Defaults to train only for the original ai_act cases; explicit elsewhere.",
    )
    notes: str = ""
    expected_impacts: list[ExpectedImpact] = Field(min_length=1)
    important_omissions: list[Omission] = Field(default_factory=list)

    @model_validator(mode="after")
    def _split_explicit_outside_ai_act(self) -> GoldenCase:
        # A forgotten split must not silently put a new fixture's case into train.
        if self.fixture != DEFAULT_FIXTURE and "split" not in self.model_fields_set:
            raise ValueError(
                f"{self.case_id}: split must be explicit for fixture {self.fixture!r} "
                "(train or val)"
            )
        return self


class GoldenError(ValueError):
    pass


def load_golden(path: Path) -> GoldenCase:
    """One public golden case; a holdout case is refused (it must never sit in the repo)."""
    case = GoldenCase.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if case.split == "holdout":
        raise GoldenError(f"{path.name}: split 'holdout' is not allowed here; {HOLDOUT_REFUSAL}")
    return case


def load_all_golden(directory: Path = GOLDEN_DIR, split: str | None = None) -> list[GoldenCase]:
    """Every top-level ``case_*.yaml`` (never ``drafts/``), optionally only one split."""
    if split is not None and split not in SELECTABLE_SPLITS:
        raise GoldenError(f"split {split!r} cannot be selected; use one of {SELECTABLE_SPLITS}")
    cases = [load_golden(p) for p in sorted(directory.glob("case_*.yaml"))]
    return [c for c in cases if split is None or c.split == split]


def load_case_fixture(case: GoldenCase, cache: dict[str, Fixture] | None = None) -> Fixture:
    """The fixture a case is scored against, loaded once per name when ``cache`` is given."""
    if cache is not None and case.fixture in cache:
        return cache[case.fixture]
    try:
        fixture = load_fixture(fixture_dir(case.fixture))
    except FixtureError as exc:
        raise GoldenError(f"{case.case_id}: {exc}") from None
    if cache is not None:
        cache[case.fixture] = fixture
    return fixture


def check_against_fixture(case: GoldenCase, fixture: Fixture | None = None) -> None:
    """Every expected impact and omission must trace to provisions inside the scenario;
    otherwise coverage would be capped by scenario construction, not system quality.

    Without ``fixture`` the case's own fixture is loaded; a given fixture must be that one."""
    if fixture is None:
        fixture = load_case_fixture(case)
    elif fixture.regulation.regulation_id != case.fixture:
        raise GoldenError(
            f"{case.case_id}: case is for fixture {case.fixture!r}, not "
            f"{fixture.regulation.regulation_id!r}"
        )
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
