"""`api` backend: LangChain `init_chat_model(...).with_structured_output(schema)`.

The model id comes from RoleConfig.model; either "provider:model" (e.g. "openai:gpt-5.1") or a
bare model id with `model_provider` given to the constructor. Not live-tested in v0 (no API key).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from womm.llm.base import LLMBackend, LLMError, OutputInvalid
from womm.models.findings import ErrorKind
from womm.models.run import CallUsage
from womm.models.system_version import RoleConfig

if TYPE_CHECKING:
    from womm.config import Settings


def _default_factory(model: str, model_provider: str | None) -> Any:
    from langchain.chat_models import init_chat_model

    return init_chat_model(model, model_provider=model_provider)


def classify_exception(exc: BaseException) -> ErrorKind:
    status = getattr(exc, "status_code", None) or getattr(
        getattr(exc, "response", None), "status_code", None
    )
    name = type(exc).__name__
    if status in (401, 403) or "Authentication" in name or "PermissionDenied" in name:
        return "auth"
    if status == 429 or "RateLimit" in name:
        return "rate_limit"
    if isinstance(exc, TimeoutError) or "Timeout" in name:
        return "timeout"
    return "process_error"


def usage_from_message(msg: Any) -> tuple[int, int]:
    meta = getattr(msg, "usage_metadata", None) or {}
    return int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0)


class ApiBackend(LLMBackend):
    name = "api"

    def __init__(
        self,
        settings: Settings | None = None,
        model_provider: str | None = None,
        model_factory: Callable[[str, str | None], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.model_provider = model_provider
        self._factory = model_factory or _default_factory
        self._models: dict[tuple[str, str], Any] = {}

    def _structured(self, model: str, schema: type[BaseModel]) -> Any:
        key = (model, f"{schema.__module__}.{schema.__qualname__}")
        if key not in self._models:
            chat = self._factory(model, self.model_provider)
            self._models[key] = chat.with_structured_output(schema, include_raw=True)
        return self._models[key]

    async def _invoke(
        self,
        role_name: str,
        system_prompt: str,
        user_content: str,
        schema: type[BaseModel],
        role_cfg: RoleConfig,
        agent: str | None,
    ) -> tuple[Any, CallUsage]:
        runnable = self._structured(role_cfg.model, schema)
        messages = [("system", system_prompt), ("human", user_content)]
        start = time.monotonic()
        try:
            out = await asyncio.wait_for(runnable.ainvoke(messages), timeout=role_cfg.timeout_s)
        except TimeoutError as exc:
            raise LLMError("timeout", f"api call exceeded {role_cfg.timeout_s:.0f}s") from exc
        except LLMError:
            raise
        except Exception as exc:  # provider SDK errors
            raise LLMError(classify_exception(exc), f"{type(exc).__name__}: {exc}") from exc
        inp, outp = usage_from_message(out.get("raw") if isinstance(out, dict) else None)
        usage = CallUsage(
            role=role_name,
            agent=agent,
            backend=self.name,
            model=role_cfg.model,
            input_tokens=inp,
            output_tokens=outp,
            latency_s=time.monotonic() - start,
        )
        if not isinstance(out, dict):  # include_raw=False style runnable
            return out, usage
        if out.get("parsing_error") is not None or out.get("parsed") is None:
            raise OutputInvalid(
                f"structured output parsing failed: {out.get('parsing_error')}", usage=usage
            )
        return out["parsed"], usage
