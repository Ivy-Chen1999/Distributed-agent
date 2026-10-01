import pytest

from womm.backends import prepare_backends
from womm.config import REPO_ROOT, load_settings
from womm.llm.base import LLMError
from womm.models.system_version import load_system_version

API_SV = load_system_version(REPO_ROOT / "system_versions/v0.1-api.yaml", REPO_ROOT)


async def test_api_backend_requires_provider_key():
    with pytest.raises(LLMError, match="OPENAI_API_KEY") as exc:
        await prepare_backends(API_SV, settings=load_settings({}))
    assert exc.value.error_kind == "auth"


async def test_api_backend_ready_with_key():
    backends, _, report = await prepare_backends(
        API_SV, settings=load_settings({"OPENAI_API_KEY": "sk-test"})
    )
    assert set(backends) == {"api"} and report is None
