"""Synthesis output (IDs only) and the assembled Impact Dossier (R8)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from womm.models.base import StrictModel
from womm.models.cost import CostSection
from womm.models.findings import ExpertFailure, ImpactFinding

# --- Synthesis LLM output: references findings by id, never re-emits quote text ---


class SynthesisImpact(StrictModel):
    impact_id: str = Field(description="Short local label, e.g. 'I1'.")
    summary: str = Field(description="One-sentence statement of the consolidated impact.")
    finding_ids: list[str] = Field(
        min_length=1, description="Findings merged into this impact; the first is primary."
    )


class ImpactChain(StrictModel):
    impact_ids: list[str] = Field(min_length=2, description="Causal order, e.g. ['I1','I3'].")
    description: str


class Disagreement(StrictModel):
    finding_ids: list[str] = Field(min_length=2)
    note: str


class SynthesisOpenQuestion(StrictModel):
    finding_id: str | None
    question: str


class Discard(StrictModel):
    finding_id: str
    reason: str


class SynthesisPlan(StrictModel):
    impacts: list[SynthesisImpact]
    chains: list[ImpactChain]
    disagreements: list[Disagreement]
    open_questions: list[SynthesisOpenQuestion]
    discarded: list[Discard]


# --- Assembled dossier: built deterministically from the plan + validated board ---


class DossierImpact(BaseModel):
    impact_id: str
    summary: str
    findings: list[ImpactFinding]
    merged: bool = Field(description="False when synthesis failed and findings are listed as-is.")


class OpenQuestion(BaseModel):
    question: str
    finding_id: str | None = None
    reason: Literal["evidence_unresolved", "synthesis"]


class DiscardedFinding(BaseModel):
    finding: ImpactFinding
    reason: str


DossierStatus = Literal["succeeded", "degraded", "failed", "no_changes"]


class LawVersion(BaseModel):
    """Header metadata: the version of the law a run analyses (its after version)."""

    version_id: str
    status: str
    source: str = Field(description="CELEX number or URL of the published text.")
    date: str
    pre_omnibus: bool = Field(
        default=False,
        description="True for the 2024 text as adopted, which Regulation (EU) 2026/1744 has "
        "since amended.",
    )
    note: str | None = None


class ImpactDossier(BaseModel):
    run_id: str
    scenario_id: str
    status: DossierStatus
    system_version: str
    law_version: LawVersion | None = None
    impacts: list[DossierImpact] = Field(default_factory=list)
    chains: list[ImpactChain] = Field(default_factory=list)
    disagreements: list[Disagreement] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    discarded: list[DiscardedFinding] = Field(default_factory=list)
    unprocessed: list[ImpactFinding] = Field(
        default_factory=list, description="Supported findings synthesis did not account for."
    )
    failed_experts: list[ExpertFailure] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    costs: CostSection | None = Field(
        default=None,
        description="Cost records and hotspots (cost-enabled versions only). Never read by "
        "synthesis or the coverage judge.",
    )
