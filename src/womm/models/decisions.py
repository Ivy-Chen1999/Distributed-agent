"""Bounded-decision log (R10)."""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

DecisionMode = Literal["off", "shadow", "active"]
Decider = Literal["jev", "stub"]


class DecisionRecord(BaseModel):
    decision_point: str = Field(description="e.g. 'router.relevance'")
    subject: str = Field(description="What the decision is about, e.g. the expert id.")
    input_summary: str
    decision: str = Field(description="e.g. 'relevant', 'not_relevant', 'error'")
    probability: float | None
    mode: DecisionMode
    decider: Decider
    system_version: str
    error: str | None = None
    truncated: bool = False
    created_at: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
