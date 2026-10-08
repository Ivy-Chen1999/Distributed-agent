"""Version ids under the U2 additions: ``router_gloss`` and builds from in-memory prompt texts.

Every committed file's id is pinned in ``test_system_version_scope.py``; these tests add that
the new field and the in-memory build keep those ids."""

import pytest

from womm.config import REPO_ROOT
from womm.models.system_version import (
    build_system_version,
    build_system_version_from_texts,
    load_system_version,
)

from .test_system_version_scope import NEW_PINNED_IDS, PINNED_IDS, SV_DIR

ALL_PINNED = {**PINNED_IDS, **NEW_PINNED_IDS}


@pytest.mark.parametrize("name", sorted(ALL_PINNED))
def test_in_memory_build_gives_the_file_id(name):
    sv = load_system_version(SV_DIR / name, REPO_ROOT)
    assert sv.version_id == ALL_PINNED[name]
    again = build_system_version_from_texts(sv.spec, dict(sv.prompts))
    assert again.version_id == sv.version_id and again.prompt_hashes == sv.prompt_hashes


def test_router_gloss_is_dropped_when_unset_and_hashed_when_set():
    sv = load_system_version(SV_DIR / "v1.0-unscoped.yaml", REPO_ROOT)
    assert all("router_gloss" not in e for e in sv.spec.canonical_dump()["experts"])
    data = sv.spec.model_dump()
    data["experts"][0]["router_gloss"] = "duties and powers"
    glossed = build_system_version(type(sv.spec).model_validate(data), REPO_ROOT)
    assert glossed.version_id != sv.version_id


def test_cost_role_is_dropped_when_unset_and_every_existing_id_holds():
    for name, pinned in ALL_PINNED.items():
        sv = load_system_version(SV_DIR / name, REPO_ROOT)
        assert sv.spec.cost is None and "cost" not in sv.spec.canonical_dump()
        assert "cost" not in sv.spec.roles()
        assert sv.version_id == pinned


def test_cost_versions_are_the_unscoped_base_plus_a_cost_role():
    for cost_file, base_file in (
        ("v1.0-cost.yaml", "v1.0-unscoped.yaml"),
        ("v1.0-cost-api.yaml", "v1.0-unscoped-api.yaml"),
    ):
        sv = load_system_version(SV_DIR / cost_file, REPO_ROOT)
        base = load_system_version(SV_DIR / base_file, REPO_ROOT)
        assert sv.version_id not in ALL_PINNED.values()
        assert sv.spec.cost is not None and sv.spec.cost.prompt == "prompts/v1/cost.md"
        assert sv.spec.roles()["cost"] == sv.spec.cost
        assert "prompts/v1/cost.md" in sv.prompt_hashes
        got, want = sv.spec.model_dump(), base.spec.model_dump()
        for spec in (got, want):
            spec.pop("name"), spec.pop("description")
        assert got.pop("cost") is not None and want.pop("cost") is None
        assert got == want
        assert sv.spec.cost.backend == base.spec.planner.backend
        assert sv.spec.cost.model == base.spec.planner.model


def test_derive_and_apply_diff_carry_the_cost_role_unchanged():
    from womm.evolve.edits import apply_diff, validate_diff
    from womm.models.system_version import derive_system_version

    sv = load_system_version(SV_DIR / "v1.0-cost.yaml", REPO_ROOT)
    fake = derive_system_version(sv, REPO_ROOT, backends=dict.fromkeys(sv.spec.roles(), "fake"))
    assert fake.spec.cost.backend == "fake" and fake.spec.cost.prompt == sv.spec.cost.prompt
    text = sv.prompt_text(sv.spec.experts[1].role) + "\nBe precise about recurrence."
    diff = validate_diff(sv, [{"op": "edit_prompt", "role": "expert:fiscal", "new_text": text}])
    child_spec, prompts = apply_diff(sv, diff)
    assert child_spec.cost == sv.spec.cost
    assert prompts[sv.spec.cost.prompt] == sv.prompt_text(sv.spec.cost)


def test_the_cost_prompt_is_outside_the_editable_surface():
    from womm.evolve.edits import EditRejected, role_prompts, validate_diff

    sv = load_system_version(SV_DIR / "v1.0-cost.yaml", REPO_ROOT)
    text = sv.prompt_text(sv.spec.cost) + "\nUse higher bands."
    with pytest.raises(EditRejected, match="non-editable role 'cost'"):
        validate_diff(sv, [{"op": "edit_prompt", "role": "cost", "new_text": text}])
    assert "cost" not in role_prompts(sv)


def test_in_memory_build_needs_every_prompt():
    sv = load_system_version(SV_DIR / "v1.0-unscoped.yaml", REPO_ROOT)
    prompts = dict(sv.prompts)
    prompts.pop(sv.spec.judge.prompt)
    with pytest.raises(KeyError, match="judge_coverage"):
        build_system_version_from_texts(sv.spec, prompts)
