from __future__ import annotations

import pytest
from pydantic import BaseModel

from womm.llm import get_backend
from womm.llm.api import ApiBackend
from womm.llm.base import LLMError
from womm.llm.claude_code import ClaudeCodeBackend
from womm.llm.fake import FakeBackend, FakeScriptExhausted
from womm.models.system_version import RoleConfig


class Out(BaseModel):
    value: int


CFG = RoleConfig(backend="fake", model="fake-model", prompt="p.md", max_retries=2)


async def test_replays_in_order_per_key():
    fake = FakeBackend({"planner": [Out(value=1), Out(value=2)]})
    a, usage = await fake.call("planner", "s", "u1", Out, CFG)
    b, _ = await fake.call("planner", "s", "u2", Out, CFG)
    assert (a.value, b.value) == (1, 2)
    assert usage.backend == "fake" and usage.role == "planner"
    assert [c.user_content for c in fake.calls] == ["u1", "u2"]


async def test_key_resolution_prefers_role_agent_then_agent_then_role():
    fake = FakeBackend(
        {"expert/legal": [Out(value=1)], "fiscal": [Out(value=2)], "expert": [Out(value=3)]}
    )
    assert (await fake.call("expert", "s", "u", Out, CFG, agent="legal"))[0].value == 1
    assert (await fake.call("expert", "s", "u", Out, CFG, agent="fiscal"))[0].value == 2
    assert (await fake.call("expert", "s", "u", Out, CFG, agent="stakeholder"))[0].value == 3


async def test_exception_step_is_raised():
    fake = FakeBackend({"expert": [LLMError("timeout", "slow")]})
    with pytest.raises(LLMError) as ei:
        await fake.call("expert", "s", "u", Out, CFG)
    assert ei.value.error_kind == "timeout"


async def test_invalid_dict_goes_through_shared_retry_loop():
    fake = FakeBackend({"planner": [{"value": "nope"}, {"value": 5}]})
    obj, _ = await fake.call("planner", "s", "u", Out, CFG)
    assert obj.value == 5
    assert "did not match" in fake.calls[1].user_content


async def test_invalid_output_exhausts_to_schema_invalid():
    fake = FakeBackend({"planner": [{"bad": 1}] * 3})
    with pytest.raises(LLMError) as ei:
        await fake.call("planner", "s", "u", Out, CFG)
    assert ei.value.error_kind == "schema_invalid"
    assert ei.value.attempts == 3


async def test_callable_step_sees_prompt():
    fake = FakeBackend({"planner": [lambda sp, uc: Out(value=len(uc))]})
    obj, _ = await fake.call("planner", "s", "abcd", Out, CFG)
    assert obj.value == 4


async def test_exhausted_script_gives_clear_error():
    fake = FakeBackend({"planner": [Out(value=1)]})
    await fake.call("planner", "s", "u", Out, CFG)
    with pytest.raises(FakeScriptExhausted, match=r"'planner' exhausted: call #2"):
        await fake.call("planner", "s", "u", Out, CFG)


async def test_unscripted_role_gives_clear_error():
    with pytest.raises(FakeScriptExhausted, match="no script for role='judge'"):
        await FakeBackend({"planner": []}).call("judge", "s", "u", Out, CFG)


def test_get_backend_factory():
    assert isinstance(get_backend("fake", None, script={}), FakeBackend)
    assert isinstance(get_backend("claude_code", None, max_concurrency=2), ClaudeCodeBackend)
    assert isinstance(get_backend("api", None), ApiBackend)
    with pytest.raises(ValueError, match="unknown"):
        get_backend("nope", None)  # type: ignore[arg-type]
