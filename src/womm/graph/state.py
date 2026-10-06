"""Graph state and runtime context for the RIA pipeline (F1)."""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, TypedDict

from womm.citations import ValidationOutcome
from womm.data.corpus import Corpus, load_default_corpus
from womm.data.fixtures import Fixture
from womm.decisions.service import DecisionService
from womm.diff import RegulatoryDiff
from womm.llm.base import LLMBackend
from womm.models.decisions import DecisionRecord
from womm.models.dossier import ImpactDossier, LawVersion, SynthesisPlan
from womm.models.findings import ExpertFailure, ImpactFinding
from womm.models.regulation import Source
from womm.models.run import CallUsage, RetrievalRecord
from womm.models.system_version import RoleConfig, SystemVersion


def merge_slots[V](left: dict[str, V] | None, right: dict[str, V | None] | None) -> dict[str, V]:
    """Per-key replace: a later write for the same key overwrites (so a retried expert node
    replaces its earlier output instead of appending); a None value clears the key."""
    out = dict(left or {})
    for key, value in (right or {}).items():
        if value is None:
            out.pop(key, None)
        else:
            out[key] = value
    return out


class RIAState(TypedDict, total=False):
    run_id: str
    scenario_id: str
    mode: str  # "preset" or "explore" (Scenario.mode); absent means preset
    law_version: LawVersion  # dossier header metadata: the version the run analyses
    diff: RegulatoryDiff
    sources: dict[str, Source]  # the union of every expert's text sources, for display
    scenario_keys: list[str]  # preset only: the scenario's provision keys (expert requests)
    index_header: str  # explore only: what the index covers (law version, comparison, counts)
    index_lines: dict[str, str]  # explore only: provision key -> corpus index line, diff order
    planner_notes: list[str]  # explore only: keys dropped by the caps, for the dossier
    focus: Any  # FocusPlan; typed loosely to avoid an import cycle with planner.py
    decisions: Annotated[list[DecisionRecord], operator.add]
    dispatched: list[str]  # experts the router actually sent work to
    board: Annotated[dict[str, list[ImpactFinding]], merge_slots]
    failures: Annotated[dict[str, ExpertFailure], merge_slots]
    usage: Annotated[list[CallUsage], operator.add]
    # Per expert, written on every return path (success, LLMError, unexpected exception). A
    # retried expert node replaces its slot. `retrieved` holds what the expert may cite: its
    # retrieved sources plus the memorandum sources it was given; `retrievals` its records.
    retrieved: Annotated[dict[str, dict[str, Source]], merge_slots]
    retrievals: Annotated[dict[str, list[RetrievalRecord]], merge_slots]
    validation: ValidationOutcome
    synthesis: SynthesisPlan | None
    synthesis_error: str | None
    fatal_error: str | None
    dossier: ImpactDossier


@dataclass
class WommContext:
    """Runtime dependencies injected via LangGraph's context_schema."""

    sv: SystemVersion
    backends: dict[str, LLMBackend]
    decisions: DecisionService
    repo_root: Path
    fixture: Fixture | None = None  # preset text store
    corpus: Corpus | None = None  # explore text store, and obligation views in every mode
    extra: dict[str, Any] = field(default_factory=dict)

    def provision_corpus(self) -> Corpus:
        """The run's corpus, loading the committed one on first use."""
        if self.corpus is None:
            self.corpus = load_default_corpus()
        return self.corpus

    def backend_for(self, role: RoleConfig) -> LLMBackend:
        try:
            return self.backends[role.backend]
        except KeyError:
            raise RuntimeError(
                f"backend {role.backend!r} is not configured for this run "
                f"(available: {sorted(self.backends)})"
            ) from None

    def prompt(self, role: RoleConfig) -> str:
        return self.sv.prompt_text(role)
