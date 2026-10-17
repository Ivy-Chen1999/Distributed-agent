"""Config edit surface (U2, R26): the only way the Improvement Planner changes a SystemVersion.

The Planner never writes YAML. It returns typed operations, validated against an allow-list
before anything is built:

- ``edit_prompt(role, new_text)`` for ``planner``, ``planner:explore``, ``synthesis`` and
  ``expert:<id>``;
- ``add_expert(id, domain, prompt_text, router_gloss)``, inheriting backend, model, timeout and
  retries from the parent's first expert (at most one per candidate);
- ``set_router_gloss(expert_id, text)``, the routing table entry the relevance decider reads;
- ``set_retrieval(max_provisions, max_prompt_chars)`` within fixed bounds.

Anything else (judge edits, backend or model changes, the router mode, data scopes, removing an
expert) is rejected with the op named. A candidate is built in memory, with new prompt texts at
virtual paths under ``prompts/evolved/<parent_id>/``; ``materialize`` writes it as files that
load to the same version_id.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from womm.models.system_version import (
    DEFAULT_RETRIEVAL,
    ExpertConfig,
    RetrievalConfig,
    SystemVersion,
    SystemVersionSpec,
    build_system_version_from_texts,
    load_system_version,
)

EXPERT_ID = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
MIN_PROMPT_CHARS = 200
MAX_PROMPT_FACTOR = 3
GLOSS_CHARS = (5, 500)
MAX_PROVISIONS = (4, 16)
MAX_PROMPT_CHARS = (10_000, 60_000)
EVOLVED_DIR = "prompts/evolved"
CANDIDATES_DIR = "system_versions/candidates"


class EditRejected(ValueError):
    def __init__(self, op: str | None, reason: str) -> None:
        self.op = op
        super().__init__(f"{op or 'diff'}: {reason}")


class _Op(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EditPrompt(_Op):
    op: Literal["edit_prompt"] = "edit_prompt"
    role: str
    new_text: str


class AddExpert(_Op):
    op: Literal["add_expert"] = "add_expert"
    id: str
    domain: str = Field(min_length=1, max_length=60)
    prompt_text: str
    router_gloss: str


class SetRouterGloss(_Op):
    op: Literal["set_router_gloss"] = "set_router_gloss"
    expert_id: str
    text: str


class SetRetrieval(_Op):
    op: Literal["set_retrieval"] = "set_retrieval"
    max_provisions: int
    max_prompt_chars: int


EditOp = Annotated[
    EditPrompt | AddExpert | SetRouterGloss | SetRetrieval, Field(discriminator="op")
]
ALLOWED_OPS = ("edit_prompt", "add_expert", "set_router_gloss", "set_retrieval")
_OP_ADAPTER: TypeAdapter[EditOp] = TypeAdapter(EditOp)


class ConfigDiff(BaseModel):
    """A validated, ordered list of edits against one parent version."""

    model_config = ConfigDict(frozen=True)
    parent_id: str
    ops: tuple[EditOp, ...]

    def ops_json(self) -> list[dict]:
        return [op.model_dump(mode="json") for op in self.ops]

    def digest(self) -> str:
        blob = json.dumps({"parent": self.parent_id, "ops": self.ops_json()}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:8]


# ---------------------------------------------------------------- validation


def _prompt_path(spec: SystemVersionSpec, role: str) -> str | None:
    """The prompt file a role key edits, or None for a role outside the editable surface."""
    if role == "planner":
        return spec.planner.prompt
    if role == "planner:explore":
        return spec.planner.explore_prompt
    if role == "synthesis":
        return spec.synthesis.prompt
    if role.startswith("expert:"):
        eid = role.split(":", 1)[1]
        return next((e.role.prompt for e in spec.experts if e.id == eid), None)
    return None


def _check_prompt(op: str, text: str, reference: str) -> None:
    if len(text) < MIN_PROMPT_CHARS:
        raise EditRejected(op, f"prompt text must have at least {MIN_PROMPT_CHARS} characters")
    limit = MAX_PROMPT_FACTOR * len(reference)
    if len(text) > limit:
        raise EditRejected(
            op, f"prompt text may be at most {MAX_PROMPT_FACTOR}x the parent's ({limit} chars)"
        )


def _parse(raw: Any) -> EditOp:
    if isinstance(raw, _Op):
        raw = raw.model_dump()
    name = raw.get("op") if isinstance(raw, dict) else None
    if name not in ALLOWED_OPS:
        raise EditRejected(str(name), f"op {name!r} is not in the allow-list {list(ALLOWED_OPS)}")
    try:
        return _OP_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        raise EditRejected(name, f"invalid fields: {exc.errors(include_url=False)}") from None


def validate_diff(parent: SystemVersion, raw_ops: list[Any]) -> ConfigDiff:
    """Parse and check ``raw_ops`` (dicts, e.g. an LLM's structured output) against ``parent``.
    Raises ``EditRejected`` naming the first offending op."""
    if not raw_ops:
        raise EditRejected(None, "no change: the diff is empty")
    ops = [_parse(r) for r in raw_ops]
    spec = parent.spec
    expert_ids = {e.id for e in spec.experts}
    targets: set[str] = set()
    adds = [o for o in ops if isinstance(o, AddExpert)]
    if len(adds) > 1:
        raise EditRejected("add_expert", "at most one add_expert per candidate")
    for op in ops:
        if isinstance(op, EditPrompt):
            path = _prompt_path(spec, op.role)
            if path is None:
                raise EditRejected(op.op, f"unknown or non-editable role {op.role!r}")
            target = f"prompt:{op.role}"
            if op.new_text == parent.prompt_file(path):
                raise EditRejected(op.op, f"no change: {op.role}'s prompt text is unchanged")
            _check_prompt(op.op, op.new_text, parent.prompt_file(path))
        elif isinstance(op, AddExpert):
            if not EXPERT_ID.match(op.id):
                raise EditRejected(op.op, f"expert id {op.id!r} must match {EXPERT_ID.pattern}")
            if op.id in expert_ids:
                raise EditRejected(op.op, f"expert {op.id!r} already exists")
            _check_prompt(op.op, op.prompt_text, parent.prompt_text(spec.experts[0].role))
            _check_gloss(op.op, op.router_gloss)
            target = f"expert:{op.id}"
        elif isinstance(op, SetRouterGloss):
            if op.expert_id not in expert_ids:
                raise EditRejected(op.op, f"unknown expert {op.expert_id!r}")
            _check_gloss(op.op, op.text)
            current = next(e for e in spec.experts if e.id == op.expert_id).router_gloss
            if op.text.strip() == current:
                raise EditRejected(op.op, f"no change: {op.expert_id}'s gloss is unchanged")
            target = f"gloss:{op.expert_id}"
        else:
            lo, hi = MAX_PROVISIONS
            if not lo <= op.max_provisions <= hi:
                raise EditRejected(op.op, f"max_provisions must be within {lo}-{hi}")
            lo, hi = MAX_PROMPT_CHARS
            if not lo <= op.max_prompt_chars <= hi:
                raise EditRejected(op.op, f"max_prompt_chars must be within {lo}-{hi}")
            current = spec.retrieval or DEFAULT_RETRIEVAL
            if (op.max_provisions, op.max_prompt_chars) == (
                current.max_provisions, current.max_prompt_chars,
            ):  # fmt: skip
                raise EditRejected(op.op, "no change: the retrieval bounds are unchanged")
            target = "retrieval"
        if target in targets:
            raise EditRejected(op.op, f"{target} is edited twice")
        targets.add(target)
    # Every op above refuses a no-op, so a valid diff always changes the version.
    return ConfigDiff(parent_id=parent.version_id, ops=tuple(ops))


def _check_gloss(op: str, text: str) -> None:
    lo, hi = GLOSS_CHARS
    if not lo <= len(text.strip()) <= hi:
        raise EditRejected(op, f"router gloss must have {lo}-{hi} characters")


# ---------------------------------------------------------------- building


def _slug(role: str) -> str:
    return role.replace(":", "-")


def _evolved_path(parent_id: str, role: str, text: str) -> str:
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{EVOLVED_DIR}/{parent_id}/{_slug(role)}-{sha}.md"


def apply_diff(parent: SystemVersion, diff: ConfigDiff) -> tuple[SystemVersionSpec, dict[str, str]]:
    """The child spec and its prompt texts by path. Changed prompts get virtual paths; the rest
    keep the parent's paths and texts. The diff is validated again here: a ConfigDiff built
    directly, without ``validate_diff``, cannot skip the allow-list and bounds."""
    if diff.parent_id != parent.version_id:
        raise EditRejected(None, f"diff is against {diff.parent_id}, not {parent.version_id}")
    validate_diff(parent, diff.ops_json())
    data = parent.spec.model_dump()
    prompts = dict(parent.prompts)
    for op in diff.ops:
        if isinstance(op, EditPrompt):
            path = _evolved_path(parent.version_id, op.role, op.new_text)
            if op.role == "planner:explore":
                data["planner"]["explore_prompt"] = path
            elif op.role in ("planner", "synthesis"):
                data[op.role]["prompt"] = path
            else:
                eid = op.role.split(":", 1)[1]
                next(e for e in data["experts"] if e["id"] == eid)["role"]["prompt"] = path
            prompts[path] = op.new_text
        elif isinstance(op, AddExpert):
            first = data["experts"][0]["role"]
            path = _evolved_path(parent.version_id, f"expert:{op.id}", op.prompt_text)
            role = {k: first[k] for k in ("backend", "model", "timeout_s", "max_retries")}
            data["experts"].append(ExpertConfig(
                id=op.id, domain=op.domain, role={**role, "prompt": path},
                router_gloss=op.router_gloss.strip(),
            ).model_dump())  # fmt: skip
            prompts[path] = op.prompt_text
        elif isinstance(op, SetRouterGloss):
            next(e for e in data["experts"] if e["id"] == op.expert_id)["router_gloss"] = (
                op.text.strip()
            )
        else:
            data["retrieval"] = RetrievalConfig(
                max_provisions=op.max_provisions, max_prompt_chars=op.max_prompt_chars
            ).model_dump()
    data["name"] = f"evo-{diff.digest()}"
    data["description"] = f"Derived from {parent.version_id} ({parent.spec.name}) by " + ", ".join(
        _describe(op) for op in diff.ops
    )
    spec = SystemVersionSpec.model_validate(data)
    used = set(spec.prompt_paths())
    return spec, {p: t for p, t in prompts.items() if p in used}


def _describe(op: EditOp) -> str:
    if isinstance(op, EditPrompt):
        return f"edit_prompt({op.role})"
    if isinstance(op, AddExpert):
        return f"add_expert({op.id})"
    if isinstance(op, SetRouterGloss):
        return f"set_router_gloss({op.expert_id})"
    return f"set_retrieval({op.max_provisions}, {op.max_prompt_chars})"


def build_candidate(parent: SystemVersion, diff: ConfigDiff) -> SystemVersion:
    """The candidate SystemVersion, in memory: no file is read or written."""
    spec, prompts = apply_diff(parent, diff)
    return build_system_version_from_texts(spec, prompts)


# ---------------------------------------------------------------- rendering and files


def role_prompts(sv: SystemVersion) -> dict[str, str]:
    """The prompt path of every editable role (``edit_prompt`` keys); never the judge's."""
    spec = sv.spec
    out = {"planner": spec.planner.prompt, "synthesis": spec.synthesis.prompt}
    if spec.planner.explore_prompt:
        out["planner:explore"] = spec.planner.explore_prompt
    out.update({f"expert:{e.id}": e.role.prompt for e in spec.experts})
    return out


def render_diff(parent: SystemVersion, child: SystemVersion) -> dict:
    """A structural summary plus one unified diff per changed prompt, for the archive and the
    evolution page."""
    p_roles, c_roles = role_prompts(parent), role_prompts(child)
    p_experts = {e.id: e for e in parent.spec.experts}
    c_experts = {e.id: e for e in child.spec.experts}
    prompts: dict[str, str] = {}
    changed = []
    for role, path in c_roles.items():
        old_path = p_roles.get(role)
        new_text = child.prompt_file(path)
        old_text = parent.prompt_file(old_path) if old_path else None
        if old_text == new_text:
            continue
        if old_path:
            changed.append(role)
        prompts[role] = "".join(difflib.unified_diff(
            (old_text or "").splitlines(keepends=True), new_text.splitlines(keepends=True),
            fromfile=old_path or "/dev/null", tofile=path,
        ))  # fmt: skip
    gloss = {
        eid: {"from": p_experts[eid].router_gloss, "to": e.router_gloss}
        for eid, e in c_experts.items()
        if eid in p_experts and p_experts[eid].router_gloss != e.router_gloss
    }
    p_ret = parent.spec.retrieval or DEFAULT_RETRIEVAL
    c_ret = child.spec.retrieval or DEFAULT_RETRIEVAL
    summary = {
        "experts_added": [eid for eid in c_experts if eid not in p_experts],
        "added_experts": {
            eid: {"domain": e.domain, "router_gloss": e.router_gloss}
            for eid, e in c_experts.items() if eid not in p_experts
        },
        "prompts_changed": changed,
        "router_gloss_changed": gloss,
        "retrieval": None if p_ret == c_ret else {
            "from": p_ret.model_dump(), "to": c_ret.model_dump(),
        },
    }  # fmt: skip
    return {"summary": summary, "prompts": prompts}


def materialize(sv: SystemVersion, repo_root: Path) -> Path:
    """Write ``sv`` as ``system_versions/candidates/<name>.yaml`` plus any prompt file that
    does not exist yet under ``repo_root``, and check that it reloads to the same version_id.
    An existing prompt file with different content is never overwritten."""
    for rel, text in sv.prompts.items():
        path = repo_root / rel
        data = text.encode("utf-8")
        if path.exists():
            if path.read_bytes() != data:
                raise FileExistsError(f"{rel} exists with different content")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    out = repo_root / CANDIDATES_DIR / f"{sv.spec.name}.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        yaml.safe_dump(sv.spec.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    reloaded = load_system_version(out, repo_root)
    if reloaded.version_id != sv.version_id:
        raise RuntimeError(f"materialised {out} loads as {reloaded.version_id}, not "
                           f"{sv.version_id}")  # fmt: skip
    return out
