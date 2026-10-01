"""Pick the DecisionService a SystemVersion asks for (router.decider)."""

from __future__ import annotations

from womm.config import ConfigError, Settings
from womm.decisions.jev import JevDecisionService
from womm.decisions.service import DecisionService
from womm.decisions.stub import StubDecisionService
from womm.models.system_version import SystemVersion


def make_decision_service(sv: SystemVersion, settings: Settings) -> DecisionService:
    decider = sv.spec.router.decider
    if decider == "stub":
        return StubDecisionService()
    if decider == "jev":
        if not settings.typesafe_api_key:
            raise ConfigError(
                f"system version {sv.spec.name} ({sv.version_id}) uses the Jev decider but "
                "TYPESAFE_API_KEY is not set. Set it, or use a system version with "
                "router.decider: stub."
            )
        return JevDecisionService(settings.typesafe_api_key, model=settings.jev_model)
    raise ConfigError(f"unknown router decider: {decider!r}")
