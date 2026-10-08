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


def test_in_memory_build_needs_every_prompt():
    sv = load_system_version(SV_DIR / "v1.0-unscoped.yaml", REPO_ROOT)
    prompts = dict(sv.prompts)
    prompts.pop(sv.spec.judge.prompt)
    with pytest.raises(KeyError, match="judge_coverage"):
        build_system_version_from_texts(sv.spec, prompts)
