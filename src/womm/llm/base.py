"""LLM backend interface (R32): system prompt + user content + Pydantic schema -> object + usage.

Graph nodes depend only on this module. Every backend implements `_invoke`, which performs one
attempt and returns raw output; `LLMBackend.call` owns validation and the shared retry loop:

- output that fails Pydantic validation (or that the backend flags via `OutputInvalid`) is
  retried up to `role_cfg.max_retries` times with the validation error fed back to the model;
  after that the call fails with `error_kind="schema_invalid"`;
- every other failure is an `LLMError` that propagates immediately (auth is never retried).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from womm.models.findings import ErrorKind
from womm.models.run import CallUsage
from womm.models.system_version import Backend, RoleConfig

if TYPE_CHECKING:
    from womm.config import Settings


class LLMError(Exception):
    """A classified LLM call failure."""

    def __init__(
        self,
        error_kind: ErrorKind,
        message: str,
        attempts: int = 1,
        usage: CallUsage | None = None,
    ) -> None:
        super().__init__(f"[{error_kind}] {message}")
        self.error_kind: ErrorKind = error_kind
        self.message = message
        self.attempts = attempts
        self.usage = usage  # accumulated usage of failed attempts, when known


class OutputInvalid(Exception):
    """Raised by a backend attempt whose output is not usable structured data (retryable)."""

    def __init__(self, message: str, usage: CallUsage | None = None) -> None:
        super().__init__(message)
        self.usage = usage


RETRY_FEEDBACK = (
    "\n\n---\nYour previous response did not match the required output schema.\n"
    "Validation error:\n{error}\n"
    "Respond again with output that strictly conforms to the schema."
)


def _validate[T: BaseModel](schema: type[T], raw: Any) -> T:
    if isinstance(raw, BaseModel):
        raw = raw.model_dump(mode="json")
    if isinstance(raw, str | bytes):
        return schema.model_validate_json(raw)
    return schema.model_validate(raw)


class LLMBackend(ABC):
    name: Backend

    @abstractmethod
    async def _invoke(
        self,
        role_name: str,
        system_prompt: str,
        user_content: str,
        schema: type[BaseModel],
        role_cfg: RoleConfig,
        agent: str | None,
    ) -> tuple[Any, CallUsage]:
        """One attempt. Returns raw structured output (dict / model / JSON string) and usage."""

    async def call[T: BaseModel](
        self,
        role_name: str,
        system_prompt: str,
        user_content: str,
        schema: type[T],
        role_cfg: RoleConfig,
        agent: str | None = None,
    ) -> tuple[T, CallUsage]:
        max_attempts = role_cfg.max_retries + 1
        total: CallUsage | None = None
        content = user_content
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            try:
                raw, usage = await self._invoke(
                    role_name, system_prompt, content, schema, role_cfg, agent
                )
            except OutputInvalid as exc:
                last_error = str(exc)
                if exc.usage is not None:
                    total = exc.usage if total is None else _add_usage(total, exc.usage)
            except LLMError as exc:
                exc.attempts = attempt
                if total is not None:
                    exc.usage = total if exc.usage is None else _add_usage(total, exc.usage)
                raise
            else:
                total = usage if total is None else _add_usage(total, usage)
                try:
                    return _validate(schema, raw), total
                except ValidationError as exc:
                    last_error = str(exc)
            content = user_content + RETRY_FEEDBACK.format(error=last_error)
        raise LLMError(
            "schema_invalid",
            f"{role_name}: output failed validation after {max_attempts} attempts: {last_error}",
            attempts=max_attempts,
            usage=total,
        )


def _add_usage(a: CallUsage, b: CallUsage) -> CallUsage:
    cost = None if a.cost_usd is None and b.cost_usd is None else (a.cost_usd or 0) + (
        b.cost_usd or 0
    )
    return a.model_copy(
        update={
            "input_tokens": a.input_tokens + b.input_tokens,
            "output_tokens": a.output_tokens + b.output_tokens,
            "cost_usd": cost,
            "latency_s": a.latency_s + b.latency_s,
        }
    )


def get_backend(backend_name: Backend, settings: Settings | None = None, **kwargs: Any):
    """Construct a backend by name. kwargs are passed to the backend constructor."""
    if backend_name == "claude_code":
        from womm.llm.claude_code import ClaudeCodeBackend

        return ClaudeCodeBackend(**kwargs)
    if backend_name == "api":
        from womm.llm.api import ApiBackend

        return ApiBackend(settings=settings, **kwargs)
    if backend_name == "fake":
        from womm.llm.fake import FakeBackend

        return FakeBackend(**kwargs)
    raise ValueError(f"unknown LLM backend: {backend_name!r}")
