"""Cost records (EU cost plan R4/R5): who pays, for what kind of effort, how much and from when.

A cost record is half LLM judgment, half deterministic code:

- the cost step (an LLM, ``womm.cost.estimate``) returns a ``CostDraft`` per obligation: effort
  types, an ordinal one-off and/or recurring band, an inferred payer with a verbatim quote where
  no rule names one, or ``not_costed`` with a reason;
- code fills everything else (citation, payer and its basis, sector, IA cost category, dates,
  the unit delta) and validates the draft into a ``CostRecord``.

Bands are ordinal. Nothing here, in the dossier or in the API sums money across records.
"""

from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field

from womm.models.base import StrictModel

EffortType = Literal[
    "new_process",
    "documentation",
    "registration",
    "notification",
    "human_oversight",
    "training",
    "assessment",
]
EFFORT_TYPES: tuple[str, ...] = get_args(EffortType)
Band = Literal["negligible", "low", "medium", "high"]
BANDS: tuple[str, ...] = get_args(Band)
PayerBasis = Literal["rule_field", "rule_table", "inferred", "unknown"]
Sector = Literal["private", "public"]
IaCategory = Literal["compliance_cost", "administrative_burden", "public_enforcement_cost"]
CostStatus = Literal["estimated", "not_costed", "not_estimated"]
Recurrence = Literal["one_off", "recurring"]
HotspotDimension = Literal["provision", "payer", "effort_type"]


def band_rank(band: str | None) -> int:
    """0 for no band, 1 (negligible) to 4 (high)."""
    return 0 if band is None else BANDS.index(band) + 1


# --- LLM output --------------------------------------------------------------------------------


class CostDraft(StrictModel):
    """The cost step's answer for one obligation record. Semantic checks (an estimated record
    needs an effort type and at least one band; a quote must be verbatim) happen in
    ``womm.cost.estimate.validate_batch``, so one bad record is dropped, not the whole batch."""

    obligation_id: str = Field(description="The id in square brackets, exactly as given.")
    status: Literal["estimated", "not_costed"] = Field(
        default="estimated",
        description="'not_costed' when the obligation carries no direct compliance effort "
        "(for example a prohibition, whose cost is forgone business).",
    )
    effort_type: EffortType | None = Field(default=None, description="The primary effort type.")
    secondary_types: list[EffortType] = Field(default_factory=list)
    one_off: Band | None = Field(default=None, description="One-off band per entity, or null.")
    recurring: Band | None = Field(default=None, description="Per-year band per entity, or null.")
    inferred_payer: str | None = Field(
        default=None,
        description="Only where the record says the payer is not identified: the actor "
        "category that bears the effort.",
    )
    payer_quote: str | None = Field(
        default=None,
        description="With an inferred payer: a short verbatim quote from the record's span "
        "(at least 3 words) that shows who bears the effort.",
    )
    rationale: str = Field(default="", description="One sentence.")
    reason: str | None = Field(default=None, description="Why the record is not costed.")


class CostBatch(StrictModel):
    records: list[CostDraft]


# --- assembled records -------------------------------------------------------------------------


class CostRecord(BaseModel):
    obligation_id: str
    unit_id: str
    provision_key: str
    source_id: str = Field(description="The citable obligation view of the record's provision.")
    records_version: str
    statement_type: str | None = None
    status: CostStatus
    payer: str | None = None
    payer_basis: PayerBasis = "unknown"
    payer_legal_basis: str | None = Field(
        default=None, description="For a rule-table payer: the provision that names the payer."
    )
    payer_quote: str | None = None
    sector: Sector | None = None
    effort_type: EffortType | None = None
    secondary_types: list[EffortType] = Field(default_factory=list)
    ia_category: IaCategory | None = None
    one_off: Band | None = None
    recurring: Band | None = None
    applies_from: str | None = None
    date_label: str | None = None
    date_withheld: str | None = None
    unit_delta: str | None = None
    changed_after_proposal: bool = False
    late_added: bool = False
    rationale: str | None = None
    reason: str | None = None

    def band(self, recurrence: Recurrence) -> Band | None:
        return self.one_off if recurrence == "one_off" else self.recurring

    @property
    def top_band(self) -> Band | None:
        bands = [b for b in (self.one_off, self.recurring) if b is not None]
        return max(bands, key=band_rank) if bands else None


class CostHotspot(BaseModel):
    """One row of a hotspot ranking: records at ``medium`` or ``high``, then at ``low``."""

    dimension: HotspotDimension
    value: str
    recurrence: Recurrence
    medium_or_high: int
    low: int
    records: int
    provision_keys: list[str] = Field(default_factory=list)


class CostCoverage(BaseModel):
    relevant: int = 0
    estimated: int = 0
    not_costed: int = 0
    not_estimated: int = 0
    invalid_dropped: int = 0
    not_covered_keys: list[str] = Field(default_factory=list)
    payers_by_basis: dict[str, int] = Field(default_factory=dict)


class CostSection(BaseModel):
    """The dossier's cost section (R4/R5). Never read by the coverage judge or synthesis."""

    records: list[CostRecord] = Field(default_factory=list)
    hotspots: list[CostHotspot] = Field(default_factory=list)
    coverage: CostCoverage = Field(default_factory=CostCoverage)
    late_added: list[str] = Field(
        default_factory=list, description="Obligation ids added after the proposal."
    )
    delta_basis: str | None = Field(
        default=None, description="What the change marks compare, when the run has a before."
    )
    notes: list[str] = Field(default_factory=list)
