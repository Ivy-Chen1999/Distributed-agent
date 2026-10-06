"""Impact findings: the LLM-facing draft shape and the system-owned stored shape (R5, R6)."""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

from womm.models.base import StrictModel

# ``no_data_in_scope``: a scoped expert whose retrieval granted no key was not called. It is not
# an error of the expert; the dossier notes it and it does not degrade the run on its own.
ErrorKind = Literal[
    "auth", "rate_limit", "timeout", "schema_invalid", "process_error", "no_data_in_scope"
]
NO_DATA_IN_SCOPE = "no_data_in_scope"


class EvidenceDraft(StrictModel):
    source_id: str
    quote: str = Field(description="Verbatim text copied from the source.")


class FindingDraft(StrictModel):
    """What an expert LLM returns. IDs, agent and provenance are filled by the system."""

    provision_key: str
    affected_actor: str
    impact: str
    mechanism: str
    evidence: list[EvidenceDraft]
    confidence: float = Field(ge=0, le=1)


class FindingBatch(StrictModel):
    findings: list[FindingDraft]


class Provenance(BaseModel):
    agent: str
    system_version: str
    prompt_hash: str
    backend: str
    model: str
    round: int = 1


class Evidence(BaseModel):
    evidence_id: str
    source_id: str
    quote: str


class ImpactFinding(BaseModel):
    finding_id: str
    agent: str
    provision_key: str
    affected_actor: str
    impact: str
    mechanism: str
    evidence: list[Evidence]
    confidence: float
    provenance: Provenance

    @classmethod
    def from_draft(
        cls, draft: FindingDraft, *, run_id: str, index: int, provenance: Provenance
    ) -> ImpactFinding:
        finding_id = stable_id("f", run_id, provenance.agent, str(index))
        return cls(
            finding_id=finding_id,
            agent=provenance.agent,
            provision_key=draft.provision_key,
            affected_actor=draft.affected_actor,
            impact=draft.impact,
            mechanism=draft.mechanism,
            evidence=[
                Evidence(
                    evidence_id=stable_id("e", finding_id, str(i)),
                    source_id=e.source_id,
                    quote=e.quote,
                )
                for i, e in enumerate(draft.evidence)
            ],
            confidence=draft.confidence,
            provenance=provenance,
        )


class ExpertFailure(BaseModel):
    agent: str
    error_kind: ErrorKind
    message: str
    attempts: int = 1

    @property
    def no_data(self) -> bool:
        return self.error_kind == NO_DATA_IN_SCOPE


def no_data_note(agent: str) -> str:
    return f"{agent}: no data within its scope for this run"


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:12]
    return f"{prefix}_{digest}"
