from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT
from womm.models.system_version import load_system_version


def test_baseline_version_loads_and_references_existing_prompts():
    sv = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT)
    assert sv.version_id.startswith("sv_")
    assert sv.spec.router.mode == "shadow"
    assert {e.id for e in sv.spec.experts} == {"legal", "fiscal", "stakeholder"}
