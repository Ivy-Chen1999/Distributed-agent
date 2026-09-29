from pathlib import Path

import pytest

from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT, ConfigError, load_settings


def test_system_version_path_from_env_resolves_relative_to_repo():
    s = load_settings({"WOMM_SYSTEM_VERSION": "system_versions/other.yaml"})
    assert s.system_version_path == REPO_ROOT / "system_versions" / "other.yaml"


def test_absolute_system_version_path_kept(tmp_path: Path):
    target = tmp_path / "v.yaml"
    assert load_settings({"WOMM_SYSTEM_VERSION": str(target)}).system_version_path == target


def test_defaults_without_database_url():
    s = load_settings({})
    assert s.database_url is None
    assert s.system_version_path == DEFAULT_SYSTEM_VERSION
    assert s.langsmith_project == "womm-dev"


def test_blank_values_treated_as_unset():
    assert load_settings({"DATABASE_URL": "  "}).database_url is None


def test_missing_langsmith_key_gives_readable_error():
    with pytest.raises(ConfigError, match="LANGSMITH_API_KEY"):
        load_settings({"CC_LANGSMITH_API_KEY": "x"}).require_langsmith()


def test_jev_api_key_alias():
    assert load_settings({"JEV_API_KEY": "k"}).typesafe_api_key == "k"
    assert load_settings({"TYPESAFE_API_KEY": "a", "JEV_API_KEY": "b"}).typesafe_api_key == "a"
