"""`fake` backend: replays scripted outputs for graph and eval tests.

The script maps a key to an ordered list of steps. A call's key is looked up as
"<role_name>/<agent>", then "<agent>", then "<role_name>"; the n-th call for that key consumes
step n. A step is:

- a Pydantic model instance or dict/JSON string (validated by the shared retry loop, so an
  invalid dict exercises schema retries),
- an Exception instance (raised; use LLMError for classified failures),
- a callable `(system_prompt, user_content) -> step`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from womm.llm.base import LLMBackend
from womm.models.run import CallUsage
from womm.models.system_version import RoleConfig


class FakeScriptExhausted(RuntimeError):
    pass


@dataclass
class FakeCall:
    key: str
    index: int
    role_name: str
    agent: str | None
    system_prompt: str
    user_content: str
    schema: type[BaseModel]


class FakeBackend(LLMBackend):
    name = "fake"

    def __init__(self, script: Mapping[str, Sequence[Any]] | None = None) -> None:
        self.script: dict[str, list[Any]] = {k: list(v) for k, v in (script or {}).items()}
        self._counters: dict[str, int] = defaultdict(int)
        self.calls: list[FakeCall] = []

    def _key(self, role_name: str, agent: str | None) -> str:
        candidates = ([f"{role_name}/{agent}", agent] if agent else []) + [role_name]
        for c in candidates:
            if c in self.script:
                return c
        raise FakeScriptExhausted(
            f"fake backend has no script for role={role_name!r} agent={agent!r} "
            f"(tried keys {candidates}; scripted keys: {sorted(self.script)})"
        )

    async def _invoke(
        self,
        role_name: str,
        system_prompt: str,
        user_content: str,
        schema: type[BaseModel],
        role_cfg: RoleConfig,
        agent: str | None,
    ) -> tuple[Any, CallUsage]:
        key = self._key(role_name, agent)
        index = self._counters[key]
        steps = self.script[key]
        if index >= len(steps):
            raise FakeScriptExhausted(
                f"fake script for {key!r} exhausted: call #{index + 1} but only "
                f"{len(steps)} step(s) scripted"
            )
        self._counters[key] += 1
        self.calls.append(
            FakeCall(key, index, role_name, agent, system_prompt, user_content, schema)
        )
        step = steps[index]
        if callable(step) and not isinstance(step, BaseModel | type):
            step = step(system_prompt, user_content)
        if isinstance(step, BaseException):
            raise step
        usage = CallUsage(role=role_name, agent=agent, backend=self.name, model=role_cfg.model)
        return step, usage
