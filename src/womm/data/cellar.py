"""Download EU legal texts from the Publications Office Cellar by content negotiation.

Only used at fixture-build time; runtime code reads the committed fixture files. EUR-Lex web
pages sit behind a WAF, so everything goes through ``publications.europa.eu/resource``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx

from womm.config import REPO_ROOT

CELLAR_BASE = "https://publications.europa.eu/resource"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "cellar"
XHTML = "application/xhtml+xml"


class CellarError(RuntimeError):
    """Raised when Cellar does not return the requested document."""


def celex_url(celex: str) -> str:
    if not re.fullmatch(r"[0-9A-Z]+", celex):
        raise ValueError(f"not a CELEX number: {celex!r}")
    return f"{CELLAR_BASE}/celex/{celex}"


def _cache_path(cache_dir: Path, url: str, accept: str, language: str) -> Path:
    digest = hashlib.sha256(f"{url}|{accept}|{language}".encode()).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9.]+", "_", url.removeprefix(CELLAR_BASE)).strip("_")
    return cache_dir / f"{stem}-{digest}.bin"


def fetch(
    url: str,
    *,
    accept: str = XHTML,
    language: str = "eng",
    cache_dir: Path = DEFAULT_CACHE_DIR,
    client: httpx.Client | None = None,
    refresh: bool = False,
) -> bytes:
    """GET ``url`` with content negotiation, caching the body on disk.

    Cellar answers an ambiguous request (e.g. a COM document with several parts) with HTTP 300
    and a listing; that is surfaced as a CellarError so the caller picks the concrete item URL.
    """
    path = _cache_path(cache_dir, url, accept, language)
    if path.exists() and not refresh:
        return path.read_bytes()

    headers = {"Accept": accept, "Accept-Language": language}
    own_client = client is None
    client = client or httpx.Client(follow_redirects=True, timeout=120.0)
    try:
        response = client.get(url, headers=headers)
    finally:
        if own_client:
            client.close()

    if response.status_code == 300:
        raise CellarError(
            f"{url} is ambiguous (HTTP 300); choose one of the listed items:\n"
            f"{response.text[:2000]}"
        )
    if response.status_code != 200:
        raise CellarError(f"{url} returned HTTP {response.status_code}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return response.content


def cached(
    url: str,
    *,
    accept: str = XHTML,
    language: str = "eng",
    cache_dir: Path = DEFAULT_CACHE_DIR,
) -> bytes | None:
    """The cached body for ``url``, or None; never touches the network."""
    path = _cache_path(cache_dir, url, accept, language)
    return path.read_bytes() if path.exists() else None


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def fetch_celex(celex: str, **kwargs) -> bytes:
    return fetch(celex_url(celex), **kwargs)
