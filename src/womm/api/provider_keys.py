"""Is the LLM provider key still accepted?

Startup only checks that a key is set. A key that expires or is revoked later (2026-11-13) left
/health "ok" while every run failed minutes in. /health, /system and POST /runs ask this check
instead: one free model-list request per provider, cached for ten minutes. Only a 401 or 403
counts as a problem; a network error or a provider outage does not block runs. The key itself
never appears in a message.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx

from womm.backends import PROVIDER_KEYS
from womm.config import Settings
from womm.models.system_version import SystemVersion

TTL_S = 600.0

PROBES: dict[str, tuple[str, Callable[[str], dict[str, str]]]] = {
    "openai": ("https://api.openai.com/v1/models", lambda k: {"Authorization": f"Bearer {k}"}),
    "anthropic": (
        "https://api.anthropic.com/v1/models",
        lambda k: {"x-api-key": k, "anthropic-version": "2023-06-01"},
    ),
}

Probe = Callable[[str, str], Awaitable[int | None]]


async def _probe(provider: str, key: str) -> int | None:
    url, headers = PROBES[provider]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            return (await client.get(url, headers=headers(key))).status_code
    except httpx.HTTPError:
        return None


class KeyCheck:
    def __init__(
        self,
        keys: dict[str, str],
        *,
        probe: Probe = _probe,
        clock: Callable[[], float] = time.monotonic,
        ttl_s: float = TTL_S,
    ) -> None:
        self._keys = keys
        self._probe = probe
        self._clock = clock
        self._ttl_s = ttl_s
        self._checked_at: float | None = None
        self._error: str | None = None
        self._lock = asyncio.Lock()

    @classmethod
    def for_version(cls, sv: SystemVersion, settings: Settings) -> KeyCheck | None:
        """A check for the providers the version's api roles use, or None if it uses none."""
        keys = {}
        for role in sv.spec.roles().values():
            if role.backend != "api" or ":" not in role.model:
                continue
            provider = role.model.split(":", 1)[0]
            key = getattr(settings, PROVIDER_KEYS.get(provider, ""), None)
            if provider in PROBES and key:
                keys[provider] = key
        return cls(keys) if keys else None

    async def error(self) -> str | None:
        async with self._lock:
            now = self._clock()
            if self._checked_at is not None and now - self._checked_at < self._ttl_s:
                return self._error
            problems = []
            for provider, key in sorted(self._keys.items()):
                code = await self._probe(provider, key)
                if code in (401, 403):
                    env = PROVIDER_KEYS[provider].upper()
                    problems.append(f"the {provider} API key was rejected (HTTP {code}); "
                                    f"set a valid {env}")  # fmt: skip
            self._error = "; ".join(problems) or None
            self._checked_at = now
            return self._error
