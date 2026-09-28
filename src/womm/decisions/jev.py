"""Jev decider (R10, U8): one Noul question per expert, answered by TypeSafe's Jev in a single
REST call. Calls the API directly with httpx; no SDK dependency.

Never raises: any transport, HTTP, or payload problem becomes a decision='error' record per
affected expert, with a short '[error_kind] reason' string, so the run continues.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from womm.decisions.service import RELEVANCE_POINT
from womm.models.decisions import DecisionRecord
from womm.models.system_version import ExpertConfig, SystemVersion

JEV_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
DEFAULT_THRESHOLD = 0.5
# Jev accepts ~32k tokens for state + the longest question. 100k chars stays well below that
# for English text (~4 chars/token) while leaving room for the question.
DEFAULT_MAX_STATE_CHARS = 100_000

# URL-like text in the state can trigger a Cloudflare 403 HTML block in front of the API.
_URL_RE = re.compile(r"(?:https?://|ftp://|www\.)\S+", re.IGNORECASE)


def strip_urls(text: str) -> str:
    return _URL_RE.sub("[url]", text)


def relevance_question(expert: ExpertConfig) -> str:
    return (
        f"Is the {expert.domain} specialist ({expert.id}) needed to assess the impacts of "
        f"these regulatory changes? The {expert.id} expert covers the {expert.domain} domain."
    )


class JevDecisionService:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        url: str = JEV_URL,
        threshold: float = DEFAULT_THRESHOLD,
        max_state_chars: int = DEFAULT_MAX_STATE_CHARS,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key:
            raise ValueError("JevDecisionService requires a TypeSafe API key")
        self._api_key = api_key
        self.model = model
        self.url = url
        self.threshold = threshold
        self.max_state_chars = max_state_chars
        self._transport = transport

    def build_state(self, context: str) -> tuple[str, bool]:
        """URL-stripped state and whether it had to be truncated to the char budget."""
        state = strip_urls(context)
        if len(state) > self.max_state_chars:
            return state[: self.max_state_chars], True
        return state, False

    async def expert_relevance(
        self, experts: list[ExpertConfig], context: str, sv: SystemVersion
    ) -> list[DecisionRecord]:
        state, truncated = self.build_state(context)

        def record(expert: ExpertConfig, decision: str, prob: float | None, error: str | None):
            return DecisionRecord(
                decision_point=RELEVANCE_POINT,
                subject=expert.id,
                input_summary=state[:500],
                decision=decision,
                probability=prob,
                mode=sv.spec.router.mode,
                decider="jev",
                system_version=sv.version_id,
                error=error,
                truncated=truncated,
            )

        if not experts:
            return []
        try:
            answers = await self._ask(experts, state, sv.spec.router.timeout_s)
        except Exception as exc:  # never raise: every expert gets an error record
            reason = _describe(exc)
            return [record(e, "error", None, reason) for e in experts]

        records = []
        for e in experts:
            prob = _noul(answers.get(e.id))
            if prob is None:
                records.append(
                    record(e, "error", None, f"[schema_invalid] no valid noul answer for {e.id}")
                )
                continue
            decision = "relevant" if prob >= self.threshold else "not_relevant"
            records.append(record(e, decision, prob, None))
        return records

    async def _ask(
        self, experts: list[ExpertConfig], state: str, timeout_s: float
    ) -> dict[str, Any]:
        body = {
            "model": self.model,
            "state": state,
            "questions": {
                e.id: {"type": "noul", "instructions": relevance_question(e)} for e in experts
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(timeout=timeout_s, transport=self._transport) as client:
            resp = await client.post(self.url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise _HttpError(resp)
        try:
            payload = resp.json()
        except ValueError:
            raise _PayloadError(f"non-JSON response ({_content_type(resp)})") from None
        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            raise _PayloadError("response has no 'answers' object")
        return answers


class _HttpError(Exception):
    def __init__(self, resp: httpx.Response):
        self.status_code = resp.status_code
        self.is_json = "json" in _content_type(resp)
        super().__init__(f"HTTP {resp.status_code}")


class _PayloadError(Exception):
    pass


def _content_type(resp: httpx.Response) -> str:
    return resp.headers.get("content-type", "unknown content-type").split(";")[0].strip()


def _describe(exc: BaseException) -> str:
    """Short '[error_kind] reason' string (ErrorKind vocabulary from womm.models.findings)."""
    if isinstance(exc, httpx.TimeoutException):
        return f"[timeout] Jev request timed out ({type(exc).__name__})"
    if isinstance(exc, _HttpError):
        if not exc.is_json:
            # e.g. a Cloudflare HTML 403 page: an edge block, not a credentials problem.
            return f"[process_error] HTTP {exc.status_code} non-JSON response (likely edge block)"
        if exc.status_code in (401, 403):
            return f"[auth] HTTP {exc.status_code} from Jev"
        if exc.status_code == 429:
            return "[rate_limit] HTTP 429 from Jev"
        return f"[process_error] HTTP {exc.status_code} from Jev"
    if isinstance(exc, _PayloadError):
        return f"[process_error] {exc}"
    return f"[process_error] {type(exc).__name__}: {exc}"[:300]


def _noul(answer: Any) -> float | None:
    """The probability from one answer, or None if it is missing or malformed."""
    if not isinstance(answer, dict):
        return None
    value = answer.get("noul")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    value = float(value)
    if not 0.0 <= value <= 1.0:
        return None
    return value
