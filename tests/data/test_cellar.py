from pathlib import Path

import httpx
import pytest

from womm.data.cellar import (
    HTML,
    XHTML,
    CellarError,
    _cache_path,
    cached,
    celex_url,
    fetch,
    fetch_celex,
    fetch_document,
    fetch_main_document,
    main_document,
    parse_listing,
    sha256,
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_negotiates_and_caches(tmp_path: Path):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"<html/>")

    with _client(handler) as client:
        first = fetch_celex("32024R1689", cache_dir=tmp_path, client=client)
        second = fetch_celex("32024R1689", cache_dir=tmp_path, client=client)
    assert first == second == b"<html/>"
    assert len(calls) == 1
    assert str(calls[0].url) == "https://publications.europa.eu/resource/celex/32024R1689"
    assert calls[0].headers["accept"] == "application/xhtml+xml"
    assert calls[0].headers["accept-language"] == "eng"


def test_refresh_bypasses_cache(tmp_path: Path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x")

    with _client(handler) as client:
        fetch("https://publications.europa.eu/resource/x", cache_dir=tmp_path, client=client)
        fetch(
            "https://publications.europa.eu/resource/x",
            cache_dir=tmp_path,
            client=client,
            refresh=True,
        )
    assert len(calls) == 2


def test_multiple_choices_is_an_error_and_not_cached(tmp_path: Path):
    def handler(request):
        return httpx.Response(300, text="<list>DOC_1 DOC_3</list>")

    with _client(handler) as client, pytest.raises(CellarError, match="DOC_1"):
        fetch_celex("52021PC0206", cache_dir=tmp_path, client=client)
    assert not list(tmp_path.iterdir())


def test_http_error(tmp_path: Path):
    with _client(lambda r: httpx.Response(404)) as client, pytest.raises(CellarError, match="404"):
        fetch_celex("32024R1689", cache_dir=tmp_path, client=client)


def test_rejects_non_celex():
    with pytest.raises(ValueError):
        celex_url("../etc/passwd")


def test_celex_with_a_document_suffix_is_percent_encoded():
    """IA CELEX ids such as 5YYYYSC0396(01) are valid in the IA index; Cellar answers 404 to
    the raw parentheses and serves the document only when they are percent-encoded."""
    assert celex_url("52099SC0001(01)").endswith("/celex/52099SC0001%2801%29")
    for bad in ("52099SC0001(1)", "52099SC0001(01", "52099SC0001(01)x", "(01)"):
        with pytest.raises(ValueError):
            celex_url(bad)


LISTING = (Path(__file__).parents[1] / "fixtures" / "cellar_300_com2022_454.html").read_text()
CRA_ITEM = (
    "https://publications.europa.eu/resource/cellar/"
    "864f472b-34e9-11ed-9c68-01aa75ed71a1.0001.03/DOC_{}"
)


def test_parse_listing_real_300_response():
    items = parse_listing(LISTING)
    assert [(i.url, i.stream_name, i.order) for i in items] == [
        (CRA_ITEM.format(1), "1_EN_ACT_part1_v8.html", 1),
        (CRA_ITEM.format(3), "1_EN_annexe_proposition_part1_v6.html", 3),
    ]


def test_main_document_is_the_act_item():
    assert main_document("52022PC0454", parse_listing(LISTING)).url == CRA_ITEM.format(1)


@pytest.mark.parametrize(
    "listing",
    [
        "<html><body>nothing here</body></html>",
        LISTING.replace("_ACT_part1", "_annexe_part2"),
        LISTING.replace("annexe_proposition_part1", "ACT_part2"),
        "",
    ],
)
def test_unrecognisable_listing_fails_naming_the_celex(listing):
    with pytest.raises(CellarError, match="52022PC0454: no single main document"):
        main_document("52022PC0454", parse_listing(listing))


def test_listing_ignores_foreign_links():
    evil = LISTING.replace("http://publications.europa.eu/", "http://example.com/")
    assert parse_listing(evil) == []


def test_fetch_main_document_resolves_300(tmp_path: Path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/celex/52022PC0454"):
            return httpx.Response(300, text=LISTING)
        return httpx.Response(200, content=b"<act/>")

    with _client(handler) as client:
        url, body = fetch_main_document("52022PC0454", cache_dir=tmp_path, client=client)
    assert (url, body) == (CRA_ITEM.format(1), b"<act/>")
    assert seen[-1] == CRA_ITEM.format(1)


def test_fetch_main_document_direct_200(tmp_path: Path):
    with _client(lambda r: httpx.Response(200, content=b"<act/>")) as client:
        url, body = fetch_main_document("52022PC0068", cache_dir=tmp_path, client=client)
    assert url == "https://publications.europa.eu/resource/celex/52022PC0068"
    assert body == b"<act/>"


def test_fetch_main_document_falls_back_to_text_html(tmp_path: Path):
    """2025+ documents: the XHTML stream is registered as text/html (404 on the resource,
    406 on the item for application/xhtml+xml)."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        accept = request.headers["Accept"]
        seen.append((request.url.path.rsplit("/", 1)[-1], accept))
        if accept != "text/html":
            return httpx.Response(404 if "/celex/" in request.url.path else 406)
        if request.url.path.endswith("/celex/52022PC0454"):
            return httpx.Response(300, text=LISTING)
        return httpx.Response(200, content=b"<act/>")

    with _client(handler) as client:
        url, body = fetch_main_document("52022PC0454", cache_dir=tmp_path, client=client)
    assert (url, body) == (CRA_ITEM.format(1), b"<act/>")
    assert [a for _, a in seen] == [
        "application/xhtml+xml",
        "text/html",
        "application/xhtml+xml",
        "text/html",
    ]
    with _client(lambda r: httpx.Response(500)) as client:  # only reached on a cache miss
        assert fetch_document(url, cache_dir=tmp_path, client=client) == b"<act/>"


def test_fetch_document_does_not_mask_other_errors(tmp_path: Path):
    with _client(lambda r: httpx.Response(500)) as client, pytest.raises(CellarError, match="500"):
        fetch_document(celex_url("52022PC0068"), cache_dir=tmp_path, client=client)
    with _client(lambda r: httpx.Response(404)) as client, pytest.raises(CellarError, match="404"):
        fetch_document(celex_url("52022PC0068"), cache_dir=tmp_path, client=client)


def test_cached_reads_only_the_cache(tmp_path: Path):
    url = "https://raw.githubusercontent.com/o/r/abc/data.csv"
    assert cached(url, accept="*/*", cache_dir=tmp_path) is None
    with _client(lambda request: httpx.Response(200, content=b"a,b")) as client:
        fetch(url, accept="*/*", cache_dir=tmp_path, client=client)
    assert cached(url, accept="*/*", cache_dir=tmp_path) == b"a,b"
    assert cached(url, cache_dir=tmp_path) is None  # cache key includes the Accept header


def _cache_both(tmp_path: Path, url: str, xhtml: bytes, html: bytes) -> None:
    """Cache ``url`` under both Accept types (text/html written last)."""
    import os

    for accept, body in ((XHTML, xhtml), (HTML, html)):
        with _client(lambda r, body=body: httpx.Response(200, content=body)) as client:
            fetch(url, accept=accept, cache_dir=tmp_path, client=client)
    xpath = _cache_path(tmp_path, url, XHTML, "eng")
    hpath = _cache_path(tmp_path, url, HTML, "eng")
    os.utime(xpath, (1_000_000, 1_000_000))
    os.utime(hpath, (2_000_000, 2_000_000))


def test_fetch_document_prefers_the_cached_body_matching_the_pin(tmp_path: Path):
    url = celex_url("52099PC0001")
    _cache_both(tmp_path, url, b"<old xhtml/>", b"<new html/>")
    with _client(lambda r: httpx.Response(500)) as client:
        got = fetch_document(
            url, cache_dir=tmp_path, client=client, expected_sha256=sha256(b"<old xhtml/>")
        )
        assert got == b"<old xhtml/>"
        got = fetch_document(
            url, cache_dir=tmp_path, client=client, expected_sha256=sha256(b"<new html/>")
        )
        assert got == b"<new html/>"


def test_fetch_document_without_a_pin_reads_the_most_recent_cache(tmp_path: Path):
    url = celex_url("52099PC0001")
    _cache_both(tmp_path, url, b"<old xhtml/>", b"<new html/>")
    with _client(lambda r: httpx.Response(500)) as client:
        assert fetch_document(url, cache_dir=tmp_path, client=client) == b"<new html/>"
        # A pin matching neither cached body falls back to the most recent one too; the
        # importer's downloads.json check then reports the change.
        assert (
            fetch_document(url, cache_dir=tmp_path, client=client, expected_sha256="0" * 64)
            == b"<new html/>"
        )
