import json

import httpx
import pytest

from tests.graph.conftest import SCENARIO, fake_sv, good_script
from womm.config import REPO_ROOT
from womm.data.fixtures import load_fixture
from womm.decisions.jev import JEV_URL, JevDecisionService, relevance_question
from womm.graph.build import run_scenario
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity
from womm.models.system_version import load_system_version

SV = load_system_version(REPO_ROOT / "system_versions" / "v0.1-jev.yaml", REPO_ROOT)
EXPERTS = SV.spec.experts


def answers(**probs):
    return {
        "model": "jev-latest",
        "answers": {k: {"type": "noul", "noul": v} for k, v in probs.items()},
    }


def service(handler, calls: list | None = None, **kw) -> JevDecisionService:
    def wrapped(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        return handler(request)

    return JevDecisionService("key-123", transport=httpx.MockTransport(wrapped), **kw)


def by_subject(recs):
    return {r.subject: r for r in recs}


def test_v01_jev_version_selects_jev_in_shadow_mode():
    assert SV.spec.router.decider == "jev"
    assert SV.spec.router.mode == "shadow"


async def test_happy_path_one_request_three_records():
    calls: list[httpx.Request] = []
    svc = service(
        lambda r: httpx.Response(200, json=answers(legal=0.9, fiscal=0.2, stakeholder=0.5)), calls
    )
    recs = await svc.expert_relevance(EXPERTS, "Scenario x: 2 changed provisions", SV)

    assert len(calls) == 1
    req = calls[0]
    assert str(req.url) == JEV_URL
    assert req.method == "POST"
    assert req.headers["Authorization"] == "Bearer key-123"
    body = json.loads(req.content)
    assert body["model"] == "jev-latest"
    assert body["state"] == "Scenario x: 2 changed provisions"
    assert set(body["questions"]) == {"legal", "fiscal", "stakeholder"}
    legal_q = body["questions"]["legal"]
    assert legal_q["type"] == "noul"
    assert legal_q["instructions"] == relevance_question(EXPERTS[0])
    assert "legal specialist" in legal_q["instructions"]
    # the domain is spelled out (a bare "fiscal" was read as public finance)
    fiscal_q = body["questions"]["fiscal"]["instructions"]
    assert "compliance costs" in fiscal_q and "fines" in fiscal_q

    assert [r.subject for r in recs] == ["legal", "fiscal", "stakeholder"]
    assert {r.decider for r in recs} == {"jev"}
    assert {r.mode for r in recs} == {"shadow"}
    assert {r.system_version for r in recs} == {SV.version_id}
    assert {r.decision_point for r in recs} == {"router.relevance"}
    got = by_subject(recs)
    assert (got["legal"].decision, got["legal"].probability) == ("relevant", 0.9)
    assert (got["fiscal"].decision, got["fiscal"].probability) == ("not_relevant", 0.2)
    assert got["stakeholder"].decision == "relevant"  # threshold is inclusive
    assert all(r.error is None and not r.truncated for r in recs)


async def test_custom_threshold():
    svc = service(
        lambda r: httpx.Response(200, json=answers(legal=0.6, fiscal=0.6, stakeholder=0.6)),
        threshold=0.7,
    )
    recs = await svc.expert_relevance(EXPERTS, "ctx", SV)
    assert {r.decision for r in recs} == {"not_relevant"}


async def test_timeout_is_error_record_not_raise():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    recs = await service(handler).expert_relevance(EXPERTS, "ctx", SV)
    assert len(recs) == 3
    for r in recs:
        assert r.decision == "error"
        assert r.probability is None
        assert r.decider == "jev"
        assert r.error.startswith("[timeout]")


async def test_timeout_comes_from_router_config():
    seen = {}

    def handler(request):
        seen["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json=answers(legal=1, fiscal=1, stakeholder=1))

    await service(handler).expert_relevance(EXPERTS, "ctx", SV)
    assert seen["timeout"]["read"] == SV.spec.router.timeout_s


async def test_cloudflare_html_403_is_process_error():
    html = (
        "<!DOCTYPE html><html><head><title>Attention Required! | Cloudflare</title></head></html>"
    )

    recs = await service(
        lambda r: httpx.Response(403, text=html, headers={"content-type": "text/html"})
    ).expert_relevance(EXPERTS, "ctx", SV)
    assert {r.decision for r in recs} == {"error"}
    assert all(r.probability is None for r in recs)
    assert all(r.error.startswith("[process_error]") and "403" in r.error for r in recs)


async def test_json_401_is_auth_error():
    recs = await service(lambda r: httpx.Response(401, json={"error": "bad key"})).expert_relevance(
        EXPERTS, "ctx", SV
    )
    assert all(r.error.startswith("[auth]") for r in recs)


async def test_non_json_200_is_process_error():
    recs = await service(
        lambda r: httpx.Response(
            200, text="<html>oops</html>", headers={"content-type": "text/html"}
        )
    ).expert_relevance(EXPERTS, "ctx", SV)
    assert {r.decision for r in recs} == {"error"}
    assert all("non-JSON" in r.error for r in recs)


async def test_connect_error_is_error_record():
    def handler(request):
        raise httpx.ConnectError("dns failure", request=request)

    recs = await service(handler).expert_relevance(EXPERTS, "ctx", SV)
    assert all(r.decision == "error" and r.error.startswith("[process_error]") for r in recs)


async def test_missing_answers_object():
    recs = await service(lambda r: httpx.Response(200, json={"model": "m"})).expert_relevance(
        EXPERTS, "ctx", SV
    )
    assert {r.decision for r in recs} == {"error"}


async def test_missing_or_malformed_answer_for_one_expert():
    payload = answers(legal=0.8)
    payload["answers"]["stakeholder"] = {"type": "noul", "noul": 1.7}
    recs = by_subject(
        await service(lambda r: httpx.Response(200, json=payload)).expert_relevance(
            EXPERTS, "ctx", SV
        )
    )
    assert recs["legal"].decision == "relevant"
    for sid in ("fiscal", "stakeholder"):
        assert recs[sid].decision == "error"
        assert recs[sid].probability is None
        assert sid in recs[sid].error


async def test_oversized_state_is_truncated():
    calls: list[httpx.Request] = []
    svc = service(
        lambda r: httpx.Response(200, json=answers(legal=0.9, fiscal=0.9, stakeholder=0.9)),
        calls,
        max_state_chars=1000,
    )
    recs = await svc.expert_relevance(EXPERTS, "x" * 5000, SV)
    assert len(json.loads(calls[0].content)["state"]) <= 1000
    assert all(r.truncated for r in recs)
    assert {r.decision for r in recs} == {"relevant"}


async def test_default_budget_truncates_large_state():
    calls: list[httpx.Request] = []
    svc = service(lambda r: httpx.Response(200, json=answers()), calls)
    recs = await svc.expert_relevance(EXPERTS, "y" * 250_000, SV)
    assert len(json.loads(calls[0].content)["state"]) <= 100_000
    assert all(r.truncated and r.decision == "error" for r in recs)


async def test_urls_are_stripped_from_state():
    calls: list[httpx.Request] = []
    svc = service(
        lambda r: httpx.Response(200, json=answers(legal=0.9, fiscal=0.9, stakeholder=0.9)), calls
    )
    context = (
        "See https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:52021PC0206 and "
        "www.example.org/page plus http://x.y/z for details."
    )
    recs = await svc.expert_relevance(EXPERTS, context, SV)
    state = json.loads(calls[0].content)["state"]
    assert "http" not in state and "www." not in state
    assert state.startswith("See [url] and [url] plus [url] for details.")
    assert "http" not in recs[0].input_summary


def test_requires_api_key():
    with pytest.raises(ValueError):
        JevDecisionService("")


async def test_graph_records_jev_decisions():
    calls: list[httpx.Request] = []
    svc = service(
        lambda r: httpx.Response(200, json=answers(legal=0.9, fiscal=0.1, stakeholder=0.7)), calls
    )
    result = await run_scenario(
        SCENARIO,
        sv=fake_sv(),
        fixture=load_fixture(),
        backends={"fake": FakeBackend(good_script())},
        decisions=svc,
        code_identity=CodeIdentity(git_sha="test", dirty=False),
        run_id="run_jev",
    )
    assert len(calls) == 1
    recs = by_subject(result.decisions)
    assert set(recs) == {"legal", "fiscal", "stakeholder"}
    assert {r.decider for r in recs.values()} == {"jev"}
    assert recs["fiscal"].decision == "not_relevant"
    assert recs["fiscal"].mode == "shadow"


async def test_records_model_latency_and_usage():
    body = {
        "model": "jev-1.13.0",
        "answers": {e.id: {"type": "noul", "noul": 0.7} for e in SV.spec.experts},
        "usage": {"input_tokens": 311, "output_tokens": 37},
    }
    svc = JevDecisionService("key-123", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json=body)))  # fmt: skip
    recs = await svc.expert_relevance(SV.spec.experts, "state", SV)
    assert {r.model for r in recs} == {"jev-1.13.0"}
    assert all(r.input_tokens == 311 and r.output_tokens == 37 for r in recs)
    assert all(r.latency_s is not None and r.latency_s >= 0 for r in recs)


async def test_error_records_have_no_usage():
    svc = JevDecisionService("key-123", transport=httpx.MockTransport(
        lambda r: httpx.Response(500, text="boom")))  # fmt: skip
    recs = await svc.expert_relevance(SV.spec.experts, "state", SV)
    assert all(r.decision == "error" and r.input_tokens is None for r in recs)
