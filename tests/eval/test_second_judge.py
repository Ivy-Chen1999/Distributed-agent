"""The holdout's blind second judge (plan 2026-10-26-001): input, money, resume, acceptance."""

from __future__ import annotations

import asyncio
import json

import pytest
from langsmith.run_helpers import get_tracing_context

from womm.data.fixtures import fixture_dir, load_fixture
from womm.eval import second_judge as sj
from womm.eval.drafting import DraftingConfig, load_drafting_config
from womm.llm.fake import FakeBackend
from womm.models.system_version import RoleConfig

from .draft_factory import KEYS, draft_dict, seal, to_draft
from .test_tiebreak import IA, holdout_scenario, run_holdout, verdict

MODEL = "openai:gpt-6.1-sol"
PROMPT = "p/second_judge.md"


class PricedFake(FakeBackend):
    """The fake backend with a list-priced cost per call, as the api backend records."""

    def __init__(self, script, usd: float = 0.01):
        super().__init__(script)
        self.usd = usd

    async def _invoke(self, *a, **k):
        raw, usage = await super()._invoke(*a, **k)
        return raw, usage.model_copy(update={"cost_usd": self.usd})


def config(model: str = MODEL, prompt_text: str = "SECOND JUDGE PROMPT") -> DraftingConfig:
    names = ("drafter", "recall", "judge_anchor", "judge_derivability", "judge_category")
    roles = {n: RoleConfig(backend="fake", model=f"model-{n}", prompt=f"p/{n}.md") for n in names}
    roles["second_judge"] = RoleConfig(backend="fake", model=model, prompt=PROMPT, max_retries=1)
    prompts = {f"p/{n}.md": n for n in names} | {PROMPT: prompt_text}
    return DraftingConfig(roles=roles, prompts=prompts, prompt_hashes={})


def out(ids, v="keep"):
    return {"verdicts": [{"item_id": i, "verdict": v, "reason": "the text says so"} for i in ids]}


def items(n: int) -> list[sj.JudgeItem]:
    return [
        sj.JudgeItem(
            f"x{k}", "effect", {"impact": f"effect {k}"}, "official report", "", ("t",), ()
        )
        for k in range(n)
    ]


def run(coro):
    return asyncio.run(coro)


def spend(tmp_path, max_usd=1.0):
    return sj.Spend(max_usd, ledger=tmp_path / "spend.json")


# ----------------------------------------------------------------------------- the blind input


def _settled_holdout():
    data = draft_dict("holdout") | {"scenario_id": "eval_spot_sealed"}
    data["expected_impacts"][0]["flags"] = ["number_mismatch"]
    ruled, _ = run_holdout(
        to_draft(seal(data)),
        [verdict("c90_e05", category="market_competition", reason="SECRET-TIEBREAK")],
    )
    return ruled


def test_the_judge_sees_no_claude_decision_and_no_original_id():
    """Covers AE4."""
    draft = _settled_holdout()
    fixture = load_fixture(fixture_dir("data_act"))
    judged = sj.draft_items(draft, fixture, holdout_scenario(), IA, "")
    content, ids = sj.user_content(judged)
    for leak in ("SECRET-TIEBREAK", "number_mismatch", "auto_accepted", "llm_kept",
                 "llm_dropped", "market_competition", "compliance_cost", '"judge', '"review"',
                 '"tiebreak"', '"category"', '"flags"', '"derivability"', "why_missing",
                 "c90_"):  # fmt: skip
        assert leak not in content, leak
    assert sorted(ids.values()) == sorted(i.item_id for i in draft.items())
    assert all(nid.startswith("i") for nid in ids)


def test_holdout_provisions_come_from_the_private_scenario():
    draft = _settled_holdout()
    fixture = load_fixture(fixture_dir("data_act"))
    scenario = holdout_scenario()
    assert scenario.scenario_id not in fixture.scenarios
    judged = sj.draft_items(draft, fixture, scenario, IA, "")
    keys = {k for it in judged for k, _ in it.provisions}
    assert keys and keys <= set(KEYS)
    assert all(text for it in judged for _, text in it.provisions)


def test_an_rsb_anchored_item_gets_its_passage_from_the_opinion():
    data = draft_dict("holdout") | {"scenario_id": "eval_spot_sealed"}
    data["important_omissions"][0]["anchor"]["against"] = "rsb"
    quote = data["important_omissions"][0]["ia_anchor"]
    draft = to_draft(seal(data))
    fixture = load_fixture(fixture_dir("data_act"))
    judged = sj.draft_items(draft, fixture, holdout_scenario(), IA, f"Board text. {quote} More.")
    omission = next(it for it in judged if it.kind == "omission")
    assert omission.source == "review board opinion" and "Board text." in omission.passages[0]


# ----------------------------------------------------------------------------- judging


def test_items_are_batched_and_mapped_back(tmp_path):
    fake = PricedFake({sj.ROLE: [out([f"i{n:02d}" for n in range(1, 16)]),
                                 out([f"i{n:02d}" for n in range(1, 6)], "drop")]})  # fmt: skip
    got = run(sj.judge(items(20), config(), {"fake": fake}, spend(tmp_path)))
    assert len(fake.calls) == 2
    assert [got[f"x{k}"].verdict for k in (0, 14, 15, 19)] == ["keep", "keep", "drop", "drop"]


def test_a_missing_verdict_is_unsure_and_an_unknown_id_is_ignored(tmp_path):
    reply = out(["i01", "i99"])
    fake = PricedFake({sj.ROLE: [reply]})
    got = run(sj.judge(items(2), config(), {"fake": fake}, spend(tmp_path)))
    assert got["x0"].verdict == "keep" and got["x1"].verdict == "unsure"
    assert set(got) == {"x0", "x1"}


def test_calls_run_untraced(tmp_path):
    seen = []

    def reply(system, user):
        seen.append(get_tracing_context()["enabled"])
        return out(["i01"])

    run(sj.judge(items(1), config(), {"fake": PricedFake({sj.ROLE: [reply]})}, spend(tmp_path)))
    assert seen == [False]


# ----------------------------------------------------------------------------- money


def test_a_cap_below_one_estimated_call_refuses_before_any_call(tmp_path):
    fake = PricedFake({sj.ROLE: [out(["i01"])]})
    with pytest.raises(sj.BudgetExceeded, match="over its cap"):
        run(sj.judge(items(1), config(), {"fake": fake}, spend(tmp_path, max_usd=0.0001)))
    assert fake.calls == []


def test_the_ledger_total_caps_all_second_judge_spend(tmp_path):
    ledger = tmp_path / "spend.json"
    ledger.write_text(json.dumps({"total_usd": 4.99, "calls": []}))
    fake = PricedFake({sj.ROLE: [out(["i01"])]})
    with pytest.raises(sj.BudgetExceeded, match="all second-judge spend"):
        run(sj.judge(items(1), config(), {"fake": fake}, sj.Spend(10.0, ledger=ledger)))
    assert fake.calls == []


def test_spend_is_recorded_in_the_ledger(tmp_path):
    s = spend(tmp_path)
    run(sj.judge(items(1), config(), {"fake": PricedFake({sj.ROLE: [out(["i01"])]}, 0.02)}, s))
    assert s.spent == pytest.approx(0.02) and s.recorded() == pytest.approx(0.02)


def test_a_model_without_a_list_price_is_refused():
    with pytest.raises(sj.SecondJudgeError, match="no list price"):
        sj.role_of(config(model="openai:unpriced-model"))


# ----------------------------------------------------------------------------- resume


def test_a_rerun_pays_only_for_missing_batches(tmp_path):
    store_path = tmp_path / "case.json"
    sha = sj.prompt_sha256(config())
    first = PricedFake({sj.ROLE: [out([f"i{n:02d}" for n in range(1, 16)]),
                                  RuntimeError("rate limited")]})  # fmt: skip
    with pytest.raises(RuntimeError):
        run(sj.judge(items(20), config(), {"fake": first}, spend(tmp_path),
                     sj.BatchStore(store_path, MODEL, sha)))  # fmt: skip
    second = PricedFake({sj.ROLE: [out([f"i{n:02d}" for n in range(1, 6)])]})
    got = run(sj.judge(items(20), config(), {"fake": second}, spend(tmp_path),
                       sj.BatchStore(store_path, MODEL, sha)))  # fmt: skip
    assert len(second.calls) == 1 and len(got) == 20


def test_a_store_of_another_prompt_is_refused(tmp_path):
    path = tmp_path / "case.json"
    sj.BatchStore(path, MODEL, "a" * 64).put("k", [])
    with pytest.raises(sj.SecondJudgeError, match="another model or prompt"):
        sj.BatchStore(path, MODEL, "b" * 64)


# ----------------------------------------------------------------------------- config


def test_the_repo_config_has_the_second_judge_on_the_api():
    role = load_drafting_config().roles.second_judge
    assert role is not None and role.backend == "api" and role.model == MODEL
    assert sj.role_of(load_drafting_config()) == role


def test_a_second_judge_sharing_the_drafters_model_is_refused():
    cfg = config()
    roles = cfg.roles.model_dump() | {
        "second_judge": {"backend": "fake", "model": "model-drafter", "prompt": PROMPT}
    }
    with pytest.raises(ValueError, match="a grader must use a different model"):
        DraftingConfig(roles=roles)


# ----------------------------------------------------------------------------- acceptance


def test_cited_locations_are_parsed():
    assert sj.cited_locations("platform_work/ia_full.txt:1068; 1824-1836") == [
        ("platform_work/ia_full.txt", 1068, 1068),
        ("platform_work/ia_full.txt", 1824, 1836),
    ]
    assert sj.cited_locations("dsa/ia_full.txt:205; proposal Art 17(1)(c)") == [
        ("dsa/ia_full.txt", 205, 205)
    ]
    assert sj.cited_locations("cra/ia_full.txt:706,714") == [
        ("cra/ia_full.txt", 706, 706),
        ("cra/ia_full.txt", 714, 714),
    ]


def _verdicts(ids, v):
    return {i: sj.SecondJudgeVerdict(item_id=i, verdict=v, reason="r") for i in ids}


def test_gate_a_fails_a_judge_that_keeps_everything():
    cases = sj.load_regression()
    ids = [c["id"] for c in cases]
    result = sj.score_pass(cases, _verdicts(ids, "keep"), _verdicts(ids, "keep"))
    assert not result["gate_a"]["passed"] and result["gate_b"]["passed"]


def test_gate_b_fails_a_judge_that_answers_unsure_to_everything():
    cases = sj.load_regression()
    ids = [c["id"] for c in cases]
    result = sj.score_pass(cases, _verdicts(ids, "unsure"), _verdicts(ids, "unsure"))
    assert result["gate_a"]["passed"] and not result["gate_b"]["passed"]
    assert (result["gate_b"]["kept"], result["gate_b"]["controls"]) == (0, 15)


def test_a_judge_keeping_exactly_the_supported_points_passes():
    cases = sj.load_regression()
    supported = [c["id"] for c in cases if c["expected"] == "supported"]
    others = [c["id"] for c in cases if c["expected"] != "supported"]
    w = _verdicts(supported, "keep") | _verdicts(others, "drop")
    result = sj.score_pass(cases, w, w)
    assert result["gate_a"]["passed"] and result["gate_b"]["passed"]
    assert result["gate_b"]["kept"] == 15 and result["gate_a"]["points"] == 15
    partial = sj.score_pass(cases, w, w, exclude=sj.CODEX_LABELLED)
    assert partial["gate_b"]["passed"] is None and partial["gate_b"]["controls"] == 13


def _regression_case(cid, kind, expected, source, point_id="c04_e01"):
    return {"id": cid, "case": "case_04_x", "point": point_id, "kind": kind,
            "expected": expected, "claim": "c", "why": "WHY-TEXT", "source": source,
            "input": {"fixture": "cra", "anchored_on": "ia", "section": "6.3",
                      "anchor": "the anchor sentence",
                      "point": {"impact": "an effect"}}}  # fmt: skip


def test_acceptance_inputs_hide_the_labels_and_show_cited_passages(tmp_path):
    ia_dir = tmp_path / "ia" / "cra"
    ia_dir.mkdir(parents=True)
    lines = ["filler line"] * 20
    lines[4], lines[14] = "Here is the anchor sentence in the report.", "FAR SUPPORT line."
    (ia_dir / "ia_full.txt").write_text("\n".join(lines))
    golden = tmp_path / "golden"
    golden.mkdir()
    (golden / "case_04_x.yaml").write_text(
        "scenario_id: eval_none\nexpected_impacts:\n- expected_id: c04_e01\n  provision_keys: []\n"
    )
    cases = [
        _regression_case("w99", "window", "supported", "cra/ia_full.txt:15"),
        _regression_case("o99", "over_inference", "not_supported", "cra/ia_full.txt:5"),
    ]
    w, e, notes = sj.acceptance_items(cases, tmp_path / "ia", golden)
    content = sj.user_content(w)[0] + sj.user_content(e)[0]
    for label in ("window", "over_inference", "supported", "WHY-TEXT", "ia_full.txt"):
        assert label not in content, label
    assert len(w) == 2 and len(e) == 1
    assert "[[" in e[0].passages[0] and "FAR SUPPORT" in e[0].passages[1]
    assert len(w[0].passages) == 1
    assert any("no provisions shown" in n for n in notes)
