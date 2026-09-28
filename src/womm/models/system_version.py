"""SystemVersion (R14a): an immutable, content-addressed description of how the system runs.

version_id hashes the canonicalised YAML spec plus the contents of every referenced prompt, so
editing a prompt yields a new version. Code changes are tracked separately via CodeIdentity.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from womm.models.base import StrictModel
from womm.models.decisions import Decider, DecisionMode

Backend = Literal["claude_code", "api", "fake"]


class RoleConfig(StrictModel):
    backend: Backend
    model: str = Field(description="Full model id, not an alias, for reproducibility.")
    prompt: str = Field(description="Prompt file path relative to the repo root.")
    timeout_s: float = 240.0
    max_retries: int = Field(default=2, ge=0)


class ExpertConfig(StrictModel):
    id: str
    domain: str
    role: RoleConfig


class RouterConfig(StrictModel):
    mode: DecisionMode = "shadow"
    decider: Decider = "stub"
    timeout_s: float = 5.0


class SystemVersionSpec(StrictModel):
    name: str
    description: str = ""
    planner: RoleConfig
    synthesis: RoleConfig
    judge: RoleConfig
    experts: list[ExpertConfig] = Field(min_length=1)
    router: RouterConfig = Field(default_factory=RouterConfig)
    max_parallel_llm_calls: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _unique_experts(self) -> SystemVersionSpec:
        ids = [e.id for e in self.experts]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate expert ids: {ids}")
        return self

    def roles(self) -> dict[str, RoleConfig]:
        out = {"planner": self.planner, "synthesis": self.synthesis, "judge": self.judge}
        out.update({f"expert:{e.id}": e.role for e in self.experts})
        return out


class SystemVersion(BaseModel):
    spec: SystemVersionSpec
    version_id: str
    prompt_hashes: dict[str, str]
    prompts: dict[str, str] = Field(
        default_factory=dict,
        description="Prompt texts snapshotted at load time, so edits during a long run cannot "
        "make the prompts actually sent diverge from version_id.",
    )
    source_path: str | None = None

    def prompt_text(self, role: RoleConfig) -> str:
        try:
            return self.prompts[role.prompt]
        except KeyError:
            raise KeyError(f"prompt {role.prompt!r} is not part of {self.version_id}") from None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_system_version(
    spec: SystemVersionSpec, repo_root: Path, source_path: str | None = None
) -> SystemVersion:
    prompt_hashes: dict[str, str] = {}
    prompts: dict[str, str] = {}
    for role in spec.roles().values():
        path = repo_root / role.prompt
        if not path.is_file():
            raise FileNotFoundError(f"prompt file not found: {role.prompt}")
        data = path.read_bytes()
        prompt_hashes[role.prompt] = _sha(data)[:16]
        prompts[role.prompt] = data.decode("utf-8")

    canonical = json.dumps(
        {"spec": spec.model_dump(mode="json"), "prompts": dict(sorted(prompt_hashes.items()))},
        sort_keys=True,
        separators=(",", ":"),
    )
    return SystemVersion(
        spec=spec,
        version_id=f"sv_{_sha(canonical.encode())[:12]}",
        prompt_hashes=prompt_hashes,
        prompts=prompts,
        source_path=source_path,
    )


def load_system_version(path: Path, repo_root: Path) -> SystemVersion:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec = SystemVersionSpec.model_validate(data)
    try:
        rel = str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        rel = str(path)
    return build_system_version(spec, repo_root, source_path=rel)
