"""Regulation data contract (R1) for the data owner, plus fixture scenario and source records."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import Field, model_serializer, model_validator

from womm.models.base import StrictModel


class Provision(StrictModel):
    provision_key: str = Field(
        min_length=1,
        description="Stable across versions; renumbered articles keep the same key.",
    )
    article: str = Field(description="Article number as printed in this version, e.g. '71'.")
    paragraph: str | None = Field(description="Paragraph number within the article, if any.")
    text: str
    source_id: str = Field(description="Source record this provision's text comes from.")


class RegulationVersion(StrictModel):
    version_id: str
    date: dt.date
    status: Literal["proposal", "amended", "adopted", "consolidated"]
    source: str = Field(description="CELEX number or URL of the published text.")
    provisions: list[Provision]

    @model_validator(mode="after")
    def _unique_keys(self) -> RegulationVersion:
        keys = [p.provision_key for p in self.provisions]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"duplicate provision_key in version {self.version_id}: {dupes}")
        return self

    def by_key(self) -> dict[str, Provision]:
        return {p.provision_key: p for p in self.provisions}


class Regulation(StrictModel):
    regulation_id: str
    title: str
    versions: list[RegulationVersion]


class Source(StrictModel):
    """A citable text. Experts may only quote from registered sources (R9)."""

    source_id: str
    title: str
    kind: Literal["provision", "memorandum", "annex"]
    text: str
    stripped_sections: list[str] = Field(
        default_factory=list,
        description="Section headings removed because they restate impact-assessment findings.",
    )
    redactions: list[str] = Field(
        default_factory=list,
        description=(
            "One 'redacted: <n> sentence(s) in <section heading>' per section of this source with "
            "sentences removed because they state or cite impact-assessment material. Neither "
            "the sentences nor the reasons are kept here (reasons stay in import.yaml)."
        ),
    )

    @model_serializer(mode="wrap")
    def _omit_empty_redactions(self, handler):
        # Sources without redactions serialise exactly as before the field existed.
        data = handler(self)
        if not self.redactions:
            data.pop("redactions", None)
        return data


class Scenario(StrictModel):
    scenario_id: str
    kind: Literal["evaluation", "demo"]
    description: str
    before_version: str | None = Field(description="None means 'no prior version' (R2 evaluation).")
    after_version: str
    provision_keys: list[str] = Field(min_length=1)
    ia_reference: str | None = None


def contract_json_schema() -> dict:
    """JSON Schema of the R1 delivery contract, for the data owner."""
    return Regulation.model_json_schema()
