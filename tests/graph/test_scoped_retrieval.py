"""Per-expert retrieval through data scopes, the retrieval log and per-agent validation (U4)."""

import hashlib
import json
import re

import pytest

from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT
from womm.data.corpus import load_default_corpus
from womm.decisions.stub import StubDecisionService
from womm.graph.build import run_scenario
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity, RunStatus
from womm.models.system_version import build_system_version, load_system_version

from .test_planner_explore import PRESET_HASHES

V1 = REPO_ROOT / "system_versions" / "v1.0-scoped.yaml"
SME = "eval_sme_impacts"
PROPOSAL = "com2021_206"
ART26 = "ai_act/art/26"  # proposal Art 29
ANNEX3 = "ai_act/annex/III"
K71 = "ai_act/penalties/penalties"
MEMOS = [f"{PROPOSAL}/memorandum/{s}" for s in ("context", "legal_basis", "other_elements")]
RATIONALE = "UNIQUE-RATIONALE-WRITTEN-WITH-DELTA"
EMPTY_SYNTHESIS = {
    "impacts": [], "chains": [], "disagreements": [], "open_questions": [], "discarded": []
}  # fmt: skip
SOURCE_ID = re.compile(r'<source id="([^"]+)" kind="([^"]+)"')


def _fake(path=DEFAULT_SYSTEM_VERSION, scopes: dict | None = None):
    """A SystemVersion file on the fake backend, optionally with replaced expert scopes."""
    base = load_system_version(path, REPO_ROOT).spec
    data = base.model_dump()
    for role in ("planner", "synthesis", "judge"):
        data[role]["backend"] = "fake"
    data["experts"] = [{**e, "role": {**e["role"], "backend": "fake"}} for e in data["experts"]]
    for e in data["experts"]:
        if scopes and e["id"] in scopes:
            e["scope"] = scopes[e["id"]]
    return build_system_version(type(base).model_validate(data), REPO_ROOT)


def _plan(keys, rationale="r"):
    return {"focus_areas": [{"provision_keys": keys, "question": "Who is affected?",
                             "rationale": rationale}]}  # fmt: skip


def _finding(key, source, quote):
    return {
        "provision_key": key, "affected_actor": "providers", "impact": "impact",
        "mechanism": "mechanism", "evidence": [{"source_id": source, "quote": quote}],
        "confidence": 0.8,
    }  # fmt: skip


def _quote(text: str, words: int = 12) -> str:
    return " ".join(text.split()[:words])


@pytest.fixture(scope="module")
def corpus():
    return load_default_corpus()


@pytest.fixture
def verdicts(monkeypatch):
    """The validation node's verdicts, as {(agent, source_id): reason}."""
    from womm.graph import synthesis

    seen: dict = {}
    real = synthesis.validate_findings

    def spy(findings, *args, **kwargs):
        outcome = real(findings, *args, **kwargs)
        agents = {f.finding_id: f.agent for f in findings}
        for v in outcome.report.verdicts:
            seen[(agents[v.finding_id], v.source_id)] = v.reason
        return outcome

    monkeypatch.setattr(synthesis, "validate_findings", spy)
    return seen


async def _run(fixture, corpus, scenario, *, sv, plan, experts=None, events=None):
    script = {"planner": [plan], "expert": [{"findings": []}] * 3, "synthesis": [EMPTY_SYNTHESIS]}
    script.update(experts or {})
    backend = FakeBackend(script)

    async def collect(e):
        events.append(e)

    result = await run_scenario(
        scenario, sv=sv, fixture=fixture, backends={"fake": backend},
        decisions=StubDecisionService(), code_identity=CodeIdentity(git_sha="t", dirty=False),
        run_id="run_scoped", corpus=corpus, on_event=collect if events is not None else None,
    )  # fmt: skip
    return result, backend


def _expert_call(backend, agent):
    (call,) = [c for c in backend.calls if c.role_name == "expert" and c.agent == agent]
    return call


def _mentions(text: str, key: str) -> bool:
    return re.search(re.escape(key) + r"(?![\w/])", text) is not None


# ---------- AE4: unscoped versions are unchanged ----------


async def test_ae4_unscoped_experts_get_v0_sources_and_bytes(fixture, corpus):
    keys = fixture.scenario(SME).provision_keys
    plan = {"focus_areas": [{"provision_keys": keys[:2], "question": "q", "rationale": "r"}]}
    result, backend = await _run(fixture, corpus, SME, sv=_fake(), plan=plan)
    assert result.status == RunStatus.succeeded
    expected = [f"{PROPOSAL}/art_{n}" for n in (53, 54, 55, 71)] + MEMOS
    for agent in ("fiscal", "legal", "stakeholder"):
        call = _expert_call(backend, agent)
        assert [m[0] for m in SOURCE_ID.findall(call.user_content)] == expected
        digest = hashlib.sha256((call.system_prompt + "\x00" + call.user_content).encode())
        assert digest.hexdigest()[:16] == PRESET_HASHES[f"{SME}/expert/{agent}"]
    # Unscoped experts are logged too: one granted_text record per scenario key, by agent.
    assert [(r.agent, r.key, r.status) for r in result.retrievals] == [
        (agent, k, "granted_text") for agent in ("legal", "fiscal", "stakeholder") for k in keys
    ]


# ---------- AE2: only retrieved sources are citable ----------


async def test_ae2_unretrieved_article_quote_is_unknown_source(fixture, corpus, verdicts):
    """Legal (unscoped, v0.3) retrieves only proposal Art 29 in an explore run. A verbatim
    quote of proposal Art 72 is in the run's source union but not in Legal's retrieval."""
    art72 = corpus.sources[f"{PROPOSAL}/art_72"].text
    art29 = corpus.sources[f"{PROPOSAL}/art_29"].text
    experts = {
        "expert/legal": [{"findings": [
            _finding(ART26, f"{PROPOSAL}/art_72", _quote(art72)),
            _finding(ART26, f"{PROPOSAL}/art_29", _quote(art29)),
        ]}],
    }  # fmt: skip
    result, _ = await _run(
        fixture, corpus, "eval_whole_proposal", sv=_fake(), plan=_plan([ART26]), experts=experts
    )
    assert verdicts == {
        ("legal", f"{PROPOSAL}/art_72"): "unknown_source",
        ("legal", f"{PROPOSAL}/art_29"): "ok",
    }
    (bad,) = [f for f in result.board if f.evidence[0].source_id.endswith("art_72")]
    assert [q.finding_id for q in result.dossier.open_questions
            if q.reason == "evidence_unresolved"] == [bad.finding_id]  # fmt: skip


async def test_ae2_preset_unscoped_quote_outside_the_scenario(fixture, corpus, verdicts):
    art72 = corpus.sources[f"{PROPOSAL}/art_72"].text
    experts = {"expert/legal": [{"findings": [_finding(K71, f"{PROPOSAL}/art_72", _quote(art72))]}]}
    await _run(fixture, corpus, SME, sv=_fake(), plan=_plan([K71]), experts=experts)
    assert verdicts == {("legal", f"{PROPOSAL}/art_72"): "unknown_source"}


# ---------- AE1: Fiscal reads obligation views only ----------


async def test_ae1_scoped_fiscal_has_no_article_text(fixture, corpus):
    keys = fixture.scenario(SME).provision_keys
    result, backend = await _run(fixture, corpus, SME, sv=_fake(V1), plan=_plan(keys))
    assert result.status == RunStatus.succeeded
    user = _expert_call(backend, "fiscal").user_content
    kinds = SOURCE_ID.findall(user)
    assert kinds and {k for _, k in kinds} == {"obligations"}
    assert [sid for sid, _ in kinds] == [
        f"{PROPOSAL}/obligations/art_{n}" for n in (53, 54, 55, 71)
    ]
    for n in (53, 54, 55, 71):
        text = fixture.sources[f"{PROPOSAL}/art_{n}"].text
        assert f'id="{PROPOSAL}/art_{n}"' not in user
        assert text[:200] not in user  # the article text itself is never shown
    assert "memorandum" not in user  # Fiscal does not see the memorandum
    fiscal = [r for r in result.retrievals if r.agent == "fiscal"]
    assert [(r.key, r.status) for r in fiscal] == [(k, "granted_obligations") for k in keys]
    # RunResult.retrievals: by expert order, then request order.
    assert [r.agent for r in result.retrievals] == ["legal"] * 4 + ["fiscal"] * 4 + [
        "stakeholder"
    ] * 4


async def test_scoped_quote_of_text_fiscal_cannot_see_is_unknown_source(fixture, corpus, verdicts):
    art71 = fixture.sources[f"{PROPOSAL}/art_71"].text
    experts = {
        "expert/fiscal": [{"findings": [_finding(K71, f"{PROPOSAL}/art_71", _quote(art71))]}]
    }
    result, _ = await _run(fixture, corpus, SME, sv=_fake(V1), plan=_plan([K71]), experts=experts)
    (f,) = result.board
    assert [q.finding_id for q in result.dossier.open_questions] == [f.finding_id]
    assert result.grounding.passed == 0 and result.grounding.total == 1
    assert verdicts == {("fiscal", f"{PROPOSAL}/art_71"): "unknown_source"}


async def test_scoped_fiscal_can_cite_its_obligation_view(fixture, corpus):
    sv = _fake(V1)
    _, probe = await _run(fixture, corpus, SME, sv=sv, plan=_plan([K71]))
    user = _expert_call(probe, "fiscal").user_content
    view = user.split(f'<source id="{PROPOSAL}/obligations/art_71"', 1)[1].split("</source>")[0]
    span = next(line for line in view.splitlines() if line.startswith("span: "))[6:]
    experts = {"expert/fiscal": [{"findings": [
        _finding(K71, f"{PROPOSAL}/obligations/art_71", _quote(span, 10))
    ]}]}  # fmt: skip
    result, _ = await _run(fixture, corpus, SME, sv=sv, plan=_plan([K71]), experts=experts)
    assert result.grounding.passed == result.grounding.total == 1


# ---------- leak check ----------


@pytest.mark.parametrize("scenario", ["eval_whole_proposal", "omnibus_2026"])
async def test_scoped_prompts_leak_no_delta_scope_or_rationale(fixture, corpus, scenario):
    from womm.graph.build import explore_inputs

    diff = explore_inputs(fixture.scenario(scenario), fixture, corpus)["diff"]
    wanted = [ART26, ANNEX3, "ai_act/art/6", "ai_act/art/99", "ai_act/art/50", "ai_act/art/10"]
    changed = diff.keys()
    keys = [k for k in wanted if k in set(changed)] or changed[:6]
    result, backend = await _run(
        fixture, corpus, scenario, sv=_fake(V1), plan=_plan(keys, rationale=RATIONALE)
    )
    assert result.status == RunStatus.succeeded, result.error
    called = [a for a in ("legal", "fiscal", "stakeholder")
              if any(c.agent == a for c in backend.calls)]  # fmt: skip
    assert "legal" in called
    for agent in called:
        user = _expert_call(backend, agent).user_content
        for tag in ("[added]", "[modified]", "[removed]"):
            assert tag not in user, (agent, tag)
        assert RATIONALE not in user
        records = [r for r in result.retrievals if r.agent == agent]
        assert [r.key for r in records] == keys
        for r in records:
            if not r.granted:
                assert not _mentions(user, r.key), (agent, r.key)
    if "fiscal" in called:
        fiscal = _expert_call(backend, "fiscal").user_content
        assert {k for _, k in SOURCE_ID.findall(fiscal)} <= {"obligations"}


# ---------- memorandum channel ----------


async def test_memorandum_quote_cannot_carry_an_out_of_scope_provision(fixture, corpus, verdicts):
    """Annex III has no obligation records: Fiscal is refused it. A verbatim memorandum quote
    filed on Annex III is provision_out_of_scope. Legal, granted Annex III and the memorandum,
    may use the same quote."""
    memo = fixture.sources[MEMOS[0]].text
    quote = _quote(memo.split("\n\n", 2)[2])
    experts = {
        "expert/fiscal": [{"findings": [_finding(ANNEX3, MEMOS[0], quote)]}],
        "expert/legal": [{"findings": [_finding(ANNEX3, MEMOS[0], quote)]}],
    }
    events: list = []
    result, backend = await _run(
        fixture, corpus, "eval_whole_proposal", sv=_fake(V1), plan=_plan([ART26, ANNEX3]),
        experts=experts, events=events,
    )  # fmt: skip
    statuses = {(r.agent, r.key): r.status for r in result.retrievals}
    assert statuses[("fiscal", ANNEX3)] == "out_of_scope"
    assert statuses[("legal", ANNEX3)] == "granted_text"
    validate = next(e for e in events if e.node == "validate" and e.event == "finished")
    assert validate.payload["grounding"] == {"passed": 1, "total": 2}
    assert verdicts == {
        ("fiscal", MEMOS[0]): "provision_out_of_scope",
        ("legal", MEMOS[0]): "ok",
    }
    (fiscal,) = [f for f in result.board if f.agent == "fiscal"]
    assert [q.finding_id for q in result.dossier.open_questions
            if q.reason == "evidence_unresolved"] == [fiscal.finding_id]  # fmt: skip


# ---------- edge cases and the log ----------


async def test_expert_with_no_granted_key_is_not_called(fixture, corpus):
    """A scoped expert whose retrieval grants nothing (here: only the memorandum) is skipped:
    no LLM call, a no_data_in_scope entry and a dossier note, and the run is not degraded."""
    scope = {"text": [], "obligations": "none", "sees_memorandum": True,
             "hypothesis": "Nothing but the memorandum."}  # fmt: skip
    sv = _fake(scopes={"fiscal": scope})
    keys = fixture.scenario(SME).provision_keys
    result, backend = await _run(fixture, corpus, SME, sv=sv, plan=_plan(keys))
    assert not [c for c in backend.calls if c.agent == "fiscal"]
    assert result.status == RunStatus.succeeded
    (failure,) = result.failures
    assert (failure.agent, failure.error_kind) == ("fiscal", "no_data_in_scope")
    assert failure.attempts == 0
    assert "fiscal: no data within its scope for this run" in result.dossier.notes
    fiscal = [r for r in result.retrievals if r.agent == "fiscal"]
    assert [(r.key, r.status, r.source_ids) for r in fiscal] == [
        (k, "out_of_scope", []) for k in keys
    ]
    assert not [u for u in result.usage if u.agent == "fiscal"]


async def test_every_expert_without_data_fails_the_run_without_calls(fixture, corpus):
    nothing = {"text": [], "obligations": "none", "hypothesis": "Sees nothing."}
    sv = _fake(scopes={e: nothing for e in ("legal", "fiscal", "stakeholder")})
    result, backend = await _run(fixture, corpus, SME, sv=sv, plan=_plan([K71]))
    assert not [c for c in backend.calls if c.role_name in ("expert", "synthesis")]
    assert result.status == RunStatus.failed
    assert {f.error_kind for f in result.failures} == {"no_data_in_scope"}
    for agent in ("legal", "fiscal", "stakeholder"):
        assert f"{agent}: no data within its scope for this run" in result.dossier.notes


async def test_unscoped_expert_is_called_even_without_keys(fixture, corpus):
    """The skip is for scoped experts only: unscoped (v0) experts keep their behaviour."""
    result, backend = await _run(fixture, corpus, SME, sv=_fake(), plan=_plan([K71]))
    assert {c.agent for c in backend.calls if c.role_name == "expert"} == {
        "legal", "fiscal", "stakeholder"
    }  # fmt: skip
    assert not result.failures


async def test_records_survive_llm_error_and_unexpected_exception(fixture, corpus):
    keys = fixture.scenario(SME).provision_keys
    experts = {
        "expert/fiscal": [LLMError("timeout", "t")],
        "expert/legal": [RuntimeError("boom")],
    }
    result, _ = await _run(fixture, corpus, SME, sv=_fake(V1), plan=_plan(keys), experts=experts)
    assert result.status == RunStatus.degraded
    assert {f.agent: f.error_kind for f in result.failures} == {
        "fiscal": "timeout", "legal": "process_error"
    }  # fmt: skip
    by_agent = {a: [r for r in result.retrievals if r.agent == a] for a in ("legal", "fiscal")}
    assert [r.status for r in by_agent["legal"]] == ["granted_text"] * 4
    assert [r.status for r in by_agent["fiscal"]] == ["granted_obligations"] * 4


async def test_refusals_of_a_failed_expert_stay_in_the_log(fixture, corpus):
    scope = {"text": [K71], "obligations": "none", "hypothesis": "One text only."}
    sv = _fake(scopes={"stakeholder": scope})
    experts = {"expert/stakeholder": [LLMError("timeout", "t")]}
    result, _ = await _run(fixture, corpus, SME, sv=sv, plan=_plan([K71]), experts=experts)
    records = [r for r in result.retrievals if r.agent == "stakeholder"]
    assert [r.status for r in records].count("out_of_scope") == 3
    assert [r.status for r in records].count("granted_text") == 1
    assert {f.agent: f.error_kind for f in result.failures} == {"stakeholder": "timeout"}


async def test_expert_event_carries_counts_and_no_texts(fixture, corpus):
    events: list = []
    keys = fixture.scenario(SME).provision_keys
    scope = {"text": keys[:1], "obligations": "none", "hypothesis": "One text only."}
    sv = _fake(scopes={"fiscal": scope})
    await _run(fixture, corpus, SME, sv=sv, plan=_plan(keys), events=events)
    by = {(e.node, e.event): e for e in events}
    fiscal = by[("expert_fiscal", "finished")].payload
    assert fiscal["retrievals"] == {"fiscal": {"granted": 1, "refused": 3}}
    assert by[("expert_legal", "finished")].payload["retrievals"] == {
        "legal": {"granted": 4, "refused": 0}
    }
    dumped = json.dumps(fiscal)
    assert "Member States" not in dumped and "source_id" not in dumped
    assert len(dumped) < 300


# ---------- no change kind through the scoped changes index ----------

DELTA_WORDS = re.compile(r"\b(?:before|after):|\b(?:added|modified|removed)\b", re.IGNORECASE)
SOURCE_TAG = re.compile(r"<source [^>]*>")


def _non_text_parts(user: str) -> str:
    """Everything an expert reads except the legal texts themselves: the head, the changes
    index, the focus areas and every source tag (id, kind, title). Source texts are legal text
    and may say "modified" on their own."""
    head, _, sources = user.partition("Citable sources")
    return head + "\n".join(SOURCE_TAG.findall(sources))


def _diff(fixture, corpus, scenario):
    from womm.diff import diff_versions
    from womm.graph.build import explore_inputs

    sc = fixture.scenario(scenario)
    if sc.mode == "explore":
        return explore_inputs(sc, fixture, corpus)["diff"]
    return diff_versions(*fixture.scenario_versions(scenario), keys=sc.provision_keys)


@pytest.mark.parametrize("scenario", ["demo_penalties_amended", "omnibus_2026"])
async def test_scoped_no_delta_prompt_never_reveals_the_change_kind(fixture, corpus, scenario):
    diff = _diff(fixture, corpus, scenario)
    by_kind: dict[str, list[str]] = {}
    for c in diff.changes:
        by_kind.setdefault(c.kind, []).append(c.provision_key)
    keys = [k for kind in sorted(by_kind) for k in by_kind[kind][:2]]
    assert "modified" in by_kind
    result, backend = await _run(fixture, corpus, scenario, sv=_fake(V1), plan=_plan(keys))
    assert result.status == RunStatus.succeeded, result.error
    called = {c.agent for c in backend.calls if c.role_name == "expert"}
    assert "legal" in called
    for agent in called:
        user = _expert_call(backend, agent).user_content
        assert DELTA_WORDS.search(_non_text_parts(user)) is None, (agent, scenario)
    # Legal sees every text: a modified key lists both versions' sources in one neutral,
    # sorted reference.
    legal = _expert_call(backend, "legal").user_content
    change = next(c for c in diff.changes if c.kind == "modified")
    ids = sorted([change.before.source_id, change.after.source_id])
    assert f"provision_key={change.provision_key} (sources: {', '.join(ids)})" in legal


async def test_scoped_expert_with_delta_keeps_before_and_after(fixture, corpus):
    scope = {"text": "all", "obligations": "none", "sees_delta": True,
             "hypothesis": "A reviewer with the delta."}  # fmt: skip
    sv = _fake(scopes={"legal": scope})
    diff = _diff(fixture, corpus, "demo_penalties_amended")
    change = next(c for c in diff.changes if c.kind == "modified")
    _, backend = await _run(
        fixture, corpus, "demo_penalties_amended", sv=sv, plan=_plan([change.provision_key])
    )
    legal = _expert_call(backend, "legal").user_content
    assert f"- [modified] provision_key={change.provision_key} (before: Art " in legal


# ---------- focus areas reach scoped experts as keys only ----------

QUESTION = "UNIQUE-PLANNER-QUESTION-TEXT about who bears the cost"


@pytest.mark.parametrize("scenario", [SME, "omnibus_2026"])
async def test_scoped_prompt_has_no_planner_question_text(fixture, corpus, scenario):
    keys = (
        fixture.scenario(SME).provision_keys
        if scenario == SME
        else [c.provision_key for c in _diff(fixture, corpus, scenario).changes][:4]
    )
    plan = {"focus_areas": [{"provision_keys": keys, "question": QUESTION, "rationale": RATIONALE}]}
    _, backend = await _run(fixture, corpus, scenario, sv=_fake(V1), plan=plan)
    calls = [c for c in backend.calls if c.role_name == "expert"]
    assert calls
    for call in calls:
        assert "UNIQUE-PLANNER-QUESTION-TEXT" not in call.user_content, call.agent
        assert RATIONALE not in call.user_content
        focus = call.user_content.split("Impact Planner focus areas:", 1)[1]
        focus = focus.split("Citable sources", 1)[0]
        assert "[keys: " in focus


async def test_unscoped_prompt_keeps_the_planner_question(fixture, corpus):
    keys = fixture.scenario(SME).provision_keys
    plan = {"focus_areas": [{"provision_keys": keys, "question": QUESTION, "rationale": RATIONALE}]}
    _, backend = await _run(fixture, corpus, SME, sv=_fake(), plan=plan)
    for call in [c for c in backend.calls if c.role_name == "expert"]:
        assert QUESTION in call.user_content and RATIONALE in call.user_content


# ---------- consolidated-target runs: no superseded obligations, marked 2024 texts ----------

CONSOLIDATED = "reg2024_1689_c20260727"
FINAL = "reg2024_1689"


def _amended_with_2024_records(corpus) -> list[str]:
    return [
        r.key
        for r in corpus.index_rows(CONSOLIDATED)
        if r.delta == "modified" and corpus.obligations[FINAL].get(r.key)
    ]


async def test_omnibus_scoped_experts_get_no_2024_obligation_view(fixture, corpus):
    keys = _amended_with_2024_records(corpus)[:4]
    result, backend = await _run(fixture, corpus, "omnibus_2026", sv=_fake(V1), plan=_plan(keys))
    for call in [c for c in backend.calls if c.role_name == "expert"]:
        assert f'id="{FINAL}/obligations/' not in call.user_content, call.agent
    assert not any(
        sid.startswith(f"{FINAL}/obligations/") for r in result.retrievals for sid in r.source_ids
    )
    # Fiscal (obligation records only) has nothing within its scope for amended articles.
    assert {f.agent: f.error_kind for f in result.failures}.get("fiscal") == "no_data_in_scope"


@pytest.mark.parametrize("sv_kind", ["scoped", "unscoped"])
async def test_omnibus_2024_texts_carry_the_superseded_title_marker(fixture, corpus, sv_kind):
    from womm.data.corpus import SUPERSEDED_MARK

    keys = _amended_with_2024_records(corpus)[:2]
    sv = _fake(V1) if sv_kind == "scoped" else _fake()
    _, backend = await _run(fixture, corpus, "omnibus_2026", sv=sv, plan=_plan(keys))
    legal = _expert_call(backend, "legal").user_content
    tags = {m.group(1): m.group(0) for m in re.finditer(r'<source id="([^"]+)"[^>]*>', legal)}
    for key in keys:
        old = corpus.version(FINAL).by_key()[key].source_id
        new = corpus.version(CONSOLIDATED).by_key()[key].source_id
        assert f"({SUPERSEDED_MARK})" in tags[old], tags[old]
        assert SUPERSEDED_MARK not in tags[new]
        # texts are unchanged
        assert f"{tags[old]}\n{corpus.sources[old].text}\n</source>" in legal

