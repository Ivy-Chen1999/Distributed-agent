"""Upload a labelling sample to the deployed app and fetch the answers back (``womm.api.labels``).

Both labelling tasks work the same way: ``sample`` writes ``pages.json`` (what each annotator
sees, never the private key) next to ``key.private.json``; ``upload`` sends the pages, the app
saves answers as people give them, and ``fetch`` writes them back as the answer files the local
``score`` step already reads. Both need ``WOMM_LABEL_ADMIN_TOKEN``, the value set on the app.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from womm.eval.golden_review import HOLDOUT_REGISTRY, refuse_holdout_fixture

PAGES_FILE = "pages.json"
TOKEN_ENV = "WOMM_LABEL_ADMIN_TOKEN"


class HostedLabelsError(ValueError):
    """The upload or fetch could not be done: nothing was changed locally."""


def write_pages(out_dir: Path, *, kind: str, sample: str, pages: dict[str, list[dict]]) -> None:
    data = {"kind": kind, "sample": sample, "annotators": pages}
    (out_dir / PAGES_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_pages(directory: Path) -> dict[str, Any]:
    try:
        return json.loads((directory / PAGES_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise HostedLabelsError(f"no {PAGES_FILE} in {directory}: draw the sample first") from None


def _token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise HostedLabelsError(f"set {TOKEN_ENV} (the value on the app)")
    return token


def upload(
    directory: Path,
    url: str,
    *,
    client: httpx.Client | None = None,
    holdout_registry: Path = HOLDOUT_REGISTRY,
) -> str:
    """Host the sample's pages; returns the link to send to the annotators."""
    pages = read_pages(directory)
    for cards in pages["annotators"].values():
        for c in cards:
            refuse_holdout_fixture(c["fixture"], "hosting a labelling page", holdout_registry)
    base = url.rstrip("/")
    client = client or httpx.Client(timeout=30)
    r = client.put(
        f"{base}/label/api/{pages['sample']}/admin/tasks",
        json={"kind": pages["kind"], "annotators": pages["annotators"]},
        headers={"Authorization": f"Bearer {_token()}"},
    )
    if r.status_code >= 400:
        raise HostedLabelsError(f"upload refused ({r.status_code}): {r.text}")
    return f"{base}/label/{pages['sample']}"


def fetch(directory: Path, url: str, *, client: httpx.Client | None = None) -> dict[str, dict]:
    """Per annotator: number of cards, when they submitted, and their answers."""
    pages = read_pages(directory)
    client = client or httpx.Client(timeout=30)
    r = client.get(
        f"{url.rstrip('/')}/label/api/{pages['sample']}/admin/results",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    if r.status_code >= 400:
        raise HostedLabelsError(f"fetch refused ({r.status_code}): {r.text}")
    return r.json()["annotators"]


def progress(got: dict[str, dict]) -> tuple[list[str], list[str]]:
    """Printable lines, one per annotator, and the names not submitted yet."""
    lines, pending = [], []
    for name, a in sorted(got.items()):
        done = bool(a.get("submitted_at"))
        if not done:
            pending.append(name)
        state = "submitted" if done else "not submitted yet"
        lines.append(f"{name}: {len(a['answers'])} of {a['cards']} answered, {state}")
    return lines, pending
