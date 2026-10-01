"""Code identity recorded next to the SystemVersion hash (git sha, dirty tree, CLI version)."""

from __future__ import annotations

import subprocess

from womm.config import REPO_ROOT
from womm.models.run import CodeIdentity


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def code_identity(claude_cli_version: str | None = None) -> CodeIdentity:
    sha = _git("rev-parse", "--short=12", "HEAD")
    status = _git("status", "--porcelain", "--untracked-files=no")
    return CodeIdentity(
        git_sha=sha, dirty=bool(status) if status is not None else True,
        claude_cli_version=claude_cli_version,
    )  # fmt: skip
