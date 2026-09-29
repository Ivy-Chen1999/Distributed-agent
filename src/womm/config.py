"""Runtime configuration loaded from environment variables.

WOMM's own tracing uses LANGSMITH_* and is deliberately separate from the
CC_LANGSMITH_* variables that Claude Code's tracing plugin reads.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SYSTEM_VERSION = REPO_ROOT / "system_versions" / "v0-baseline.yaml"


class ConfigError(RuntimeError):
    """Raised when a required setting is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    system_version_path: Path
    langsmith_api_key: str | None
    langsmith_project: str
    database_url: str | None
    openai_api_key: str | None
    anthropic_api_key: str | None
    api_token: str | None
    typesafe_api_key: str | None = None
    jev_model: str = "jev-latest"

    def require_langsmith(self) -> str:
        if not self.langsmith_api_key:
            raise ConfigError(
                "LANGSMITH_API_KEY is not set. WOMM traces to its own LangSmith project; "
                "set LANGSMITH_API_KEY (and optionally LANGSMITH_PROJECT) — the CC_LANGSMITH_* "
                "variables belong to Claude Code's own tracing and are not used."
            )
        return self.langsmith_api_key


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env

    def opt(name: str) -> str | None:
        value = env.get(name, "").strip()
        return value or None

    raw_path = opt("WOMM_SYSTEM_VERSION")
    path = Path(raw_path) if raw_path else DEFAULT_SYSTEM_VERSION
    if not path.is_absolute():
        path = REPO_ROOT / path

    return Settings(
        system_version_path=path,
        langsmith_api_key=opt("LANGSMITH_API_KEY"),
        langsmith_project=opt("LANGSMITH_PROJECT") or "womm-dev",
        database_url=opt("DATABASE_URL"),
        openai_api_key=opt("OPENAI_API_KEY"),
        anthropic_api_key=opt("ANTHROPIC_API_KEY"),
        api_token=opt("WOMM_API_TOKEN"),
        typesafe_api_key=opt("TYPESAFE_API_KEY") or opt("JEV_API_KEY"),
        jev_model=opt("WOMM_JEV_MODEL") or "jev-latest",
    )
