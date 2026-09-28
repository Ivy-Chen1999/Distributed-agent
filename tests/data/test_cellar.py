from pathlib import Path

import httpx
import pytest

from womm.data.cellar import CellarError, celex_url, fetch, fetch_celex


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
    assert str(calls[0].url) == "http://publications.europa.eu/resource/celex/32024R1689"
    assert calls[0].headers["accept"] == "application/xhtml+xml"
    assert calls[0].headers["accept-language"] == "eng"


def test_refresh_bypasses_cache(tmp_path: Path):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x")

    with _client(handler) as client:
        fetch("http://publications.europa.eu/resource/x", cache_dir=tmp_path, client=client)
        fetch(
            "http://publications.europa.eu/resource/x",
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
