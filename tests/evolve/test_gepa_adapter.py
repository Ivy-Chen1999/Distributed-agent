"""U5 spike and adapter: GEPA's engine with WOMM's evaluator (U4 replay, pinned judge), reflective
dataset (Failure Memory through PlannerView), proposer (LLMBackend, structured PromptEdit) and
U2 typed edits. Everything on the fake backend and a throwaway database."""

import asyncio
import json
import sys

import gepa
import pytest

from womm.decisions.stub import StubDecisionService
from womm.evolve.archive import Archive
from womm.evolve.cycle import ReplayEvaluator, run_prompt_stage
from womm.evolve.edits import build_candidate, validate_diff
from womm.evolve.gepa_adapter import CaseRef, WommAdapter
from womm.evolve.planner_view import PlannerView
from womm.evolve.proposers import Budget, Proposer, load_evolution_config
from womm.evolve.replay import ReplayStore, ReplayWorker, judge_version
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity
from womm.models.system_version import RoleConfig

from ..eval.test_run_eval_fake import CASE
from ..graph.conftest import K55, K71, PLAN, Q55, Q71, fake_sv, finding

MARKER = "SME_MARKER"
SENTINEL = "SME_SENTINEL"
TRAIN = [CASE, CASE.model_copy(update={"case_id": "case_02b_copy"})]
VAL = [CASE.model_copy(update={"case_id": "case_02v_copy", "split": "val"})]
CASES = {"train": TRAIN, "val": VAL}
CODE = CodeIdentity(git_sha="sha1", dirty=False)
EXTRA = (
    f"\n\n{MARKER}: for every provision that supports small and medium-sized enterprises, also "
    "report the cost relief it gives them and whether it offsets their compliance burden, as a "
    "separate finding with its own mechanism."
)
LONG = 500


def load_cases(split):
    return CASES.get(split, [])


def fiscal(system, _user):
    """The fake Fiscal expert finds the SME impact only once its prompt carries the marker."""
    if MARKER in system:
        return {"findings": [finding(K55, "com2021_206/art_55", Q55, impact=SENTINEL)]}
    return {"findings": []}


def synth(_system, user):
    rows = json.loads(user.split("Validated findings:\n", 1)[1])
    return {
        "impacts": [
            {"impact_id": f"I{i}", "summary": r["impact"], "finding_ids": [r["finding_id"]]}
            for i, r in enumerate(rows, 1)
        ],
        "chains": [],
        "disagreements": [],
        "open_questions": [],
        "discarded": [],
    }


def judge(_system, user):
    covered = SENTINEL in user
    expected = json.loads(user.split("Expected impacts:\n", 1)[1].split("\n\nImportant")[0])
    omissions = json.loads(user.split("Important omissions:\n", 1)[1])
    return {
        "expected": [
            {
                "expected_id": e["expected_id"],
                "covered": covered,
                "impact_id": "I1" if covered else None,
                "justification": "j",
            }
            for e in expected
        ],
        "omissions": [
            {
                "omission_id": o["omission_id"],
                "addressed": True,
                "impact_id": "I1",
                "justification": "j",
            }
            for o in omissions
        ],
    }


def improve(_system, user):
    """The fake Improvement Planner: append the marker to whatever component it is asked about."""
    role = user.split("Component: ", 1)[1].split("\n", 1)[0]
    current = user.split("Current prompt:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
    return {"role": role, "new_text": current + EXTRA, "rationale": f"targets {role}"}


def graph_script() -> dict:
    return {
        "planner": [PLAN] * LONG,
        "expert/legal": [{"findings": [finding(K71, "com2021_206/art_71", Q71, "providers")]}]
        * LONG,
        "expert/fiscal": [fiscal] * LONG,
        "expert/stakeholder": [{"findings": []}] * LONG,
        "synthesis": [synth] * LONG,
        "judge": [judge] * LONG,
    }


def config(proposer=improve):
    cfg = load_evolution_config()
    fake = {"backend": "fake", "model": "fake-model"}
    roles = cfg.roles.model_copy(
        update={
            "reflect": RoleConfig(**fake, prompt=cfg.roles.reflect.prompt),
            "propose_expert": RoleConfig(**fake, prompt=cfg.roles.propose_expert.prompt),
        }
    )
    return cfg.model_copy(update={"roles": roles})


def graph_calls(backend: FakeBackend) -> int:
    return sum(c.role_name == "planner" for c in backend.calls)


@pytest.fixture
async def env(db, database_url, tmp_path):
    sv = fake_sv()
    archive = Archive(db)
    await archive.archive(sv, origin="seed")
    graph = FakeBackend(graph_script())
    store = ReplayStore(db, load_cases=load_cases)
    worker = ReplayWorker(
        store=store,
        archive=archive,
        judge_sv=sv,
        backends={"fake": graph},
        decisions=StubDecisionService(),
        code=CODE,
        runs_dir=tmp_path / "runs",
    )
    evaluator = ReplayEvaluator(archive_store=archive, store=store, worker=worker)
    planner_llm = FakeBackend({"improvement_planner": [improve] * LONG})
    proposer = Proposer(planner_llm, config())
    view = PlannerView(database_url, runs_dir=tmp_path / "runs", env={}, load_cases=load_cases)
    await view.open()
    yield {
        "sv": sv,
        "archive": archive,
        "graph": graph,
        "store": store,
        "evaluator": evaluator,
        "proposer": proposer,
        "planner_llm": planner_llm,
        "view": view,
        "db": db,
    }
    await view.close()


def adapter(env, **budget) -> WommAdapter:
    return WommAdapter(
        base=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=Budget(**budget),
        cycle_id="c1",
    )


# ---------------------------------------------------------------- (a) evaluate through replay


async def test_evaluate_replays_with_the_pinned_judge_and_is_cached(env):
    a = adapter(env, val_repetitions=2)
    seed = a.seed_candidate()
    out = await asyncio.to_thread(a.evaluate, [CaseRef("val", VAL[0].case_id)], seed, True)
    assert out.scores == [0.0] and out.num_metric_calls == 2
    assert out.trajectories[0]["version_id"] == env["sv"].version_id
    assert len(out.trajectories[0]["run_ids"]) == 2
    async with env["db"].pool.connection() as conn:
        rows = await (
            await conn.execute("SELECT judge_version, split FROM replay_items")
        ).fetchall()
    assert {(r["judge_version"], r["split"]) for r in rows} == {(judge_version(env["sv"]), "val")}
    before = graph_calls(env["graph"])
    again = await asyncio.to_thread(a.evaluate, [CaseRef("val", VAL[0].case_id)], seed, False)
    assert again.scores == out.scores and again.trajectories is None
    assert graph_calls(env["graph"]) == before  # finished replays are never re-run


async def test_evaluate_refuses_a_holdout_instance(env):
    a = adapter(env)
    with pytest.raises(ValueError, match="train/val"):
        await asyncio.to_thread(a.evaluate, [CaseRef("holdout", "h1")], a.seed_candidate())


async def test_grounding_below_the_floor_scores_zero(env):
    a = adapter(env)
    ref = CaseRef("val", "x")
    from womm.eval.evaluators import CaseScore

    low = CaseScore(case_id="x", scenario_id="s", outcome="scored", coverage=1.0, grounding=0.2)
    ok = low.model_copy(update={"grounding": 0.9})
    assert a._summarise(ref, [low])["score"] == 0.0
    assert a._summarise(ref, [ok])["score"] == 1.0
    assert a._summarise(ref, [])["score"] == 0.0


# ---------------------------------------------------------------- (b) reflective dataset


async def test_reflective_dataset_comes_from_failure_memory(env):
    a = adapter(env)
    seed = a.seed_candidate()
    batch = [CaseRef("train", c.case_id) for c in TRAIN]
    out = await asyncio.to_thread(a.evaluate, batch, seed, True)
    data = await asyncio.to_thread(a.make_reflective_dataset, seed, out, ["expert:fiscal"])
    records = data["expert:fiscal"]
    assert len(records) == 2
    fb = records[0]["Feedback"]
    actors = {m["affected_actor"] for m in fb["missed_expected_impacts"]}
    assert actors == {e.affected_actor for e in CASE.expected_impacts}
    assert all(
        m["owner"] == ["none"] and m["missed_in_runs"] == "2/2"
        for m in fb["missed_expected_impacts"]
    )
    assert {(p["kind"], p["owner"]) for p in fb["patterns"]} == {("missed_impact", "none")}
    assert records[0]["Generated Outputs"] == []  # the fiscal expert said nothing
    capped = WommAdapter(
        base=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=Budget(reflective_chars=1000),
    )
    small = await asyncio.to_thread(capped.make_reflective_dataset, seed, out, ["expert:fiscal"])
    assert len(json.dumps(small["expert:fiscal"])) <= 1000


# ---------------------------------------------------------------- (c)+(d) proposals as U2 edits


async def test_proposal_is_a_validated_typed_edit_archived_under_its_parent(env):
    a = adapter(env)
    seed = a.seed_candidate()
    new = await asyncio.to_thread(
        a.propose_new_texts, seed, {"expert:fiscal": []}, ["expert:fiscal"]
    )
    assert new["expert:fiscal"].endswith(EXTRA)
    call = env["planner_llm"].calls[0]
    assert call.schema.__name__ == "PromptEdit" and call.agent == "expert:fiscal"
    child = a.version_of({**seed, **new})
    expected = build_candidate(
        env["sv"],
        validate_diff(
            env["sv"],
            [{"op": "edit_prompt", "role": "expert:fiscal", "new_text": new["expert:fiscal"]}],
        ),
    )
    assert child.version_id == expected.version_id
    row = await env["archive"].get(child.version_id)
    assert (row["parent_id"], row["origin"], row["cycle_id"]) == (
        env["sv"].version_id,
        "gepa",
        "c1",
    )
    assert row["diff"]["ops"][0]["role"] == "expert:fiscal"
    assert row["diff"]["rationale"] == {"expert:fiscal": "targets expert:fiscal"}
    assert row["proposer"]["prompt"] == "prompts/evolution/reflect_prompt.md"


async def test_invalid_proposal_is_rejected_and_recorded(env):
    def judge_edit(_s, _u):
        return {"role": "judge", "new_text": "x" * 300, "rationale": "r"}

    env["proposer"].backend = FakeBackend({"improvement_planner": [judge_edit, improve]})
    a = adapter(env)
    seed = a.seed_candidate()
    new = await asyncio.to_thread(a.propose_new_texts, seed, {}, ["expert:fiscal"])
    assert new == {"expert:fiscal": seed["expert:fiscal"]}
    assert a.rejections[0]["op"] == "edit_prompt" and "judge" in a.rejections[0]["reason"]
    assert a.archived == []

    def too_short(_s, _u):
        return {"role": "expert:fiscal", "new_text": "short", "rationale": "r"}

    env["proposer"].backend = FakeBackend({"improvement_planner": [too_short]})
    await asyncio.to_thread(a.propose_new_texts, seed, {}, ["expert:fiscal"])
    assert "at least" in a.rejections[-1]["reason"] and a.archived == []


async def test_adapter_reads_through_a_planner_view_only(env):
    with pytest.raises(TypeError, match="PlannerView"):
        WommAdapter(
            base=env["sv"],
            view=env["db"],
            evaluator=env["evaluator"],
            proposer=env["proposer"],
            budget=Budget(),
        )


async def test_adapter_methods_refuse_to_block_the_event_loop(env):
    a = adapter(env)
    with pytest.raises(RuntimeError, match="worker thread"):
        a.evaluate([CaseRef("val", VAL[0].case_id)], a.seed_candidate())


# ---------------------------------------------------------------- the spike: a full GEPA run


async def test_gepa_run_finds_the_fiscal_edit_and_resumes_from_replays(env, tmp_path):
    budget = Budget(max_metric_calls=60, train_repetitions=1, val_repetitions=2)
    kw = dict(
        base=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=budget,
        cycle_id="c1",
    )
    result = await run_prompt_stage(**kw, run_dir=str(tmp_path / "gepa"))
    assert MARKER in result.best.prompt_file(
        next(e for e in result.best.spec.experts if e.id == "fiscal").role.prompt
    )
    assert result.best.version_id in result.front and result.best.version_id in result.archived
    assert result.metric_calls <= budget.max_metric_calls + 2 * len(TRAIN)
    assert "litellm" not in sys.modules  # GEPA's reflection LM was never used
    row = await env["archive"].get(result.best.version_id)
    assert row["origin"] == "gepa" and row["parent_id"] is not None

    # A restart with GEPA's run_dir re-uses its state and every finished replay.
    graph_before, llm_before = graph_calls(env["graph"]), len(env["planner_llm"].calls)
    again = await run_prompt_stage(**kw, run_dir=str(tmp_path / "gepa"))
    assert again.best.version_id == result.best.version_id
    assert graph_calls(env["graph"]) == graph_before
    assert len(env["planner_llm"].calls) == llm_before


async def test_gepa_run_without_its_state_still_reuses_replays(env):
    budget = Budget(max_metric_calls=40, train_repetitions=1, val_repetitions=1)
    kw = dict(
        base=env["sv"],
        view=env["view"],
        evaluator=env["evaluator"],
        proposer=env["proposer"],
        budget=budget,
        cycle_id="c1",
    )
    first = await run_prompt_stage(**kw)
    before = graph_calls(env["graph"])
    second = await run_prompt_stage(**kw)  # same seed, deterministic proposer: same candidates
    assert second.candidates == first.candidates
    assert graph_calls(env["graph"]) == before


def test_gepa_core_needs_no_litellm():
    assert gepa.optimize is not None and "litellm" not in sys.modules


# ---------------------------------------------------------------- U6 canary


async def test_reflection_and_proposals_never_see_a_holdout_case(env, database_url, tmp_path):
    canary = "CANARY_holdout_9c1e"
    impacts = [e.model_copy(update={"impact": canary}) for e in CASE.expected_impacts]
    sealed = CASE.model_copy(update={
        "case_id": "case_holdout_x", "split": "holdout", "notes": canary,
        "expected_impacts": impacts,
    })  # fmt: skip

    def leaky(split):  # a loader that (wrongly) also hands out a holdout case
        return [*CASES.get(split, []), sealed]

    view = PlannerView(database_url, runs_dir=tmp_path / "runs", env={}, load_cases=leaky)
    async with view:
        a = WommAdapter(base=env["sv"], view=view, evaluator=env["evaluator"],
                        proposer=env["proposer"], budget=Budget(train_repetitions=1))  # fmt: skip
        seed = a.seed_candidate()
        batch = [CaseRef("train", c.case_id) for c in TRAIN]
        out = await asyncio.to_thread(a.evaluate, batch, seed, True)
        data = await asyncio.to_thread(a.make_reflective_dataset, seed, out, list(seed))
        await asyncio.to_thread(a.propose_new_texts, seed, data, ["expert:fiscal"])
    assert data["expert:fiscal"]
    assert canary not in json.dumps(data)
    assert all(canary not in c.user_content for c in env["planner_llm"].calls)
