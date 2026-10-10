"""Hosted labelling for the reference-answer check (``womm.eval.reference_check``).

The owner draws a sample locally and uploads its pages (``scripts/reference_check.py upload``);
annotators open ``/label/<sample>``, sign in with their name, and each answer is saved here as
they give it, so there is nothing to download or send. The owner fetches the answers back
(``scripts/reference_check.py fetch``) and scores them locally, where the tool's decisions are.

Off unless ``WOMM_LABEL_ADMIN_TOKEN`` is set. Sign-in is the annotator's name alone, checked
against the sample's annotators on every request through a signed cookie (decided 2026-10-10:
no access code). The admin token guards upload and fetch. Only train/val material: the sample
step refuses holdout drafts, the upload re-checks the local holdout registry, and cards that
carry the tool's decision are refused.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from womm.api.db import Database
from womm.eval import reference_check as rc
from womm.eval.golden_review import ReviewError, refuse_holdout_fixture

ADMIN_PAGE = Path(__file__).with_name("label_admin.html")
SAMPLE = re.compile(r"\d{4}-\d{2}-\d{2}-[0-9a-f]{8}")
NAME = rc.NAME_OK
COOKIE = "womm_label"
SESSION_S = 30 * 86400  # long enough to stop and come back with the same link
# What only the private key holds: a card carrying any of these would show the tool's decision.
KEY_FIELDS = {"stratum", "tool_kept", "tool_reason", "decision", "review", "judge", "tiebreak"}
MAX_CARDS = 500
SIGN_IN_FAILURES = 10
SIGN_IN_WINDOW_S = 600.0

_bearer = HTTPBearer(auto_error=False)


class SignIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class Answer(BaseModel):
    label: str
    note: str = Field(default="", max_length=2000)


class Upload(BaseModel):
    annotators: dict[str, list[dict]]


def _sign(secret: str, sample: str, annotator: str, expires: int) -> str:
    msg = f"{sample}\n{annotator}\n{expires}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()


def session_cookie(secret: str, sample: str, annotator: str, now: float) -> str:
    expires = int(now) + SESSION_S
    return f"{sample}|{annotator}|{expires}|{_sign(secret, sample, annotator, expires)}"


def cookie_annotator(secret: str, sample: str, raw: str, now: float) -> str | None:
    """The annotator a session cookie names, when it is for ``sample``, signed and unexpired."""
    parts = raw.split("|")
    if len(parts) != 4 or parts[0] != sample or not parts[2].isdigit():
        return None
    _, name, expires, sig = parts
    good = hmac.compare_digest(sig, _sign(secret, sample, name, int(expires)))
    if not good or int(expires) < now:
        return None
    return name


def _client_ip(request: Request) -> str:
    # Railway terminates TLS at a proxy that sets X-Forwarded-For.
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "")


def _check_cards(annotators: dict[str, list[dict]]) -> None:
    if not annotators:
        raise ValueError("no annotators")
    for name, cards in annotators.items():
        if not NAME.fullmatch(name):
            raise ValueError(f"annotator name {name!r}: letters, digits, - and _ only")
        if not cards or len(cards) > MAX_CARDS:
            raise ValueError(f"{name}: between 1 and {MAX_CARDS} cards")
        ids = [c.get("id") for c in cards]
        if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
            raise ValueError(f"{name}: every card needs a distinct id")
        for c in cards:
            leaked = sorted(KEY_FIELDS & c.keys())
            if leaked:
                raise ValueError(f"card {c['id']} carries {leaked}; the tool's decision is private")
            refuse_holdout_fixture(str(c.get("fixture", "")), "hosting a labelling page")


def add_label_routes(app: FastAPI, admin_token: str) -> None:
    failures: dict[str, deque[float]] = defaultdict(deque)

    def db(request: Request) -> Database:
        return request.app.state.db

    def sample_id(sample: str) -> str:
        if not SAMPLE.fullmatch(sample):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such sample")
        return sample

    def require_admin(
        creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    ) -> None:
        if creds is None or not hmac.compare_digest(
            creds.credentials.encode(), admin_token.encode()
        ):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin token required")

    async def annotator(request: Request, sample: str) -> str:
        """The signed-in annotator of ``sample``, still on its roster."""
        sample_id(sample)
        name = cookie_annotator(admin_token, sample, request.cookies.get(COOKIE, ""), time.time())
        if name is None or name not in await db(request).label_annotators(sample):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in with your name")
        return name

    async def task_view(request: Request, sample: str, name: str) -> dict:
        task = await db(request).label_task(sample, name)
        if task is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "sign in with your name")
        return {"annotator": name, "items": task["cards"], "answers": task["answers"],
                "submitted": task["submitted_at"] is not None}  # fmt: skip

    @app.get("/label/{sample}", include_in_schema=False)
    async def page(sample: str, request: Request) -> HTMLResponse:
        if not await db(request).label_annotators(sample_id(sample)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such sample")
        html = rc.render_hosted_page(sample, api=f"/label/api/{sample}")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.get("/label/{sample}/admin", include_in_schema=False)
    async def admin_page(sample: str) -> HTMLResponse:
        """Progress for the owner. The page asks for the admin token and sends it as a header,
        so it never sits in a URL or a log."""
        html = ADMIN_PAGE.read_text(encoding="utf-8").replace("__SAMPLE__", sample_id(sample))
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.post("/label/api/{sample}/session")
    async def sign_in(sample: str, body: SignIn, request: Request, response: Response) -> dict:
        sample_id(sample)
        ip, now = _client_ip(request), time.monotonic()
        recent = failures[ip]
        while recent and now - recent[0] > SIGN_IN_WINDOW_S:
            recent.popleft()
        if len(recent) >= SIGN_IN_FAILURES:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "too many tries; wait a bit")
        wanted = body.name.strip().lstrip("@").casefold()
        roster = await db(request).label_annotators(sample)
        name = next((a for a in roster if a.casefold() == wanted), None)
        if name is None:
            recent.append(now)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "that name is not on this check")
        https = "https" in (request.url.scheme, request.headers.get("x-forwarded-proto"))
        response.set_cookie(
            COOKIE, session_cookie(admin_token, sample, name, time.time()), path="/label",
            httponly=True, samesite="strict", secure=https, max_age=SESSION_S,
        )  # fmt: skip
        return await task_view(request, sample, name)

    @app.get("/label/api/{sample}/me")
    async def me(sample: str, request: Request) -> dict:
        return await task_view(request, sample, await annotator(request, sample))

    @app.put("/label/api/{sample}/answers/{item_id}")
    async def answer(sample: str, item_id: str, body: Answer, request: Request) -> dict:
        name = await annotator(request, sample)
        if body.label not in rc.LABELS:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                f"label must be one of {', '.join(rc.LABELS)}")  # fmt: skip
        task = await db(request).label_task(sample, name)
        if task is None or item_id not in {c["id"] for c in task["cards"]}:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"item {item_id} is not on your page")
        await db(request).save_label_answer(sample, name, item_id, body.label, body.note.strip())
        return {"saved": item_id}

    @app.post("/label/api/{sample}/submit")
    async def submit(sample: str, request: Request) -> dict:
        name = await annotator(request, sample)
        task = await db(request).label_task(sample, name)
        open_ = [c["id"] for c in task["cards"] if c["id"] not in task["answers"]]
        if open_:
            raise HTTPException(status.HTTP_409_CONFLICT, f"still open: {', '.join(open_)}")
        await db(request).submit_label_task(sample, name)
        return {"submitted": True}

    @app.put("/label/api/{sample}/admin/tasks", dependencies=[Depends(require_admin)])
    async def upload(sample: str, body: Upload, request: Request) -> dict:
        sample_id(sample)
        try:
            _check_cards(body.annotators)
        except (ValueError, ReviewError) as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
        try:
            await db(request).put_label_tasks(sample, body.annotators)
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None
        return {"sample": sample, "annotators": sorted(body.annotators)}

    @app.get("/label/api/{sample}/admin/results", dependencies=[Depends(require_admin)])
    async def results(sample: str, request: Request) -> dict:
        found = await db(request).label_results(sample_id(sample))
        if not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such sample")
        return {"sample": sample, "annotators": found}
