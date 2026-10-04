"""Explore-mode scenarios: the Planner reads the corpus index (no text) and its selection is
bounded; preset scenarios keep their v0 inputs byte for byte."""

import hashlib

import pytest

from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT
from womm.data.corpus import load_default_corpus
from womm.decisions.stub import StubDecisionService
from womm.diff import diff_versions
from womm.graph.build import explore_inputs, run_scenario
from womm.graph.planner import FocusArea, FocusPlan, cap_plan
from womm.graph.router import router_state
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity, RunStatus
from womm.models.system_version import build_system_version, load_system_version

EXPLORE = ("eval_whole_proposal", "consolidated_whole_act", "omnibus_2026")
CONSOLIDATED = "reg2024_1689_c20260727"
ART26 = "ai_act/art/26"
EMPTY_SYNTHESIS = {
    "impacts": [], "chains": [], "disagreements": [], "open_questions": [], "discarded": []
}  # fmt: skip


def _fake(path, **retrieval):
    """A SystemVersion file with every role on the fake backend (and optional caps)."""
    base = load_system_version(path, REPO_ROOT).spec
    data = base.model_dump()
    for role in ("planner", "synthesis", "judge"):
        data[role]["backend"] = "fake"
    data["experts"] = [{**e, "role": {**e["role"], "backend": "fake"}} for e in data["experts"]]
    if retrieval:
        data["retrieval"] = {**(data.get("retrieval") or {"max_provisions": 8}), **retrieval}
    return build_system_version(type(base).model_validate(data), REPO_ROOT)


V1 = REPO_ROOT / "system_versions" / "v1.0-scoped.yaml"


@pytest.fixture(scope="module")
def corpus():
    return load_default_corpus()


def _index_keys(user: str) -> list[str]:
    return [line.split(" | ", 1)[0] for line in user.splitlines() if line.startswith("ai_act/")]


def _plan(keys, per_area=4):
    areas = [keys[i : i + per_area] for i in range(0, len(keys), per_area)] or [[]]
    return {
        "focus_areas": [
            {
                "provision_keys": a,
                "question": f"Who is affected by {a[0] if a else '-'}?",
                "rationale": "Selected from the index line.",
            }
            for a in areas
        ]  # fmt: skip
    }


def _longest_keys(corpus, fixture, scenario_id, n=40):
    """The diff's keys ordered by total text length (both versions), longest first: the
    adversarial selection for the prompt budget."""
    diff = explore_inputs(fixture.scenario(scenario_id), fixture, corpus)["diff"]

    def size(c):
        return sum(len(p.text) for p in (c.before, c.after) if p is not None)

    return [c.provision_key for c in sorted(diff.changes, key=size, reverse=True)][:n]


async def _run(fixture, corpus, scenario_id, planner_step, sv=None):
    script = {
        "planner": [planner_step],
        "expert": [{"findings": []}] * 3,
        "synthesis": [EMPTY_SYNTHESIS],
    }
    backend = FakeBackend(script)
    result = await run_scenario(
        scenario_id,
        sv=sv or _fake(V1),
        fixture=fixture,
        backends={"fake": backend},
        decisions=StubDecisionService(),
        code_identity=CodeIdentity(git_sha="test", dirty=False),
        run_id="run_explore",
        corpus=corpus,
    )
    return result, backend


def _calls(backend, role):
    return [c for c in backend.calls if c.role_name == role]


# ---------- scenarios and fixture rules ----------


def test_explore_scenarios_name_corpus_versions(fixture, corpus):
    for sid in EXPLORE:
        s = fixture.scenario(sid)
        assert s.mode == "explore" and s.provision_keys == []
        for vid in (s.before_version, s.after_version):
            if vid is not None:
                corpus.version(vid)  # raises if unknown
    whole = fixture.scenario("eval_whole_proposal")
    assert (whole.kind, whole.before_version, whole.after_version) == (
        "evaluation", None, "com2021_206"
    )  # fmt: skip
    assert fixture.scenario("consolidated_whole_act").before_version is None
    omnibus = fixture.scenario("omnibus_2026")
    assert (omnibus.before_version, omnibus.after_version) == ("reg2024_1689", CONSOLIDATED)


def test_preset_scenarios_keep_their_mode(fixture):
    presets = {s.scenario_id for s in fixture.scenarios.values() if s.mode == "preset"}
    assert presets == {
        "eval_provider_compliance_costs",
        "eval_sme_impacts",
        "demo_penalties_amended",
    }


# ---------- Planner input ----------


@pytest.mark.parametrize("scenario_id", EXPLORE)
async def test_explore_planner_reads_index_without_text(fixture, corpus, scenario_id, capsys):
    result, backend = await _run(
        fixture, corpus, scenario_id, lambda s, u: _plan(_index_keys(u)[:3])
    )
    assert result.status == RunStatus.succeeded, result.error
    (call,) = _calls(backend, "planner")
    assert call.system_prompt == load_system_version(V1, REPO_ROOT).prompt_file(
        "prompts/v1/planner_explore.md"
    )
    assert len(call.user_content) < 20_000
    diff = explore_inputs(fixture.scenario(scenario_id), fixture, corpus)["diff"]
    assert _index_keys(call.user_content) == diff.keys()  # changed keys only, diff order
    # No provision text (headings are allowed; an excerpt past the heading is not).
    for c in diff.changes:
        for p in (c.before, c.after):
            if p is not None and len(p.text) > 400:
                mid = len(p.text) // 2
                assert p.text[mid : mid + 100] not in call.user_content
    assert "Select at most 8 provision keys" in call.user_content
    with capsys.disabled():
        print(f"\n{scenario_id}: Planner user content {len(call.user_content)} chars, "
              f"{len(diff.changes)} index rows")  # fmt: skip


async def test_whole_act_index_has_no_art26_text(fixture, corpus):
    _, backend = await _run(fixture, corpus, "consolidated_whole_act", lambda s, u: _plan([ART26]))
    (call,) = _calls(backend, "planner")
    art26 = corpus.version(CONSOLIDATED).by_key()[ART26].text
    assert art26 not in call.user_content
    assert art26.split("\n")[0][:80] not in call.user_content
    assert f"{ART26} | " in call.user_content


async def test_no_prior_version_index_shows_run_change_kind(fixture, corpus):
    """The proposal evaluation must not learn how the proposal was later amended: the index's
    stored delta (proposal -> 2024) is replaced by the run's own change kind."""
    _, backend = await _run(fixture, corpus, "eval_whole_proposal", lambda s, u: _plan([ART26]))
    (call,) = _calls(backend, "planner")
    rows = [line for line in call.user_content.splitlines() if line.startswith("ai_act/")]
    assert rows and all(line.split(" | ")[2] == "added" for line in rows)
    assert " | modified" not in call.user_content and " | removed" not in call.user_content


async def test_preset_without_explore_prompt_falls_back_to_prompt(fixture, corpus):
    sv = _fake(DEFAULT_SYSTEM_VERSION)
    assert sv.spec.planner.explore_prompt is None and sv.spec.retrieval is None
    result, backend = await _run(
        fixture, corpus, "consolidated_whole_act", lambda s, u: _plan([ART26]), sv=sv
    )
    assert result.status == RunStatus.succeeded
    (call,) = _calls(backend, "planner")
    assert call.system_prompt == sv.prompt_text(sv.spec.planner)
    assert "Select at most 8 provision keys" in call.user_content  # default cap


# ---------- caps ----------


def test_cap_plan_truncates_in_planner_order():
    keys = [f"k{i}" for i in range(40)]
    plan = FocusPlan.model_validate(_plan(keys, per_area=10))
    capped, notes = cap_plan(plan, 20, lambda p: True)
    kept = [k for a in capped.focus_areas for k in a.provision_keys]
    assert kept == keys[:20]
    assert len(notes) == 1 and "selected 40" in notes[0] and "k20" in notes[0]


def test_cap_plan_drops_keys_over_the_prompt_budget():
    plan = FocusPlan(
        focus_areas=[FocusArea(provision_keys=["a", "big", "b"], question="q", rationale="r")]
    )
    capped, notes = cap_plan(plan, 8, lambda p: "big" not in p.focus_areas[0].provision_keys)
    assert capped.focus_areas[0].provision_keys == ["a", "b"]
    assert notes == ["dropped big: an expert prompt would exceed retrieval.max_prompt_chars"]


async def test_forty_keys_with_cap_twenty_keeps_twenty_and_notes_it(fixture, corpus):
    sv = _fake(V1, max_provisions=20, max_prompt_chars=10_000_000)
    _, backend0 = await _run(fixture, corpus, "consolidated_whole_act", lambda s, u: _plan([ART26]))
    keys = _index_keys(_calls(backend0, "planner")[0].user_content)[:40]
    result, backend = await _run(
        fixture, corpus, "consolidated_whole_act", _plan(keys, per_area=10), sv=sv
    )
    assert result.status == RunStatus.succeeded
    expert = _calls(backend, "expert")[0].user_content
    focus = expert.split("Impact Planner focus areas:", 1)[1].split("Citable sources", 1)[0]
    assert all(k in focus for k in keys[:20]) and not any(f"{k}," in focus for k in keys[20:])
    (note,) = [n for n in result.dossier.notes if "max_provisions" in n]
    assert "selected 40" in note and "kept the first 20" in note


async def test_key_outside_the_diff_is_dropped(fixture, corpus):
    """omnibus_2026 diffs only the amended and inserted units; an unchanged article is not in
    the diff, so restrict_to drops it (existing behaviour, now on corpus keys)."""
    unchanged = next(r.key for r in corpus.index_rows(CONSOLIDATED) if r.delta == "unchanged")
    changed = next(r.key for r in corpus.index_rows(CONSOLIDATED) if r.delta == "modified")
    result, backend = await _run(fixture, corpus, "omnibus_2026", _plan([unchanged, changed]))
    assert result.status == RunStatus.succeeded
    expert = _calls(backend, "expert")[0].user_content
    assert f"provision_key={changed} " in expert
    assert f"provision_key={unchanged} " not in expert


async def test_zero_valid_keys_fails_the_run_with_a_planner_error(fixture, corpus):
    result, backend = await _run(
        fixture, corpus, "consolidated_whole_act", _plan(["ai_act/art/999", "nope"])
    )
    assert result.status == RunStatus.failed
    assert result.error and result.error.startswith("planner failed: [no_provisions]")
    assert not _calls(backend, "expert")


async def test_empty_explore_plan_fails_the_run(fixture, corpus):
    result, backend = await _run(fixture, corpus, "consolidated_whole_act", {"focus_areas": []})
    assert result.status == RunStatus.failed and "no_provisions" in result.error
    assert not _calls(backend, "expert")


# ---------- expert prompt budget ----------


@pytest.mark.parametrize("scenario_id", EXPLORE)
@pytest.mark.parametrize("sv_path", [V1, DEFAULT_SYSTEM_VERSION], ids=["v1.0-scoped", "v0.3"])
async def test_every_expert_prompt_stays_under_60k(fixture, corpus, scenario_id, sv_path):
    """Adversarial Planner: it asks for the 40 longest provisions of the diff. With
    max_provisions 8 and the prompt cap, every expert prompt stays under 60k characters."""
    keys = _longest_keys(corpus, fixture, scenario_id)
    result, backend = await _run(fixture, corpus, scenario_id, _plan(keys), sv=_fake(sv_path))
    assert result.status == RunStatus.succeeded, result.error
    experts = _calls(backend, "expert")
    assert len(experts) == 3
    for c in experts:
        assert len(c.system_prompt) + len(c.user_content) < 60_000, c.agent
    focus = experts[0].user_content.split("Impact Planner focus areas:", 1)[1]
    focus = focus.split("Citable sources", 1)[0]
    assert sum(k in focus for k in keys) <= 8
    assert any("max_provisions" in n for n in result.dossier.notes)


async def test_experts_see_only_the_selected_provisions(fixture, corpus):
    keys = [ART26, "ai_act/art/50"]
    _, backend = await _run(fixture, corpus, "consolidated_whole_act", _plan(keys))
    user = _calls(backend, "expert")[0].user_content
    assert user.count("provision_key=") == 2
    assert f'id="{CONSOLIDATED}/art_26"' in user and f'id="{CONSOLIDATED}/art_50"' in user
    assert user.count("<source ") == 2  # no memorandum: it explains the 2021 proposal
    assert "memorandum" not in user


async def test_proposal_explore_keeps_the_memorandum(fixture, corpus):
    _, backend = await _run(fixture, corpus, "eval_whole_proposal", _plan([ART26]))
    user = _calls(backend, "expert")[0].user_content
    assert 'id="com2021_206/art_29"' in user  # proposal Art 29 is final Art 26
    for slug in ("context", "legal_basis", "other_elements"):
        assert f'id="com2021_206/memorandum/{slug}"' in user


# ---------- law version labels ----------


@pytest.mark.parametrize("scenario_id", ["consolidated_whole_act", "omnibus_2026"])
async def test_consolidated_runs_label_nothing_pre_omnibus(fixture, corpus, scenario_id):
    result, backend = await _run(
        fixture, corpus, scenario_id, lambda s, u: _plan(_index_keys(u)[:4])
    )
    law = result.dossier.law_version
    assert (law.version_id, law.pre_omnibus, law.note) == (CONSOLIDATED, False, None)
    for c in backend.calls:
        assert "pre-omnibus" not in (c.system_prompt + c.user_content).lower()


async def test_adopted_2024_run_is_labelled_pre_omnibus(run):
    from .conftest import good_script

    result, _ = await run(good_script(EMPTY_SYNTHESIS), scenario="demo_penalties_amended")
    law = result.dossier.law_version
    assert law.version_id == "reg2024_1689" and law.pre_omnibus
    assert law.note.startswith("Pre-Omnibus")


async def test_proposal_runs_are_not_pre_omnibus(run):
    from .conftest import good_script

    result, _ = await run(good_script(EMPTY_SYNTHESIS))
    assert result.dossier.law_version.version_id == "com2021_206"
    assert not result.dossier.law_version.pre_omnibus


# ---------- Router ----------


def test_explore_router_state_uses_index_and_focus(fixture, corpus):
    inputs = explore_inputs(fixture.scenario("consolidated_whole_act"), fixture, corpus)
    focus = FocusPlan.model_validate(_plan([ART26]))
    text = router_state(
        {"scenario_id": "consolidated_whole_act", "mode": "explore", "focus": focus, **inputs}
    )
    assert inputs["index_lines"][ART26] in text
    assert "the Planner selected 1" in text
    art26 = corpus.version(CONSOLIDATED).by_key()[ART26].text
    assert art26[:100] not in text
    assert len(text) < 3_000
    other = next(k for k in inputs["index_lines"] if k != ART26)
    assert inputs["index_lines"][other] not in text


# ---------- preset byte stability ----------

# sha256(system + NUL + user)[:16] of every Planner and expert call for the preset scenarios,
# computed on the code before explore mode existed (same script as _preset_hashes).
PRESET_HASHES = {
    "demo_penalties_amended/expert/fiscal": "aa237ce7b8191dec",
    "demo_penalties_amended/expert/legal": "52009fba68f71fa4",
    "demo_penalties_amended/expert/stakeholder": "e877948dd82715f4",
    "demo_penalties_amended/planner/-": "2ae792a7a7ba444d",
    "eval_provider_compliance_costs/expert/fiscal": "6b69d30f2b59932a",
    "eval_provider_compliance_costs/expert/legal": "63b6d49980fd384d",
    "eval_provider_compliance_costs/expert/stakeholder": "b5d1d80049e0139b",
    "eval_provider_compliance_costs/planner/-": "d71a9a91a2193c5f",
    "eval_sme_impacts/expert/fiscal": "e391c06bfd858715",
    "eval_sme_impacts/expert/legal": "82848028c9c45f4d",
    "eval_sme_impacts/expert/stakeholder": "398af2ee360b5c05",
    "eval_sme_impacts/planner/-": "751aa9557bd1548e",
}


async def test_preset_planner_and_expert_inputs_are_byte_identical(fixture, corpus):
    sv = _fake(DEFAULT_SYSTEM_VERSION)
    got = {}
    for sid in ("eval_provider_compliance_costs", "eval_sme_impacts", "demo_penalties_amended"):
        keys = fixture.scenario(sid).provision_keys
        plan = {"focus_areas": [{"provision_keys": keys[:2], "question": "q", "rationale": "r"}]}
        script = {"planner": [plan], "expert": [{"findings": []}] * 3,
                  "synthesis": [EMPTY_SYNTHESIS]}  # fmt: skip
        backend = FakeBackend(script)
        await run_scenario(
            sid, sv=sv, fixture=fixture, backends={"fake": backend},
            decisions=StubDecisionService(), code_identity=CodeIdentity(git_sha="t", dirty=False),
            run_id="run_h", corpus=corpus,
        )  # fmt: skip
        for c in backend.calls:
            digest = hashlib.sha256((c.system_prompt + "\x00" + c.user_content).encode())
            got[f"{sid}/{c.role_name}/{c.agent or '-'}"] = digest.hexdigest()[:16]
    assert got == PRESET_HASHES


async def test_preset_planner_still_reads_changes_with_text(fixture):
    from womm.graph import render

    sv = _fake(V1)
    s = fixture.scenario("eval_sme_impacts")
    before, after = fixture.scenario_versions(s.scenario_id)
    diff = diff_versions(before, after, keys=s.provision_keys)
    plan = {
        "focus_areas": [{"provision_keys": s.provision_keys, "question": "q", "rationale": "r"}]
    }
    _, backend = await _run(fixture, None, s.scenario_id, plan, sv=sv)
    (call,) = _calls(backend, "planner")
    assert call.system_prompt == sv.prompt_text(sv.spec.planner)  # not the explore prompt
    assert call.user_content == "Regulatory changes:\n\n" + render.changes_with_text(diff)
