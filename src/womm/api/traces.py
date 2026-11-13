"""LangSmith trace links for the console's Run detail (system interface only).

A link is the authenticated workspace URL of a run's root trace, built from a fixed host and the
tenant and project ids of the deployment's LangSmith project; never a public share link. The ids
are resolved on the first request that needs them and cached: the production project only
exists after its first traced run, so a failed lookup is retried (at most once a minute) instead
of being remembered. Nothing here raises into a request; a missing link carries a reason.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from womm.tracing import tracing_enabled

log = logging.getLogger(__name__)

HOST = "https://smith.langchain.com"
RETRY_AFTER_S = 60.0

NOT_STARTED = "The run has not started yet."
TRACING_OFF = "Not traced: tracing was off on this server when the run started."
UNRESOLVED = "The LangSmith project could not be reached yet; try again in a minute."


def trace_url(tenant_id: str, project_id: str, trace_id: str) -> str:
    return (
        f"{HOST}/o/{tenant_id}/projects/p/{project_id}/r/{trace_id}?trace_id={trace_id}&poll=true"
    )


def _default_client() -> Any:
    from langsmith import Client

    return Client()


class TraceLinks:
    def __init__(
        self,
        project: str,
        client_factory: Callable[[], Any] = _default_client,
        enabled: Callable[[], bool] = tracing_enabled,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.project = project
        self._client_factory = client_factory
        self._enabled = enabled
        self._clock = clock
        self._ids: tuple[str, str] | None = None
        self._failed_at: float | None = None
        self._lock = asyncio.Lock()

    async def _resolve(self) -> tuple[str, str] | None:
        if self._ids is not None:
            return self._ids
        async with self._lock:
            if self._ids is not None:
                return self._ids
            if self._failed_at is not None and self._clock() - self._failed_at < RETRY_AFTER_S:
                return None
            try:
                project = await asyncio.to_thread(
                    lambda: self._client_factory().read_project(project_name=self.project)
                )
                self._ids = (str(project.tenant_id), str(project.id))
            except Exception:  # noqa: BLE001 - a missing link must never fail the request
                log.warning("could not resolve LangSmith project %r", self.project, exc_info=True)
                self._failed_at = self._clock()
                return None
            return self._ids

    async def link(self, trace_run_id: str | None, status: str) -> dict[str, str]:
        """``{"url": ...}`` for a traced run, else ``{"reason": ...}``."""
        if trace_run_id is None:
            return {"reason": NOT_STARTED if status == "queued" else TRACING_OFF}
        if not self._enabled():
            return {"reason": UNRESOLVED}
        ids = await self._resolve()
        if ids is None:
            return {"reason": UNRESOLVED}
        return {"url": trace_url(ids[0], ids[1], trace_run_id)}
