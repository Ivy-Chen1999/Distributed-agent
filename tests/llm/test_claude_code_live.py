"""Live test against the real `claude` CLI on the local subscription. Run: pytest -m live."""

from __future__ import annotations

import json
import shutil
import time

import pytest
from pydantic import BaseModel

from womm.llm.claude_code import ClaudeCodeBackend
from womm.models.system_version import RoleConfig

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not installed"),
]

MODEL = "claude-haiku-4-5-20251001"


class Capital(BaseModel):
    country: str
    capital: str


async def test_live_tiny_call_and_isolation_self_check():
    backend = ClaudeCodeBackend(max_concurrency=1)
    report = await backend.self_check(MODEL)
    print("\nself_check:", json.dumps(report.__dict__, indent=1, default=str))
    assert report.passed, report.problems

    cfg = RoleConfig(backend="claude_code", model=MODEL, prompt="-", timeout_s=120)
    t = time.monotonic()
    obj, usage = await backend.call(
        "live",
        "Answer with the requested structured data only.",
        "What is the capital of France?",
        Capital,
        cfg,
    )
    print(f"call: {obj!r} usage={usage.model_dump()} wall={time.monotonic() - t:.2f}s")
    assert obj.capital.lower() == "paris"
    assert usage.input_tokens > 0 and usage.output_tokens > 0
    assert usage.cost_usd is not None
