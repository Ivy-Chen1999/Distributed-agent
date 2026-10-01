import pytest

from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT, ConfigError, load_settings
from womm.decisions.factory import make_decision_service
from womm.decisions.jev import JevDecisionService
from womm.decisions.stub import StubDecisionService
from womm.models.system_version import load_system_version

STUB_SV = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT)
JEV_SV = load_system_version(REPO_ROOT / "system_versions" / "v0.1-jev.yaml", REPO_ROOT)


def test_settings_read_typesafe_key():
    s = load_settings({"TYPESAFE_API_KEY": " k "})
    assert s.typesafe_api_key == "k"
    assert s.jev_model == "jev-latest"
    assert load_settings({}).typesafe_api_key is None
    assert load_settings({"WOMM_JEV_MODEL": "jev-x"}).jev_model == "jev-x"


async def test_stub_selected_without_http(monkeypatch):
    import httpx

    def boom(*a, **kw):
        raise AssertionError("no HTTP client may be created for the stub decider")

    monkeypatch.setattr(httpx, "AsyncClient", boom)
    svc = make_decision_service(STUB_SV, load_settings({"TYPESAFE_API_KEY": "k"}))
    assert isinstance(svc, StubDecisionService)
    recs = await svc.expert_relevance(STUB_SV.spec.experts, "ctx", STUB_SV)
    assert {r.decider for r in recs} == {"stub"}


def test_jev_selected_with_key():
    svc = make_decision_service(
        JEV_SV, load_settings({"TYPESAFE_API_KEY": "k", "WOMM_JEV_MODEL": "jev-2"})
    )
    assert isinstance(svc, JevDecisionService)
    assert svc.model == "jev-2"


def test_jev_without_key_raises_clear_error():
    with pytest.raises(ConfigError, match="TYPESAFE_API_KEY"):
        make_decision_service(JEV_SV, load_settings({}))
