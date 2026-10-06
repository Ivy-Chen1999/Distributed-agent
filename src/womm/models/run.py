"""Run lifecycle and results."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from womm.models.base import StrictModel
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
    # sha256 prefix of `git diff HEAD` when dirty: tells two uncommitted edits apart.
    diff_sha: str | None = None


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


RetrievalStatus = Literal["granted_text", "granted_obligations", "out_of_scope", "unknown_key"]
GRANTED: frozenset[str] = frozenset({"granted_text", "granted_obligations"})


class RetrievalRecord(StrictModel):
    """One Layer 1 retrieval (R12): an agent asked for a provision key and got its text, its
    obligation view, or nothing. ``source_ids`` are the sources granted for the key."""

    agent: str
    layer: Literal[1] = 1
    key: str
    status: RetrievalStatus
    source_ids: list[str] = Field(default_factory=list)
    at: dt.datetime = Field(description="UTC time of the retrieval.")

    @field_validator("at")
    @classmethod
    def _utc(cls, value: dt.datetime) -> dt.datetime:
        if value.utcoffset() != dt.timedelta(0):
            raise ValueError("retrieval time must be timezone-aware UTC")
        return value

    @property
    def granted(self) -> bool:
        return self.status in GRANTED


class CitableSource(BaseModel):
    """A source some expert of the run could cite, as the console shows it."""

    source_id: str
    title: str
    kind: str
    text: str


class PlannerTrace(BaseModel):
    """What the Planner chose and where the router sent work, kept so trajectory metrics can be
    computed offline from ``runs/``. Router decisions and their probabilities are in
    ``RunResult.decisions``."""

    keys: list[str] = Field(
        default_factory=list,
        description="Distinct focus keys after restriction and caps, in the Planner's order.",
    )
    areas: list[list[str]] = Field(
        default_factory=list, description="The provision keys of each focus area."
    )
    notes: list[str] = Field(default_factory=list, description="Explore cap notes, if any.")
    dispatched: list[str] = Field(
        default_factory=list, description="Experts the router actually sent work to."
    )


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
    synthesis_error: str | None = None
    retrievals: list[RetrievalRecord] = Field(
        default_factory=list,
        description="Every expert's Layer 1 retrieval records, in expert order, then request "
        "order.",
    )
    citable_sources: list[CitableSource] | None = Field(
        default=None,
        description="The union of every expert's citable sources (retrieved texts, obligation "
        "views and memorandum), in expert order, each once. None on results saved before it "
        "existed.",
    )
    planner: PlannerTrace | None = Field(
        default=None,
        description="The Planner's focus and the router's dispatch. None when the Planner did "
        "not run (no changes, planner failure) or on results saved before it existed.",
    )
    trace_run_id: str | None = Field(
        default=None,
        description="LangSmith id of the graph's root run, when the run was traced.",
    )


class RunEvent(BaseModel):
    """Node lifecycle event for live progress (R35): the run page's agent graph reads these."""

    run_id: str
    seq: int
    node: str
    event: str = Field(description="'started' | 'finished' | 'failed'")
    payload: dict = Field(default_factory=dict)
    at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
