"""Run lifecycle and results."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from womm.models.decisions import DecisionRecord
from womm.models.dossier import ImpactDossier
from womm.models.findings import ExpertFailure, ImpactFinding


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    degraded = "degraded"
    failed = "failed"
    no_changes = "no_changes"


class CodeIdentity(BaseModel):
    git_sha: str | None
    dirty: bool
    claude_cli_version: str | None = None


class CallUsage(BaseModel):
    role: str
    agent: str | None = None
    backend: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    latency_s: float = 0.0


class GroundingStats(BaseModel):
    """Quote-existence rate (R12): passed evidence items / all evidence items, before synthesis."""

    passed: int = 0
    total: int = 0

    @property
    def rate(self) -> float | None:
        return self.passed / self.total if self.total else None


class RunResult(BaseModel):
    run_id: str
    scenario_id: str
    status: RunStatus
    system_version: str
    code_identity: CodeIdentity
    dossier: ImpactDossier | None = None
    board: list[ImpactFinding] = Field(default_factory=list)
    failures: list[ExpertFailure] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    grounding: GroundingStats = Field(default_factory=GroundingStats)
    usage: list[CallUsage] = Field(default_factory=list)
    error: str | None = None
