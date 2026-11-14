"""The provider-key check: a rejected key is a reason (never the key), cached for ten minutes;
network errors and outages do not block runs."""

from womm.api.provider_keys import KeyCheck
from womm.config import load_settings

from ..graph.conftest import fake_sv

SECRET = "sk-test-not-a-real-key"


def _check(codes, now):
    calls = []

    async def probe(provider, key):
        calls.append((provider, key))
        return codes.pop(0)

    return KeyCheck({"openai": SECRET}, probe=probe, clock=lambda: now["t"]), calls


async def test_a_rejected_key_is_a_reason_without_the_key():
    now = {"t": 0.0}
    check, _ = _check([401], now)
    err = await check.error()
    assert "openai API key was rejected (HTTP 401)" in err and "OPENAI_API_KEY" in err
    assert SECRET not in err


async def test_the_result_is_cached_then_rechecked():
    now = {"t": 0.0}
    check, calls = _check([401, 200], now)
    assert await check.error()
    now["t"] = 599.0
    assert await check.error()
    assert len(calls) == 1
    now["t"] = 601.0
    assert await check.error() is None  # a replaced key is picked up after the TTL
    assert len(calls) == 2


async def test_network_errors_and_outages_do_not_block_runs():
    for code in (None, 500, 429, 200):
        check, _ = _check([code], {"t": 0.0})
        assert await check.error() is None


def test_only_versions_with_api_roles_get_a_check():
    settings = load_settings({"OPENAI_API_KEY": SECRET})
    assert KeyCheck.for_version(fake_sv(), settings) is None
