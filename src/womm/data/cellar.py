"""Download EU legal texts from the Publications Office Cellar by content negotiation.

Only used at fixture-build time; runtime code reads the committed fixture files. EUR-Lex web
pages sit behind a WAF, so everything goes through ``publications.europa.eu/resource``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import httpx
import lxml.html
from lxml import etree

from womm.config import REPO_ROOT

CELLAR_BASE = "https://publications.europa.eu/resource"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "cellar"
XHTML = "application/xhtml+xml"
HTML = "text/html"


class CellarError(RuntimeError):
    """Raised when Cellar does not return the requested document."""


class CellarNoDatastream(CellarError):
    """HTTP 404/406: the resource exists in Cellar but holds no stream of the requested type."""


class CellarMultipleChoice(CellarError):
    """HTTP 300: the resource has several items (``DOC_1``, ``DOC_3``, ...); ``listing`` is
    Cellar's HTML list of them."""

    def __init__(self, url: str, listing: str) -> None:
        super().__init__(
            f"{url} is ambiguous (HTTP 300); choose one of the listed items:\n{listing[:2000]}"
        )
        self.url = url
        self.listing = listing


@dataclass(frozen=True)
class ListingItem:
    url: str
    stream_name: str
    order: int | None


def parse_listing(listing: str) -> list[ListingItem]:
    """Items of a Cellar HTTP 300 listing, with their https URL and stream name."""
    try:
        root = lxml.html.fromstring(listing)
    except (etree.ParserError, ValueError):
        return []
    items = []
    for li in root.iter("li"):
        if li.get("title") != "item":
            continue
        link = li.find("a")
        href = link.get("href", "") if link is not None else ""
        if not href.startswith(("http://publications.europa.eu/", f"{CELLAR_BASE}/")):
            continue
        fields = {f.get("title"): (f.text or "").strip() for f in li.iter("li")}
        order = fields.get("stream_order", "")
        items.append(
            ListingItem(
                url="https://" + href.split("://", 1)[1],
                stream_name=fields.get("stream_name", ""),
                order=int(order) if order.isdigit() else None,
            )
        )
    return items


def main_document(celex: str, items: list[ListingItem]) -> ListingItem:
    """The item holding the act itself (explanatory memorandum + articles), not its annexes.

    Commission proposals name it ``<n>_EN_ACT_part1_v<k>.html``; the annexes are
    ``..._annexe_...``. Anything else is ambiguous and fails naming the CELEX."""
    acts = [
        i
        for i in items
        if re.search(r"(^|_)ACT(_|\.)", i.stream_name) and "annex" not in i.stream_name.lower()
    ]
    if len(acts) != 1:
        names = [i.stream_name or i.url for i in items]
        raise CellarError(
            f"{celex}: no single main document in the HTTP 300 listing "
            f"({len(acts)} act item(s) among {names})"
        )
    return acts[0]


def celex_url(celex: str) -> str:
    """Cellar resource URL; a document suffix such as ``(01)`` must be percent-encoded, as
    Cellar answers 404 to raw parentheses."""
    m = re.fullmatch(r"([0-9A-Z]+)(?:\((\d{2})\))?", celex)
    if not m:
        raise ValueError(f"not a CELEX number: {celex!r}")
    suffix = f"%28{m.group(2)}%29" if m.group(2) else ""
    return f"{CELLAR_BASE}/celex/{m.group(1)}{suffix}"


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
        raise CellarMultipleChoice(url, response.text)
    if response.status_code in (404, 406):
        raise CellarNoDatastream(f"{url} returned HTTP {response.status_code} for {accept}")
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


def _cached_document(
    url: str, *, language: str, cache_dir: Path, expected_sha256: str | None
) -> bytes | None:
    """The cached body under either type: the one matching ``expected_sha256`` (the pinned
    hash), else the one cached last; None when neither is cached."""
    paths = [_cache_path(cache_dir, url, accept, language) for accept in (XHTML, HTML)]
    present = [p for p in paths if p.exists()]
    if not present:
        return None
    bodies = {p: p.read_bytes() for p in present}
    if expected_sha256:
        for body in bodies.values():
            if sha256(body) == expected_sha256:
                return body
    return bodies[max(present, key=lambda p: p.stat().st_mtime_ns)]


def fetch_document(url: str, *, expected_sha256: str | None = None, **kwargs) -> bytes:
    """``fetch`` as XHTML, falling back to ``text/html`` when Cellar holds no XHTML stream.

    Since 2025 Cellar registers the Commission's XHTML manifestation as ``text/html``: the
    XHTML request answers 404 (resource) or 406 (item), the ``text/html`` one serves the same
    XHTML document. A body cached under either type is reused without a request: the one whose
    sha256 equals ``expected_sha256`` (the pin in ``downloads.json``) when given, else the one
    cached last, never XHTML blindly."""
    if kwargs.get("accept", XHTML) == XHTML and not kwargs.get("refresh"):
        body = _cached_document(
            url,
            language=kwargs.get("language", "eng"),
            cache_dir=kwargs.get("cache_dir", DEFAULT_CACHE_DIR),
            expected_sha256=expected_sha256,
        )
        if body is not None:
            return body
    try:
        return fetch(url, **kwargs)
    except CellarNoDatastream:
        if kwargs.get("accept", XHTML) != XHTML:
            raise
        return fetch(url, **{**kwargs, "accept": HTML})


def fetch_main_document(celex: str, **kwargs) -> tuple[str, bytes]:
    """(URL, body) of a COM document's main item: the CELEX resource itself when Cellar
    answers 200, else the act item (``DOC_n``) resolved from its HTTP 300 listing."""
    try:
        url = celex_url(celex)
        return url, fetch_document(url, **kwargs)
    except CellarMultipleChoice as exc:
        item = main_document(celex, parse_listing(exc.listing))
        return item.url, fetch_document(item.url, **kwargs)
