"""U5 cycle on the fake backend: the topology stage (one new expert for a persistent unowned
pattern), budgets, and the cycle's single candidate for the gate."""

import asyncio
import json

import pytest

from womm.evolve.cycle import (
    choose_candidate,
    run_cycle,
    run_prompt_stage,
    run_topology_stage,
    unowned_patterns,
)
from womm.evolve.edits import build_candidate, validate_diff
from womm.evolve.failure_memory import CaseRun, FailureEvent
from womm.evolve.gepa_adapter import CaseRef, WommAdapter
from womm.evolve.proposers import Budget
from womm.evolve.replay import judge_version
from womm.llm.fake import FakeBackend

from ..graph.conftest import fake_sv
from .test_gepa_adapter import CASE, CASES, CODE, MARKER, TRAIN

OTHER = CASE.model_copy(update={"case_id": "case_other_act", "fixture": "other_act"})
CATEGORY = "social_environmental"
TARGET = f"missed_impact/{CATEGORY}/none"
PROMPT = (
    "You are the Workforce Analyst in a regulatory impact assessment system for EU legislation. "
    "Your lens: effects on workers, employment, skills and working conditions. Report impacts "
    "as findings with provision_key, affected_actor, mechanism, impact, verbatim evidence of at "
    "least 8 words and confidence; an empty list is better than an unsupported finding."
)


def proposal(**over):
    def step(_system, _user):
        return {
            "id": "workforce",
            "domain": "workforce",
            "prompt_text": PROMPT,
            "router_gloss": "employment, skills and working conditions",
            "rationale": "no expert covers workforce effects",
            "target_pattern": TARGET,
            **over,
        }

    return step


async def seed_pattern(db, version_id, cases=(CASE, OTHER), runs=2, missed=2, owner="none",
                       judge=None, sha=CODE.git_sha):  # fmt: skip
    """Failure Memory rows: each case's first expected impact, categorised, missed in
    ``missed`` of ``runs`` runs, owned by ``owner``, on the case's own split, scored by
    ``judge`` (default: the seed's pinned judge) on code ``sha``."""
    judge = judge or judge_version(fake_sv())
    events, case_runs = [], []
    for case in cases:
        item = case.expected_impacts[0]
        for rep in range(1, runs + 1):
            run_id = f"seed_{case.case_id}_{rep}"
            case_runs.append(
                CaseRun(
                    case_id=case.case_id,
                    fixture=case.fixture,
                    split=case.split,
                    run_id=run_id,
                    repetition=rep,
                    system_version=version_id,
                    judge_version=judge,
                    git_sha=sha,
                )
            )
            if rep <= missed:
                events.append(
                    FailureEvent(
                        kind="missed_impact",
                        case_id=case.case_id,
                        fixture=case.fixture,
                        split=case.split,
                        item_id=item.expected_id,
                        category=CATEGORY,
                        touching_agents=[] if owner == "none" else [owner],
                        owner=owner,
                        run_id=run_id,
                        repetition=rep,
                        system_version=version_id,
                        judge_version=judge,
                        git_sha=sha,
                    )
                )
    await db.record_failure_events(events, case_runs)


@pytest.fixture
def two_proposals(monkeypatch):
    monkeypatch.setitem(CASES, "train", [*TRAIN, OTHER])


def topology_kw(env, **budget):
    return dict(
        seed=env["sv"],
        parent=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=Budget(val_repetitions=2, **budget),
        cycle_id="c1",
    )


# ---------------------------------------------------------------- topology stage


async def test_persistent_unowned_pattern_yields_one_new_expert(env, two_proposals):
    await seed_pattern(env["db"], env["sv"].version_id)
    env["proposer"].backend = FakeBackend({"improvement_planner/topology": [proposal()]})
    result = await run_topology_stage(**topology_kw(env))
    child = result.candidate
    assert result.reason == "proposed" and child is not None
    assert [e.id for e in child.spec.experts] == ["legal", "fiscal", "stakeholder", "workforce"]
    new = child.spec.experts[-1]
    assert new.router_gloss == "employment, skills and working conditions"
    assert child.prompt_text(new.role) == PROMPT
    row = await env["archive"].get(child.version_id)
    assert (row["origin"], row["parent_id"]) == ("topology", env["sv"].version_id)
    assert row["diff"]["target_pattern"] == TARGET
    assert row["diff"]["rendered"]["summary"]["experts_added"] == ["workforce"]
    assert row["proposer"]["prompt"] == "prompts/evolution/propose_expert.md"
    # The proposer saw the pattern and the missed train impacts of both proposals.
    user = env["proposer"].backend.calls[0].user_content
    assert TARGET in user and "other_act" in user
    assert CASE.expected_impacts[0].affected_actor in user
    # The candidate was replayed on val with four expert nodes.
    assert any(c.agent == "workforce" for c in env["graph"].calls)


async def test_pattern_in_one_proposal_does_not_trigger(env, two_proposals):
    await seed_pattern(env["db"], env["sv"].version_id, cases=(CASE,))
    env["proposer"].backend = FakeBackend({})
    result = await run_topology_stage(**topology_kw(env))
    assert result.candidate is None and "2+ proposals" in result.reason
    assert env["proposer"].backend.calls == []


async def test_owned_or_unknown_persistence_does_not_trigger(env, two_proposals):
    await seed_pattern(env["db"], env["sv"].version_id, owner="fiscal")
    rows = await env["view"].failure_patterns(env["sv"].version_id)
    assert unowned_patterns(rows) == []
    single = [
        {
            "kind": "missed_impact",
            "owner": "none",
            "persistence": "unknown",
            "persistent_misses": 0,
            "proposals": ["a", "b"],
        }
    ]
    assert unowned_patterns(single) == []


async def test_invalid_expert_proposals_are_rejected(env, two_proposals):
    await seed_pattern(env["db"], env["sv"].version_id)
    env["proposer"].backend = FakeBackend(
        {
            "improvement_planner/topology": [
                proposal(target_pattern="missed_impact/other/none"),
                proposal(id="fiscal"),
            ]
        }
    )
    wrong = await run_topology_stage(**topology_kw(env))
    assert wrong.candidate is None and "targets" in wrong.rejections[0]["reason"]
    dup = await run_topology_stage(**topology_kw(env))
    assert dup.candidate is None and "already exists" in dup.rejections[0]["reason"]
    assert await env["archive"].children(env["sv"].version_id) == []


async def test_expert_count_is_capped_at_the_seed_plus_one(env, two_proposals):
    sv = env["sv"]
    op = {
        "op": "add_expert",
        "id": "workforce",
        "domain": "workforce",
        "prompt_text": PROMPT,
        "router_gloss": "employment effects",
    }
    parent = build_candidate(sv, validate_diff(sv, [op]))
    await seed_pattern(env["db"], parent.version_id)
    env["proposer"].backend = FakeBackend({})
    result = await run_topology_stage(**{**topology_kw(env), "parent": parent})
    assert result.candidate is None and "cap" in result.reason


# ---------------------------------------------------------------- val stays for selection only

VAL_CANARY = "CANARY_val_text_4f2a"


def val_canary_case(case_id="case_val_canary", fixture="val_act"):
    """A val case whose golden text is a canary: it may be replayed and scored, never shown to
    the Improvement Planner."""
    impacts = [
        e.model_copy(update={"affected_actor": f"{VAL_CANARY}_actor",
                             "mechanism": f"{VAL_CANARY}_mechanism",
                             "impact": f"{VAL_CANARY}_impact"})
        for e in CASE.expected_impacts
    ]  # fmt: skip
    return CASE.model_copy(update={"case_id": case_id, "fixture": fixture, "split": "val",
                                   "expected_impacts": impacts})  # fmt: skip


async def test_topology_proposal_never_sees_val_golden_text(env, two_proposals, monkeypatch):
    val = val_canary_case()
    monkeypatch.setitem(CASES, "val", [*CASES["val"], val])
    # The pattern spans two train proposals and, on val, a third one.
    await seed_pattern(env["db"], env["sv"].version_id, cases=(CASE, OTHER, val))
    env["proposer"].backend = FakeBackend({"improvement_planner/topology": [proposal()]})
    result = await run_topology_stage(**topology_kw(env))
    assert result.candidate is not None
    user = env["proposer"].backend.calls[0].user_content
    assert CASE.expected_impacts[0].affected_actor in user  # train text is the input
    assert VAL_CANARY not in user and "val_act" not in user and val.case_id not in user


async def test_a_val_only_pattern_does_not_trigger_a_new_expert(env, monkeypatch):
    vals = [val_canary_case(), val_canary_case("case_val_canary_2", "val_act_2")]
    monkeypatch.setitem(CASES, "val", [*CASES["val"], *vals])
    await seed_pattern(env["db"], env["sv"].version_id, cases=vals)
    env["proposer"].backend = FakeBackend({})
    result = await run_topology_stage(**topology_kw(env))
    assert result.candidate is None and "2+ proposals" in result.reason
    assert env["proposer"].backend.calls == []


async def test_prompt_proposals_never_see_val_golden_text(env, monkeypatch):
    val = val_canary_case()
    monkeypatch.setitem(CASES, "val", [val])
    a = WommAdapter(base=env["sv"], view=env["view"], evaluator=env["evaluator"],
                    proposer=env["proposer"], budget=Budget(train_repetitions=1,
                                                            val_repetitions=1))  # fmt: skip
    seed = a.seed_candidate()
    batch = [CaseRef("train", TRAIN[0].case_id), CaseRef("val", val.case_id)]
    out = await asyncio.to_thread(a.evaluate, batch, seed, True)
    data = await asyncio.to_thread(a.make_reflective_dataset, seed, out, ["expert:fiscal"])
    assert data["expert:fiscal"]  # the train case is still a record
    assert {r["Inputs"]["split"] for r in data["expert:fiscal"]} == {"train"}
    assert VAL_CANARY not in json.dumps(data)
    await asyncio.to_thread(a.propose_new_texts, seed, data, ["expert:fiscal"])
    # A whole GEPA run (val replayed for the Pareto front) never shows val text either.
    await run_prompt_stage(base=env["sv"], view=env["view"], evaluator=env["evaluator"],
                           proposer=env["proposer"],
                           budget=Budget(max_metric_calls=20, train_repetitions=1,
                                         val_repetitions=1))  # fmt: skip
    assert env["planner_llm"].calls
    assert all(VAL_CANARY not in c.user_content for c in env["planner_llm"].calls)


async def test_reflective_patterns_aggregate_train_events_only(env, monkeypatch):
    val = val_canary_case()
    monkeypatch.setitem(CASES, "val", [val])
    a = WommAdapter(base=env["sv"], view=env["view"], evaluator=env["evaluator"],
                    proposer=env["proposer"], budget=Budget(train_repetitions=2))  # fmt: skip
    seed = a.seed_candidate()
    out = await asyncio.to_thread(a.evaluate, [CaseRef("train", TRAIN[0].case_id)], seed, True)
    train_events = await env["view"].failure_events(env["sv"].version_id, ("train",))
    e = next(e for e in train_events if e.kind == "missed_impact")
    # The same (kind, category, owner) on a val proposal, scored by the same judge and code.
    val_events = [e.model_copy(update={"case_id": val.case_id, "fixture": "val_act",
                                       "split": "val", "run_id": f"val_run_{i}"})
                  for i in (1, 2)]  # fmt: skip
    val_runs = [CaseRun(case_id=val.case_id, fixture="val_act", split="val", run_id=v.run_id,
                        repetition=v.repetition, system_version=v.system_version,
                        judge_version=v.judge_version, git_sha=v.git_sha)
                for v in val_events]  # fmt: skip
    await env["db"].record_failure_events(val_events, val_runs)
    data = await asyncio.to_thread(a.make_reflective_dataset, seed, out, ["expert:fiscal"])
    patterns = [p for r in data["expert:fiscal"] for p in r["Feedback"]["patterns"]]
    assert patterns and all("val_act" not in p["proposals"] for p in patterns)


# ---------------------------------------------------------------- one judge, one code version


@pytest.mark.parametrize("stale", [{"judge": "judge_old"}, {"sha": "sha_old"}])
async def test_patterns_from_another_judge_or_code_do_not_trigger(env, two_proposals, stale):
    await seed_pattern(env["db"], env["sv"].version_id, **stale)
    env["proposer"].backend = FakeBackend({})
    result = await run_topology_stage(**topology_kw(env))
    assert result.candidate is None and "2+ proposals" in result.reason
    assert env["proposer"].backend.calls == []


async def test_reflective_patterns_are_this_judge_and_code_only(env):
    a = WommAdapter(base=env["sv"], view=env["view"], evaluator=env["evaluator"],
                    proposer=env["proposer"], budget=Budget(train_repetitions=2))  # fmt: skip
    seed = a.seed_candidate()
    out = await asyncio.to_thread(a.evaluate, [CaseRef("train", TRAIN[0].case_id)], seed, True)
    e = next(e for e in await env["view"].failure_events(env["sv"].version_id)
             if e.kind == "missed_impact")  # fmt: skip
    stale = [e.model_copy(update={"fixture": f"stale_{k}", "run_id": f"stale_{k}", **upd})
             for k, upd in (("judge", {"judge_version": "judge_old"}),
                            ("code", {"git_sha": "sha_old"}))]  # fmt: skip
    await env["db"].record_failure_events(stale, [])
    data = await asyncio.to_thread(a.make_reflective_dataset, seed, out, ["expert:fiscal"])
    patterns = [p for r in data["expert:fiscal"] for p in r["Feedback"]["patterns"]]
    assert patterns and not any("stale" in "".join(p["proposals"]) for p in patterns)


# ---------------------------------------------------------------- selection beyond noise


async def _with_val(env, text, coverage, grounding=0.9, n=4, sd=0.2, case_sd=None):
    """An archived candidate (an edit of the fiscal prompt) with val split metrics."""
    sv = env["sv"]
    role = next(e for e in sv.spec.experts if e.id == "fiscal").role
    if text is None:
        cand = sv
    else:
        op = {"op": "edit_prompt", "role": "expert:fiscal",
              "new_text": sv.prompt_text(role) + text}  # fmt: skip
        cand = build_candidate(sv, validate_diff(sv, [op]))
        await env["archive"].archive(cand, origin="gepa", parent_id=sv.version_id)
    rows = [{"level": "split", "subject": "", "metric": m, "n": n, "mean": v, "sd": sd}
            for m, v in (("coverage", coverage), ("grounding", grounding))]  # fmt: skip
    if n == 1:
        rows += [{"level": "case", "subject": "c1", "metric": "coverage", "n": 3,
                  "mean": coverage, "sd": case_sd}]  # fmt: skip
    await env["archive"].record_metrics(
        cand.version_id, "val", rows, batch_id=f"rb_{cand.version_id[-8:]}",
        judge_version=judge_version(sv), git_sha=CODE.git_sha, full_split=True,
    )  # fmt: skip
    return cand


async def _choose(env, front, k=1.0, tolerance=0.02):
    return await choose_candidate(env["view"], env["sv"], [c.version_id for c in front],
                                  tolerance, judge_version(env["sv"]), k)  # fmt: skip


SUFFIX = " Also weigh second-order effects on small firms, and say how strong the evidence is."


async def test_a_gain_within_noise_keeps_the_base(env):
    await _with_val(env, None, coverage=0.5)
    small = await _with_val(env, SUFFIX, coverage=0.6)  # gain 0.1 < 1 x SE 0.141
    assert await _choose(env, [small]) == env["sv"].version_id
    assert await _choose(env, [small], k=0.5) == small.version_id  # 0.1 > 0.5 x 0.141


async def test_a_gain_beyond_noise_is_chosen(env):
    await _with_val(env, None, coverage=0.5)
    small = await _with_val(env, SUFFIX, coverage=0.6)
    big = await _with_val(env, SUFFIX + " Then check again.", coverage=0.8)
    assert await _choose(env, [small, big]) == big.version_id


async def test_grounding_outside_tolerance_keeps_the_base(env):
    await _with_val(env, None, coverage=0.5, grounding=0.9)
    loose = await _with_val(env, SUFFIX, coverage=0.9, grounding=0.8)
    assert await _choose(env, [loose]) == env["sv"].version_id


async def test_one_val_case_uses_the_repetition_spread(env):
    await _with_val(env, None, coverage=0.5, n=1, sd=None, case_sd=0.3)
    cand = await _with_val(env, SUFFIX, coverage=0.7, n=1, sd=None, case_sd=0.3)
    # SE of the difference: sqrt(2 x 0.3^2 / 3) = 0.245 > 0.2
    assert await _choose(env, [cand]) == env["sv"].version_id
    assert await _choose(env, [cand], k=0.5) == cand.version_id


async def test_a_base_without_val_metrics_is_kept(env):
    cand = await _with_val(env, SUFFIX, coverage=0.9)
    assert await _choose(env, [cand]) == env["sv"].version_id


# ---------------------------------------------------------------- budgets


async def test_max_usd_stops_before_any_proposal(env):
    result = await run_prompt_stage(
        base=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=Budget(max_usd=0.0),
    )
    assert result.best.version_id == env["sv"].version_id
    assert result.archived == [] and env["planner_llm"].calls == []


async def test_metric_budget_reached_mid_stage_keeps_every_candidate(env):
    budget = Budget(max_metric_calls=8, train_repetitions=1, val_repetitions=1)
    result = await run_prompt_stage(
        base=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=budget,
    )
    assert result.archived and MARKER not in "".join(result.best.prompts.values())
    for vid in result.archived:
        assert (await env["archive"].load_candidate(vid)).version_id == vid


# ---------------------------------------------------------------- the cycle


async def test_cycle_both_stages_names_one_candidate(env):
    budget = Budget(max_metric_calls=60, train_repetitions=1, val_repetitions=2)
    result = await run_cycle(
        base=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=budget,
        cycle_id="c1",
    )
    assert result.chosen.version_id == result.prompt.best.version_id != env["sv"].version_id
    # The prompt edit removed every miss, so no topology change is proposed.
    assert result.topology.candidate is None and "pattern" in result.topology.reason


async def test_topology_only_cycle_keeps_the_base_when_the_expert_does_not_help(env, two_proposals):
    await seed_pattern(env["db"], env["sv"].version_id)
    env["proposer"].backend = FakeBackend({"improvement_planner/topology": [proposal()]})
    result = await run_cycle(
        base=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=Budget(val_repetitions=2),
        stage="topology",
    )
    assert result.topology.candidate is not None
    assert result.chosen.version_id == env["sv"].version_id  # same val coverage: no change


async def test_unknown_stage_is_refused(env):
    with pytest.raises(ValueError, match="stage"):
        await run_cycle(
            base=env["sv"],
            view=env["view"],
            evaluator=env["evaluator"],
            proposer=env["proposer"],
            budget=Budget(),
            stage="judge",
        )
