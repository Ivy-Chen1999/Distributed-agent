"""api backend with a fake chat model (no network)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from womm.llm.api import ApiBackend
from womm.llm.base import LLMError
from womm.models.system_version import RoleConfig


class Out(BaseModel):
    value: int


CFG = RoleConfig(backend="api", model="openai:gpt-test", prompt="p.md", timeout_s=1)


class FakeStructured:
    def __init__(self, steps: list[Any]):
        self.steps = steps
        self.inputs: list[Any] = []

    async def ainvoke(self, messages):
        self.inputs.append(messages)
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, float):
            await asyncio.sleep(step)
        return step


class FakeChatModel:
    def __init__(self, steps):
        self.structured = FakeStructured(steps)
        self.schema = None
        self.include_raw = None

    def with_structured_output(self, schema, include_raw=False):
        self.schema, self.include_raw = schema, include_raw
        return self.structured


def make(steps):
    created: dict[str, Any] = {}

    def factory(model, provider):
        created["model"], created["provider"] = model, provider
        created["chat"] = FakeChatModel(steps)
        return created["chat"]

    return ApiBackend(model_factory=factory), created


def raw(parsed=None, error=None, in_tok=12, out_tok=5):
    msg = AIMessage(
        content="",
        usage_metadata={
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        },
    )
    return {"raw": msg, "parsed": parsed, "parsing_error": error}


async def test_structured_output_and_usage_from_raw_message():
    backend, created = make([raw(Out(value=7))])
    obj, usage = await backend.call("planner", "SYS", "USER", Out, CFG, agent=None)
    assert obj.value == 7
    assert (usage.input_tokens, usage.output_tokens) == (12, 5)
    assert usage.backend == "api" and usage.model == "openai:gpt-test"
    chat = created["chat"]
    assert created["model"] == "openai:gpt-test"
    assert chat.schema is Out and chat.include_raw is True
    assert chat.structured.inputs[0] == [("system", "SYS"), ("human", "USER")]


async def test_parsing_error_retries_then_schema_invalid():
    backend, created = make([raw(None, ValueError("bad json"))] * 3)
    with pytest.raises(LLMError) as ei:
        await backend.call("planner", "s", "u", Out, CFG)
    assert ei.value.error_kind == "schema_invalid"
    assert ei.value.attempts == 3
    assert "bad json" in created["chat"].structured.inputs[1][1][1]
    assert ei.value.usage.input_tokens == 36


class StatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class AuthenticationError(Exception):
    pass


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (StatusError(401), "auth"),
        (AuthenticationError("bad key"), "auth"),
        (StatusError(429), "rate_limit"),
        (StatusError(500), "process_error"),
    ],
)
async def test_provider_errors_are_classified_without_retry(exc, kind):
    backend, created = make([exc, raw(Out(value=1))])
    with pytest.raises(LLMError) as ei:
        await backend.call("planner", "s", "u", Out, CFG)
    assert ei.value.error_kind == kind
    assert len(created["chat"].structured.inputs) == 1


async def test_timeout():
    backend, _ = make([5.0])
    with pytest.raises(LLMError) as ei:
        await backend.call("planner", "s", "u", Out, CFG.model_copy(update={"timeout_s": 0.05}))
    assert ei.value.error_kind == "timeout"
