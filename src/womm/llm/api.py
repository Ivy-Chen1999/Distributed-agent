"""`api` backend: LangChain `init_chat_model(...).with_structured_output(schema)`.

The model id comes from RoleConfig.model; either "provider:model" (e.g. "openai:gpt-5.1") or a
bare model id with `model_provider` given to the constructor. Cost is recorded at list price
for the models in `PRICES_PER_MTOK`.
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


def _default_factory(model: str, model_provider: str | None, **kwargs: Any) -> Any:
    from langchain.chat_models import init_chat_model

    return init_chat_model(model, model_provider=model_provider, **kwargs)


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


# Official list prices in USD per 1M tokens (input, cached input, output), read 2026-10-14. Output
# tokens include reasoning tokens. A model not listed records no cost, so a USD cap refuses it.
PRICES_PER_MTOK: dict[str, tuple[float, float, float]] = {
    "openai:gpt-6.1-sol": (2.0, 0.10, 10.0),
    "openai:gpt-6-luna": (0.10, 0.01, 0.50),
    "openai:gpt-6-astra": (10.0, 1.0, 50.0),
    "openai:gpt-5.5": (5.0, 0.50, 30.0),
    "openai:gpt-5.6-terra": (2.0, 0.20, 12.0),
}


def usage_from_message(msg: Any) -> tuple[int, int, int]:
    """(input tokens, of which cached, output tokens) from a LangChain message."""
    meta = getattr(msg, "usage_metadata", None) or {}
    cached = (meta.get("input_token_details") or {}).get("cache_read") or 0
    return int(meta.get("input_tokens") or 0), int(cached), int(meta.get("output_tokens") or 0)


def list_price_usd(model: str, input_tokens: int, cached: int, output_tokens: int) -> float | None:
    price = PRICES_PER_MTOK.get(model)
    if price is None:
        return None
    p_in, p_cached, p_out = price
    cached = min(cached, input_tokens)
    return ((input_tokens - cached) * p_in + cached * p_cached + output_tokens * p_out) / 1e6


class ApiBackend(LLMBackend):
    name = "api"

    def __init__(
        self,
        settings: Settings | None = None,
        model_provider: str | None = None,
        model_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.settings = settings
        self.model_provider = model_provider
        self._factory = model_factory or _default_factory
        self._models: dict[tuple[str, str | None, str], Any] = {}

    def _structured(self, role_cfg: RoleConfig, schema: type[BaseModel]) -> Any:
        model, effort = role_cfg.model, role_cfg.reasoning_effort
        key = (model, effort, f"{schema.__module__}.{schema.__qualname__}")
        if key not in self._models:
            kwargs = {} if effort is None else {"reasoning_effort": effort}
            chat = self._factory(model, self.model_provider, **kwargs)
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
        runnable = self._structured(role_cfg, schema)
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
        inp, cached, outp = usage_from_message(out.get("raw") if isinstance(out, dict) else None)
        usage = CallUsage(
            role=role_name,
            agent=agent,
            backend=self.name,
            model=role_cfg.model,
            input_tokens=inp,
            output_tokens=outp,
            cost_usd=list_price_usd(role_cfg.model, inp, cached, outp),
            latency_s=time.monotonic() - start,
        )
        if not isinstance(out, dict):  # include_raw=False style runnable
            return out, usage
        if out.get("parsing_error") is not None or out.get("parsed") is None:
            raise OutputInvalid(
                f"structured output parsing failed: {out.get('parsing_error')}", usage=usage
            )
        return out["parsed"], usage
