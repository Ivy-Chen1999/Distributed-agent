"""Improvement Planner proposers (U5, R26): structured edits through WOMM's own LLM backend.

Two calls, each with a fixed Pydantic schema, through ``LLMBackend.call`` (so ``fake``,
``claude_code`` and ``api`` all work and GEPA's own reflection LM, and with it litellm, is never
used):

- ``propose_prompt``: one ``PromptEdit`` for one prompt component, from its reflective records;
- ``propose_expert``: one ``ExpertProposal`` for a persistent unowned failure pattern.

The proposers' prompts and models are fixed config (``evals/evolution.yaml``); they are never
part of an evolved SystemVersion. What a proposal may change is decided by U2's validator, not
here: these functions only shape the request and parse the answer.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import Field

from womm.config import REPO_ROOT
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel
from womm.models.run import CallUsage
from womm.models.system_version import RoleConfig

DEFAULT_CONFIG = REPO_ROOT / "evals" / "evolution.yaml"
ROLE_NAME = "improvement_planner"


class PromptEdit(StrictModel):
    role: str = Field(description="The component edited, exactly as given, e.g. 'expert:fiscal'.")
    new_text: str = Field(description="The complete new prompt text for that component.")
    rationale: str = Field(description="Which failures the edit addresses and how.")


class ExpertProposal(StrictModel):
    id: str = Field(description="New expert id: lowercase letters, digits, underscores.")
    domain: str = Field(description="Short domain name, e.g. 'workforce'.")
    prompt_text: str = Field(description="The complete system prompt of the new expert.")
    router_gloss: str = Field(
        description="One phrase describing the domain, used in the relevance question "
        "'Is <provision> relevant to <gloss>?'."
    )
    rationale: str = Field(description="Why no existing expert covers the target pattern.")
    target_pattern: str = Field(
        description="The pattern key this expert targets, exactly as given (kind/category/owner)."
    )


class Budget(StrictModel):
    max_metric_calls: int = Field(default=60, ge=1, description="Case runs requested by GEPA.")
    max_usd: float | None = Field(default=None, ge=0, description="Replays plus proposer calls.")
    minibatch_size: int = Field(default=2, ge=1)
    train_repetitions: int = Field(default=2, ge=1)
    val_repetitions: int = Field(default=3, ge=1)
    grounding_floor: float = Field(
        default=0.5, ge=0, le=1, description="A case run below this grounding scores 0."
    )
    grounding_tolerance: float = Field(
        default=0.02, ge=0, description="Candidate choice: val grounding may drop this much."
    )
    selection_k: float = Field(
        default=1.0,
        ge=0,
        description="Candidate choice: val coverage must beat the reference by more than k "
        "standard errors of the difference.",
    )
    reflective_chars: int = Field(default=12_000, ge=1_000, description="Per component.")
    min_pattern_proposals: int = Field(
        default=2, ge=2, description="A topology trigger must span this many proposals."
    )


class EvolutionRoles(StrictModel):
    reflect: RoleConfig
    propose_expert: RoleConfig


class EvolutionConfig(StrictModel):
    roles: EvolutionRoles
    budget: Budget = Field(default_factory=Budget)
    prompts: dict[str, str] = Field(default_factory=dict, exclude=True)
    prompt_hashes: dict[str, str] = Field(default_factory=dict)


def load_evolution_config(
    path: Path = DEFAULT_CONFIG, repo_root: Path = REPO_ROOT
) -> EvolutionConfig:
    """The Improvement Planner's roles and budget, with prompt texts snapshotted and hashed."""
    config = EvolutionConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    prompts, hashes = {}, {}
    for role in (config.roles.reflect, config.roles.propose_expert):
        data = (repo_root / role.prompt).read_bytes()
        prompts[role.prompt] = data.decode("utf-8")
        hashes[role.prompt] = hashlib.sha256(data).hexdigest()[:16]
    return config.model_copy(update={"prompts": prompts, "prompt_hashes": hashes})


def _block(title: str, body: str) -> str:
    return f"{title}:\n<<<\n{body}\n>>>"


class Proposer:
    """The Improvement Planner's LLM calls. Usage is accumulated for the cycle budget."""

    def __init__(self, backend: LLMBackend, config: EvolutionConfig) -> None:
        self.backend = backend
        self.config = config
        self.usage: list[CallUsage] = []

    @property
    def spent_usd(self) -> float:
        return sum(u.cost_usd or 0.0 for u in self.usage)

    def provenance(self, kind: str) -> dict:
        role = self.config.roles.reflect if kind == "prompt" else self.config.roles.propose_expert
        return {
            "kind": kind,
            "backend": role.backend,
            "model": role.model,
            "prompt": role.prompt,
            "prompt_hash": self.config.prompt_hashes[role.prompt],
        }

    async def propose_prompt(self, role: str, current_text: str, records: list[dict]) -> PromptEdit:
        cfg = self.config.roles.reflect
        user = "\n\n".join(
            [
                f"Component: {role}",
                _block("Current prompt", current_text),
                _block(
                    "Reflective records (JSON, train failures of this component)",
                    json.dumps(records, indent=1, ensure_ascii=False),
                ),
            ]
        )
        edit, usage = await self.backend.call(
            ROLE_NAME, self.config.prompts[cfg.prompt], user, PromptEdit, cfg, agent=role
        )
        self.usage.append(usage)
        return edit

    async def propose_expert(
        self, pattern: dict, examples: list[dict], experts: list[dict]
    ) -> ExpertProposal:
        cfg = self.config.roles.propose_expert
        user = "\n\n".join(
            [
                f"Target pattern: {pattern_key(pattern)}",
                _block("Pattern (JSON)", json.dumps(pattern, indent=1, ensure_ascii=False)),
                _block(
                    "Missed impacts in this pattern (JSON, train)",
                    json.dumps(examples, indent=1, ensure_ascii=False),
                ),
                _block(
                    "Existing experts (JSON)", json.dumps(experts, indent=1, ensure_ascii=False)
                ),
            ]
        )
        proposal, usage = await self.backend.call(
            ROLE_NAME,
            self.config.prompts[cfg.prompt],
            user,
            ExpertProposal,
            cfg,
            agent="topology",
        )
        self.usage.append(usage)
        return proposal


def pattern_key(pattern: dict) -> str:
    return f"{pattern['kind']}/{pattern['category']}/{pattern['owner']}"
