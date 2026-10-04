"""DataScope, PlannerConfig and RetrievalConfig, and the version-id invariants they must keep."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from womm.config import REPO_ROOT
from womm.models.system_version import (
    DataScope,
    derive_system_version,
    load_system_version,
)

SV_DIR = REPO_ROOT / "system_versions"

# version ids before data scopes existed; PROMOTIONS lineage and LangSmith tags key on them.
PINNED_IDS = {
    "v0-baseline.yaml": "sv_3d8f3a48f955",
    "v0.1-api.yaml": "sv_d4b62f8eabca",
    "v0.1-jev.yaml": "sv_3f5a7b41a35f",
    "v0.2-candidate.yaml": "sv_7ced81aab2d0",
    "v0.3-api.yaml": "sv_052a818610f1",
    "v0.3-candidate.yaml": "sv_c28e04120c37",
}
# Files added together with or after data scopes; their ids are new. They are pinned too, so a
# change to one is deliberate (update the pin with the change).
NEW_FILES = {"v1.0-scoped.yaml"}
NEW_PINNED_IDS = {"v1.0-scoped.yaml": "sv_735b080cf78a"}

ROLE = {"backend": "fake", "model": "fake-1", "prompt": "prompts/p.md"}
SCOPE = {"text": [], "obligations": "full", "hypothesis": "Costs trace to obligation records."}


def _write(tmp: Path, spec: dict, name: str = "v.yaml") -> Path:
    (tmp / "prompts").mkdir(exist_ok=True)
    (tmp / "prompts" / "p.md").write_text("You are a planner.")
    (tmp / "prompts" / "explore.md").write_text("You read an index.")
    path = tmp / name
    path.write_text(yaml.safe_dump(spec, sort_keys=False))
    return path


def _spec(**expert) -> dict:
    return {
        "name": "t",
        "planner": dict(ROLE),
        "synthesis": ROLE,
        "judge": ROLE,
        "experts": [{"id": "fiscal", "domain": "fiscal", "role": ROLE, **expert}],
    }


@pytest.mark.parametrize("name", sorted(PINNED_IDS))
def test_existing_version_ids_are_unchanged(name):
    assert load_system_version(SV_DIR / name, REPO_ROOT).version_id == PINNED_IDS[name]


@pytest.mark.parametrize("name", sorted(NEW_PINNED_IDS))
def test_new_version_ids_are_pinned(name):
    assert load_system_version(SV_DIR / name, REPO_ROOT).version_id == NEW_PINNED_IDS[name]


def test_every_committed_version_is_pinned_or_declared_new():
    names = {p.name for p in SV_DIR.glob("*.yaml")}
    assert names - set(PINNED_IDS) <= NEW_FILES, "pin the id of every pre-scope version"
    assert set(PINNED_IDS) <= names


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (
            {
                "backends": {
                    "planner": "fake",
                    "synthesis": "fake",
                    "judge": "fake",
                    "expert:legal": "fake",
                    "expert:fiscal": "fake",
                    "expert:stakeholder": "fake",
                }
            },
            "sv_6d92c182f54b",
        ),
        ({"backends": {"planner": "api"}}, "sv_fd9e0c7a6a82"),
        ({"router_mode": "active"}, "sv_acbd8c250ca8"),
    ],
)
def test_derived_overrides_of_v03_keep_their_ids(kwargs, expected):
    base = load_system_version(SV_DIR / "v0.3-candidate.yaml", REPO_ROOT)
    assert derive_system_version(base, REPO_ROOT, **kwargs).version_id == expected


def test_scope_without_hypothesis_fails_validation(tmp_path):
    scope = {k: v for k, v in SCOPE.items() if k != "hypothesis"}
    with pytest.raises(ValidationError, match="hypothesis"):
        load_system_version(_write(tmp_path, _spec(scope=scope)), tmp_path)


def test_empty_hypothesis_fails_validation():
    with pytest.raises(ValidationError, match="hypothesis"):
        DataScope.model_validate({**SCOPE, "hypothesis": ""})


@pytest.mark.parametrize(
    "bad",
    [{"text": "some"}, {"obligations": "partial"}, {"text": ["a", "a"]}, {"extra": 1}],
)
def test_malformed_scopes_are_rejected(bad):
    with pytest.raises(ValidationError):
        DataScope.model_validate({**SCOPE, **bad})


def test_scope_defaults_hide_delta_and_memorandum():
    scope = DataScope.model_validate(SCOPE)
    assert (scope.sees_delta, scope.sees_memorandum) == (False, False)
    assert not scope.sees_text("ai_act/art/26")
    assert DataScope.model_validate({**SCOPE, "text": "all"}).sees_text("ai_act/art/26")


def test_scope_changes_the_version_id(tmp_path):
    plain = load_system_version(_write(tmp_path, _spec(), "a.yaml"), tmp_path)
    scoped = load_system_version(_write(tmp_path, _spec(scope=SCOPE), "b.yaml"), tmp_path)
    explicit_none = load_system_version(_write(tmp_path, _spec(scope=None), "c.yaml"), tmp_path)
    assert scoped.version_id != plain.version_id
    assert explicit_none.version_id == plain.version_id


def test_explore_prompt_is_hashed_and_snapshotted(tmp_path):
    spec = _spec()
    plain = load_system_version(_write(tmp_path, spec, "a.yaml"), tmp_path)
    spec["planner"]["explore_prompt"] = "prompts/explore.md"
    sv = load_system_version(_write(tmp_path, spec, "b.yaml"), tmp_path)
    assert sv.version_id != plain.version_id
    assert sv.prompt_file("prompts/explore.md") == "You read an index."
    assert "prompts/explore.md" in sv.prompt_hashes
    (tmp_path / "prompts" / "explore.md").write_text("edited")
    assert load_system_version(tmp_path / "b.yaml", tmp_path).version_id != sv.version_id


def test_missing_explore_prompt_file(tmp_path):
    spec = _spec()
    spec["planner"]["explore_prompt"] = "prompts/missing.md"
    with pytest.raises(FileNotFoundError, match="missing.md"):
        load_system_version(_write(tmp_path, spec), tmp_path)


def test_retrieval_config(tmp_path):
    spec = {**_spec(), "retrieval": {"max_provisions": 8}}
    sv = load_system_version(_write(tmp_path, spec), tmp_path)
    assert sv.spec.retrieval is not None and sv.spec.retrieval.max_provisions == 8
    plain = load_system_version(_write(tmp_path, _spec(), "a.yaml"), tmp_path)
    assert sv.version_id != plain.version_id
    with pytest.raises(ValidationError):
        load_system_version(
            _write(tmp_path, {**_spec(), "retrieval": {"max_provisions": 0}}), tmp_path
        )


def test_derive_keeps_scopes(tmp_path):
    sv = load_system_version(_write(tmp_path, _spec(scope=SCOPE)), tmp_path)
    derived = derive_system_version(sv, tmp_path, backends={"expert:fiscal": "api"})
    assert derived.spec.experts[0].scope == sv.spec.experts[0].scope
    assert derived.version_id != sv.version_id
