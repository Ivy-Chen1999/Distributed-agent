"""Stub decider: marks every expert relevant unless overridden. Records decider='stub' so its
records are never mistaken for Jev calibration data."""

from __future__ import annotations

from womm.decisions.service import RELEVANCE_POINT
from womm.models.decisions import DecisionRecord
from womm.models.system_version import ExpertConfig, SystemVersion


class StubDecisionService:
    def __init__(self, overrides: dict[str, tuple[str, float | None]] | None = None):
        self._overrides = overrides or {}

    async def expert_relevance(
        self, experts: list[ExpertConfig], context: str, sv: SystemVersion
    ) -> list[DecisionRecord]:
        summary = context[:500]
        records = []
        for e in experts:
            decision, prob = self._overrides.get(e.id, ("relevant", None))
            records.append(
                DecisionRecord(
                    decision_point=RELEVANCE_POINT,
                    subject=e.id,
                    input_summary=summary,
                    decision=decision,
                    probability=prob,
                    mode=sv.spec.router.mode,
                    decider="stub",
                    system_version=sv.version_id,
                )
            )
        return records
