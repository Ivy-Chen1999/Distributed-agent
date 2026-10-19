from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT
from womm.models.system_version import load_system_version


def test_baseline_version_loads_and_references_existing_prompts():
    sv = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT)
    assert sv.version_id.startswith("sv_")
    assert sv.spec.router.mode == "shadow"
    assert {e.id for e in sv.spec.experts} == {"legal", "fiscal", "stakeholder"}


V1_SCOPED = REPO_ROOT / "system_versions" / "v1.0-scoped.yaml"
V1_UNSCOPED = REPO_ROOT / "system_versions" / "v1.0-unscoped.yaml"


def _without(data: dict, *keys: str) -> dict:
    return {k: v for k, v in data.items() if k not in keys}


def test_v1_scoped_and_unscoped_load_with_distinct_ids():
    scoped = load_system_version(V1_SCOPED, REPO_ROOT)
    unscoped = load_system_version(V1_UNSCOPED, REPO_ROOT)
    v03 = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT)
    assert len({scoped.version_id, unscoped.version_id, v03.version_id}) == 3


def test_v1_unscoped_is_scoped_minus_every_scope():
    """The control arm isolates data separation: same prompts, explore prompt, retrieval cap,
    router and experts; only the scopes (and the name and description) differ."""
    scoped = load_system_version(V1_SCOPED, REPO_ROOT).spec.model_dump()
    unscoped = load_system_version(V1_UNSCOPED, REPO_ROOT).spec.model_dump()
    assert all(e["scope"] is not None for e in scoped["experts"])
    assert all(e["scope"] is None for e in unscoped["experts"])
    assert unscoped["planner"]["explore_prompt"] == scoped["planner"]["explore_prompt"]
    assert unscoped["retrieval"] == scoped["retrieval"] is not None
    scoped["experts"] = [_without(e, "scope") for e in scoped["experts"]]
    unscoped["experts"] = [_without(e, "scope") for e in unscoped["experts"]]
    assert _without(unscoped, "name", "description") == _without(scoped, "name", "description")


def test_single_agent_version_is_deferred():
    assert not (REPO_ROOT / "system_versions" / "v1.0-single.yaml").exists()
