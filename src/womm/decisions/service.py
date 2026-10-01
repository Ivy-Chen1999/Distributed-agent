"""Bounded-decision interface (R10). The router asks one yes/no relevance question per expert."""

from __future__ import annotations

from typing import Protocol

from womm.models.decisions import DecisionRecord
from womm.models.system_version import ExpertConfig, SystemVersion

RELEVANCE_POINT = "router.relevance"


class DecisionService(Protocol):
    async def expert_relevance(
        self, experts: list[ExpertConfig], context: str, sv: SystemVersion
    ) -> list[DecisionRecord]:
        """One DecisionRecord per expert. Must not raise: failures become decision='error'."""
        ...
