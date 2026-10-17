"""Code identity recorded next to the SystemVersion hash (git sha, dirty tree, CLI version)."""

from __future__ import annotations

import hashlib
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
    dirty = bool(status) if status is not None else True
    diff = _git("diff", "HEAD") if dirty and status is not None else None
    return CodeIdentity(
        git_sha=sha, dirty=dirty, claude_cli_version=claude_cli_version,
        diff_sha=hashlib.sha256(diff.encode()).hexdigest()[:12] if diff is not None else None,
    )  # fmt: skip
