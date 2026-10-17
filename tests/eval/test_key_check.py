"""womm.eval.key_check: split, retrieve in Python, judge; quotes verified, board points only on
the board's own opinion (plan 2026-10-16-001, F2)."""

import asyncio
import json

import pytest

from womm.config import REPO_ROOT
from womm.eval import key_check as kc
from womm.eval.drafting import DraftingConfig, DraftingRoles, RoleConfig
from womm.eval.key_sources import load_sources
from womm.llm.fake import FakeBackend

ROLES = ("drafter", "recall", "judge_anchor", "judge_derivability", "judge_category")


def config() -> DraftingConfig:
    roles = {
        n: RoleConfig(
            backend="fake",
            model="author" if n in ("drafter", "recall") else "grader",
            prompt=f"p/{n}.md",
        )
        for n in (*ROLES, "key_split", "key_judge")
    }
    return DraftingConfig(
        roles=DraftingRoles(**roles),
        prompts={f"p/{n}": f"PROMPT {n}" for n in (*ROLES, "key_split.md", "key_judge.md")}
        | {"p/key_split.md": "SPLIT", "p/key_judge.md": "JUDGE"},
        prompt_hashes={"p/key_split.md": "a", "p/key_judge.md": "b"},
    )


def sources(tmp_path, opinion=True):
    ia = tmp_path / "ia" / "fx"
    ia.mkdir(parents=True)
    (ia / "ia_full.txt").write_text(
        "Platforms face higher costs under the option.\n"
        "The greater visibility could improve consumer confidence and trust in more complex "
        "products.\n"
        "The Commission will review the scheme after five years.\n"
    )
    if opinion:
        (ia / "rsb_opinion.txt").write_text(
            "(3) The report should say which markets are affected.\n"
        )
    (ia / "rsb.txt").write_text("The board asked about markets and competition effects.\n")
    return load_sources("fx", ia_root=tmp_path / "ia", fixtures_root=tmp_path / "none")


def point(**over):
    base = dict(
        key="case_1/c1_e01",
        fixture="fx",
        fields={"impact": "Better consumer trust in complex products, and uptake of them."},
        anchor="could improve consumer confidence and trust",
        anchored_on="ia",
    )
    return kc.PointInput(**(base | over))


def run(p, srcs, split, judge):
    fake = FakeBackend({kc.SPLIT_ROLE: [split], kc.JUDGE_ROLE: [judge]})
    return asyncio.run(kc.check_point(p, srcs, config(), {"fake": fake})), fake


SPLIT = {
    "claims": [
        {
            "id": "c1",
            "text": "Better consumer trust in complex products",
            "searches": ["consumer trust complex products"],
        },
        {"id": "c2", "text": "uptake of complex products", "searches": ["uptake"]},
    ]
}


def test_a_supported_claim_keeps_its_verified_quote_and_where(tmp_path):
    judge = {
        "claims": [
            {
                "id": "c1",
                "verdict": "supported",
                "reason": "stated",
                "evidence": [
                    {
                        "passage": "p1",
                        "quote": "improve consumer confidence and trust in more complex products",
                        "type": "ia",
                    }
                ],
            },
            {"id": "c2", "verdict": "not_found", "reason": "uptake is not stated"},
        ]
    }
    res, fake = run(point(), sources(tmp_path), SPLIT, judge)
    c1, c2 = res["claims"]
    assert c1["verdict"] == "supported" and c1["evidence"][0]["where"] == "ia_full.txt:2"
    assert c2["verdict"] == "not_found"
    sent = json.loads(fake.calls[1].user_content.split("## Claims\n\n", 1)[1])
    assert sent["claims"][0]["passages"][0]["where"] == "ia_full.txt:2"  # the anchor passage first
    assert set(res["sources"]) == {"ia", "rsb_opinion"}


def test_a_quote_not_in_its_passage_becomes_cant_tell(tmp_path):
    judge = {
        "claims": [
            {
                "id": "c1",
                "verdict": "supported",
                "reason": "r",
                "evidence": [
                    {
                        "passage": "p1",
                        "quote": "consumers will buy many more products",
                        "type": "ia",
                    }
                ],
            },
            {"id": "c2", "verdict": "not_found", "reason": "r"},
        ]
    }
    res, _ = run(point(), sources(tmp_path), SPLIT, judge)
    assert res["claims"][0]["verdict"] == "cant_tell"


def test_the_clauses_given_are_judged_as_given(tmp_path):
    judge = {"claims": [{"id": "c1", "verdict": "not_found", "reason": "r"}]}
    res, fake = run(
        point(claims=("uptake of complex products",)),
        sources(tmp_path),
        {"claims": [{"id": "c1", "text": "something else", "searches": ["uptake"]}]},
        judge,
    )
    assert [c["text"] for c in res["claims"]] == ["uptake of complex products"]
    assert json.loads(fake.calls[0].user_content.split("\n\n", 1)[1])["claims_to_check"] == [
        "uptake of complex products"
    ]


def test_a_board_point_without_the_opinion_is_cant_tell_with_no_call(tmp_path):
    p = point(
        anchored_on="rsb_opinion",
        fields={"description": "Which markets are affected."},
        anchor="which markets are affected",
    )
    fake = FakeBackend(
        {kc.SPLIT_ROLE: [{"claims": [{"id": "c1", "text": "markets", "searches": ["markets"]}]}]}
    )
    res = asyncio.run(kc.check_point(p, sources(tmp_path, opinion=False), config(), {"fake": fake}))
    assert res["opinion_missing"] and [c["verdict"] for c in res["claims"]] == ["cant_tell"]
    assert len(fake.calls) == 1  # the split only; no judgement without the opinion


def test_a_board_point_reads_only_the_opinion_and_the_law(tmp_path):
    """The procedural annex is loaded only when the opinion is missing, and then a board point
    is cant_tell without a judgement (test above); so the annex never supports a board point."""
    srcs = sources(tmp_path)
    assert "rsb_annex" not in srcs.digests
    p = point(
        anchored_on="rsb_opinion",
        fields={"description": "Competition effects."},
        anchor="which markets are affected",
    )
    shown = kc._retrieve(
        kc.Index(srcs.passages),
        p,
        kc.SplitClaim(id="c1", text="markets competition", searches=["markets"]),
    )
    assert shown and all(x.source in ("rsb_opinion", "law") for x in shown)
    judge = {
        "claims": [
            {
                "id": "c1",
                "verdict": "supported",
                "reason": "r",
                "evidence": [
                    {
                        "passage": "p1",
                        "quote": "The report should say which markets are affected",
                        "type": "ia",
                    }
                ],
            }
        ]
    }
    res, _ = run(
        p, srcs, {"claims": [{"id": "c1", "text": "markets", "searches": ["markets"]}]}, judge
    )
    assert res["claims"][0]["verdict"] == "supported"
    assert res["claims"][0]["evidence"][0]["source"] == "rsb_opinion"


def test_the_commission_never_matches_a_commission_fee():
    assert "^commis" in kc.tokens("costs for the Commission and Member States")
    assert "^commis" not in kc.tokens("an increase in commissions")


def test_inflections_meet_in_retrieval():
    assert set(kc.tokens("account suspension")) == set(kc.tokens("suspend accounts"))


def test_the_check_version_covers_retrieval(monkeypatch):
    v1 = kc.check_version(config())
    monkeypatch.setitem(kc.RETRIEVAL, "top_per_search", 99)
    assert kc.check_version(config()) != v1


def test_no_key_roles_is_refused():
    c = config()
    c = c.model_copy(update={"roles": c.roles.model_copy(update={"key_judge": None})})
    with pytest.raises(kc.KeyCheckError):
        kc.check_version(c)


def test_the_acceptance_runner_builds_frozen_points_and_scores_clause_by_clause():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "key_check_acceptance", REPO_ROOT / "scripts/key_check_acceptance.py"
    )
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    case = {
        "id": "o01",
        "kind": "over_inference",
        "claim": "uptake",
        "expected": "not_supported",
        "input": {
            "fixture": "fx",
            "point": {"impact": "x"},
            "anchor": "a",
            "anchored_on": "ia",
            "sources": {},
        },
    }
    rows = run.score(
        [case], [{"claims": [{"verdict": "not_found", "reason": "r", "evidence": [], "shown": []}]}]
    )
    assert rows[0]["pass"] and rows[0]["got"] == "not_supported"
    rows = run.score(
        [case], [{"claims": [{"verdict": "cant_tell", "reason": "r", "evidence": [], "shown": []}]}]
    )
    assert not rows[0]["pass"]  # cant_tell is a miss
