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


class PlannerConfig(RoleConfig):
    """The Planner's role. It has no data scope: it always reads the whole index, delta included."""

    explore_prompt: str | None = Field(
        default=None,
        description="Prompt file used in explore mode only; preset runs keep `prompt`.",
    )


class CostConfig(RoleConfig):
    """The cost step (EU cost plan R4): one structured call per batch of obligation records. It
    is not an expert and is not routed; its input view is fixed in code (obligation records in
    the ``full`` view, no provision text, no memorandum, no delta, no findings). It is outside
    the Improvement Planner's editable surface."""

    max_records_per_call: int = Field(default=60, ge=1)


class DataScope(StrictModel):
    """What one expert may see, split by data form (text versus obligation records), not topic.

    Enforced by `womm.retrieval.retrieve`. An expert without a scope sees everything (v0)."""

    text: Literal["all"] | list[str] = Field(
        description="'all', or the provision keys whose full text is visible (empty: none)."
    )
    obligations: Literal["none", "actors", "full"] = Field(
        description="Obligation view for keys without visible text. 'actors' shows addressee, "
        "actors, condition and application date only, plus the verbatim span when the primary "
        "actor is unspecified; 'full' shows every field and the span."
    )
    sees_delta: bool = Field(default=False, description="Whether change kinds are visible.")
    sees_memorandum: bool = Field(
        default=False, description="Whether the stripped explanatory memorandum is in context."
    )
    hypothesis: str = Field(
        min_length=1, description="What this separation is meant to test. Mandatory."
    )

    @model_validator(mode="after")
    def _unique_text_keys(self) -> DataScope:
        if isinstance(self.text, list):
            dupes = sorted({k for k in self.text if self.text.count(k) > 1})
            if dupes:
                raise ValueError(f"duplicate keys in scope text: {dupes}")
            if any(not k for k in self.text):
                raise ValueError("empty provision key in scope text")
        return self

    def sees_text(self, key: str) -> bool:
        return self.text == "all" or key in self.text


class ExpertConfig(StrictModel):
    id: str
    domain: str
    role: RoleConfig
    scope: DataScope | None = Field(
        default=None, description="Data scope; None means everything (v0 behaviour)."
    )
    router_gloss: str | None = Field(
        default=None,
        description="What this expert analyses, as the router's relevance question puts it. "
        "None falls back to the built-in gloss for the domain.",
    )


class RetrievalConfig(StrictModel):
    """Explore-mode bounds. Versions without this block use ``DEFAULT_RETRIEVAL``."""

    max_provisions: int = Field(ge=1, description="Cap on the keys an explore Planner may pick.")
    max_prompt_chars: int = Field(
        default=60_000,
        ge=10_000,
        description="Cap on an explore-mode expert prompt (system prompt plus user content, in "
        "characters). Keys the Planner picked that would push an expert prompt past it are "
        "dropped, in the Planner's order, and the drop is recorded.",
    )


DEFAULT_RETRIEVAL = RetrievalConfig(max_provisions=8)


class RouterConfig(StrictModel):
    mode: DecisionMode = "shadow"
    decider: Decider = "stub"
    timeout_s: float = 5.0


class SystemVersionSpec(StrictModel):
    name: str
    description: str = ""
    planner: PlannerConfig
    synthesis: RoleConfig
    judge: RoleConfig
    experts: list[ExpertConfig] = Field(min_length=1)
    router: RouterConfig = Field(default_factory=RouterConfig)
    max_parallel_llm_calls: int = Field(default=3, ge=1)
    retrieval: RetrievalConfig | None = None
    cost: CostConfig | None = Field(
        default=None, description="The optional cost step; None means the graph has no cost node."
    )

    @model_validator(mode="after")
    def _unique_experts(self) -> SystemVersionSpec:
        ids = [e.id for e in self.experts]
        if len(ids) != len(set(ids)):
            raise ValueError(f"duplicate expert ids: {ids}")
        return self

    def roles(self) -> dict[str, RoleConfig]:
        out = {"planner": self.planner, "synthesis": self.synthesis, "judge": self.judge}
        out.update({f"expert:{e.id}": e.role for e in self.experts})
        if self.cost is not None:
            out["cost"] = self.cost
        return out

    def prompt_paths(self) -> list[str]:
        """Every prompt file the version references, role prompts first."""
        paths = [r.prompt for r in self.roles().values()]
        if self.planner.explore_prompt is not None:
            paths.append(self.planner.explore_prompt)
        return list(dict.fromkeys(paths))

    def canonical_dump(self) -> dict:
        """The spec as hashed into version_id. Fields added after v0 (`scope`, `router_gloss`,
        the Planner's `explore_prompt`, `retrieval`, `cost`) are dropped when None, so versions
        that do not use them keep their ids. Nothing else is dropped: existing defaults are part
        of today's hashes."""
        data = self.model_dump(mode="json")
        for optional in ("retrieval", "cost"):
            if data.get(optional) is None:
                data.pop(optional, None)
        if data["planner"].get("explore_prompt") is None:
            data["planner"].pop("explore_prompt", None)
        for expert in data["experts"]:
            for optional in ("scope", "router_gloss"):
                if expert.get(optional) is None:
                    expert.pop(optional, None)
        return data


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
        return self.prompt_file(role.prompt)

    def prompt_file(self, path: str) -> str:
        try:
            return self.prompts[path]
        except KeyError:
            raise KeyError(f"prompt {path!r} is not part of {self.version_id}") from None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_system_version(
    spec: SystemVersionSpec, repo_root: Path, source_path: str | None = None
) -> SystemVersion:
    texts: dict[str, bytes] = {}
    for rel in spec.prompt_paths():
        path = repo_root / rel
        if not path.is_file():
            raise FileNotFoundError(f"prompt file not found: {rel}")
        texts[rel] = path.read_bytes()
    return _build(spec, texts, source_path)


def build_system_version_from_texts(
    spec: SystemVersionSpec, prompts: dict[str, str], source_path: str | None = None
) -> SystemVersion:
    """A SystemVersion from in-memory prompt texts keyed by (possibly virtual) prompt path. It
    has the same version_id as loading the spec with those texts written as UTF-8 files."""
    missing = [rel for rel in spec.prompt_paths() if rel not in prompts]
    if missing:
        raise KeyError(f"no prompt text for {missing}")
    texts = {rel: prompts[rel].encode("utf-8") for rel in spec.prompt_paths()}
    return _build(spec, texts, source_path)


def _build(spec: SystemVersionSpec, texts: dict[str, bytes], source_path: str | None):
    prompt_hashes = {rel: _sha(data)[:16] for rel, data in texts.items()}
    prompts = {rel: data.decode("utf-8") for rel, data in texts.items()}
    canonical = json.dumps(
        {"spec": spec.canonical_dump(), "prompts": dict(sorted(prompt_hashes.items()))},
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


def derive_system_version(
    base: SystemVersion,
    repo_root: Path,
    *,
    router_mode: DecisionMode | None = None,
    backends: dict[str, Backend] | None = None,
    decider: Decider | None = None,
) -> SystemVersion:
    """A new SystemVersion from `base` with the router mode and/or per-role backends changed.
    Role keys are those of `SystemVersionSpec.roles()` ("planner", "expert:legal", ...). The
    result is content-addressed like any other version, so its version_id differs."""
    data = base.spec.model_dump()
    if router_mode is not None:
        data["router"]["mode"] = router_mode
    if decider is not None:
        data["router"]["decider"] = decider
    for role, backend in (backends or {}).items():
        if role in ("planner", "synthesis", "judge") or (role == "cost" and data.get("cost")):
            data[role]["backend"] = backend
        elif role.startswith("expert:"):
            eid = role.split(":", 1)[1]
            matches = [e for e in data["experts"] if e["id"] == eid]
            if not matches:
                raise ValueError(f"unknown expert role {role!r}")
            matches[0]["role"]["backend"] = backend
        else:
            raise ValueError(f"unknown role {role!r}")
    if data == base.spec.model_dump():
        return base
    data["name"] = f"{base.spec.name}+overrides"
    # The base's snapshotted prompt texts, so a candidate whose prompts exist only in memory
    # (womm.evolve) can be derived too; for a file-backed base they equal the files.
    return build_system_version_from_texts(
        SystemVersionSpec.model_validate(data), base.prompts, source_path=base.source_path
    )
