"""Construct the LLM backends a SystemVersion needs (shared by the CLI and the API)."""

from __future__ import annotations

import sys

from womm.config import load_settings
from womm.llm.base import LLMBackend, get_backend
from womm.llm.claude_code import ClaudeCodeBackend, SelfCheckReport
from womm.models.system_version import SystemVersion


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


async def prepare_backends(
    sv: SystemVersion, *, skip_self_check: bool = False
) -> tuple[dict[str, LLMBackend], str | None, SelfCheckReport | None]:
    """Instantiate each backend the version uses. claude_code must pass its isolation
    self-check first (fail closed)."""
    settings = load_settings()
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
                report = await cc.self_check(model)
                info(f"claude_code isolation self-check passed ({report.cli_version})")
            backends[name] = cc
        else:
            backends[name] = get_backend(name, settings)
    return backends, cli_version, report
