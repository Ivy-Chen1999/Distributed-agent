"""Construct the LLM backends a SystemVersion needs (shared by the CLI and the API)."""

from __future__ import annotations

import sys

from langsmith import tracing_context

from womm.config import Settings, load_settings
from womm.llm.base import LLMBackend, LLMError, get_backend
from womm.llm.claude_code import ClaudeCodeBackend, SelfCheckReport
from womm.models.system_version import SystemVersion


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


async def prepare_backends(
    sv: SystemVersion, *, skip_self_check: bool = False, settings: Settings | None = None
) -> tuple[dict[str, LLMBackend], str | None, SelfCheckReport | None]:
    """Instantiate each backend the version uses. claude_code must pass its isolation
    self-check first (fail closed)."""
    settings = settings or load_settings()
    roles = sv.spec.roles().values()
    backends: dict[str, LLMBackend] = {}
    cli_version = None
    report = None
    for name in sorted({r.backend for r in roles}):
        if name == "claude_code":
            cc = ClaudeCodeBackend(max_concurrency=sv.spec.max_parallel_llm_calls)
            cli_version = await cc.cli_version()
            if not skip_self_check:
                model = next(r.model for r in roles if r.backend == "claude_code")
                # The isolation probe is plumbing, not a pipeline step: keep it out of traces.
                with tracing_context(enabled=False):
                    report = await cc.self_check(model)
                info(f"claude_code isolation self-check passed ({report.cli_version})")
            backends[name] = cc
        else:
            if name == "api":
                check_api_keys(sv, settings)
            backends[name] = get_backend(name, settings)
    return backends, cli_version, report


PROVIDER_KEYS = {"openai": "openai_api_key", "anthropic": "anthropic_api_key"}


def check_api_keys(sv: SystemVersion, settings: Settings) -> None:
    """Fail at startup, not at the first LLM call, when a provider key is missing."""
    missing = set()
    for role in sv.spec.roles().values():
        if role.backend != "api" or ":" not in role.model:
            continue
        provider = role.model.split(":", 1)[0]
        attr = PROVIDER_KEYS.get(provider)
        if attr and not getattr(settings, attr):
            missing.add(attr.upper())
    if missing:
        raise LLMError("auth", f"api backend needs {', '.join(sorted(missing))}")
