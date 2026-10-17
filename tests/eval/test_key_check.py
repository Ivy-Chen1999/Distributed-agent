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
    judges = judge if isinstance(judge, list) else [judge] * kc.JUDGE_VOTES
    fake = FakeBackend({kc.SPLIT_ROLE: [split], kc.JUDGE_ROLE: judges})
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
                "checks": {
                    "group": "same",
                    "item": "same",
                    "kind": "same",
                    "degree": "n/a",
                    "modality": "same",
                },
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
                "checks": {
                    "group": "same",
                    "item": "same",
                    "kind": "same",
                    "degree": "n/a",
                    "modality": "same",
                },
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
                "checks": {
                    "group": "same",
                    "item": "same",
                    "kind": "same",
                    "degree": "n/a",
                    "modality": "same",
                },
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


# ------------------------------------------------------------ boundary cases (Codex 2026-10-17)


@pytest.mark.parametrize("quote", ["", "   ", "consumer trust"])
def test_an_empty_or_token_quote_never_supports(tmp_path, quote):
    judge = {
        "claims": [
            {
                "id": "c1",
                "checks": {
                    "group": "same",
                    "item": "same",
                    "kind": "same",
                    "degree": "n/a",
                    "modality": "same",
                },
                "verdict": "supported",
                "reason": "r",
                "evidence": [{"passage": "p1", "quote": quote, "type": "ia"}],
            },
            {"id": "c2", "verdict": "not_found", "reason": "r"},
        ]
    }
    res, _ = run(point(), sources(tmp_path), SPLIT, judge)
    assert res["claims"][0]["verdict"] == "cant_tell"


@pytest.mark.parametrize(
    "claims",
    [[], [{"id": "c1", "text": "a", "searches": []}, {"id": "c1", "text": "b", "searches": []}]],
)
def test_an_empty_or_duplicate_split_is_incomplete_and_judges_nothing(tmp_path, claims):
    fake = FakeBackend({kc.SPLIT_ROLE: [{"claims": claims}]})
    res = asyncio.run(kc.check_point(point(), sources(tmp_path), config(), {"fake": fake}))
    assert res["incomplete"] and res["claims"] == [] and len(fake.calls) == 1


def test_an_empty_case_set_never_passes_acceptance(tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "key_check_acceptance", REPO_ROOT / "scripts/key_check_acceptance.py"
    )
    acc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acc)
    empty = tmp_path / "cases.yaml"
    empty.write_text("cases: []\n")
    assert acc.main(["--out", str(tmp_path / "run"), "--cases", str(empty)]) == 2
    assert "empty case set" in capsys.readouterr().err
    rows = acc.score(
        [{"id": "x", "kind": "window", "claim": "c", "expected": "supported"}],
        [{"claims": [], "incomplete": "the split returned no claims"}],
    )
    assert rows[0]["got"] == "incomplete" and not rows[0]["pass"] and rows[0]["stage"] == "split"


def _acceptance():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "key_check_acceptance", REPO_ROOT / "scripts/key_check_acceptance.py"
    )
    acc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acc)
    return acc


def test_each_miss_names_its_stage():
    acc = _acceptance()
    case = {"expected": "supported", "source": "dsa/ia_full.txt:205"}
    assert acc.stage(case, "cant_tell", {"reason": "quote not found in its passage (x)"}) == "quote"
    assert (
        acc.stage(case, "not_supported", {"reason": "r", "shown": ["ia_full.txt:9"]}) == "retrieval"
    )
    shown = {"reason": "r", "shown": ["ia_full.txt:205"]}
    assert acc.stage(case, "not_supported", shown) == "judgement"


def test_split_elements_must_land_whole_in_one_claim():
    acc = _acceptance()
    case = {"id": "s1", "must_cover": [{"element": "uptake", "expected": "not_supported"},
                                       {"element": "confidence to claim compensation",
                                        "expected": "supported"}]}  # fmt: skip
    claims = [{"text": "Better consumer trust in, and uptake of, complex products",
               "verdict": "not_found", "reason": "r", "evidence": [], "shown": []},
              {"text": "more confidence", "verdict": "supported", "reason": "r",
               "evidence": [], "shown": []}]  # fmt: skip
    rows = acc.score_split([case], [{"claims": claims}])
    assert rows[0]["pass"] and rows[1]["got"] == "missing" and rows[1]["stage"] == "split"


def test_the_split_cases_quote_their_elements_from_the_frozen_point():
    import yaml

    cases = yaml.safe_load((REPO_ROOT / "evals/regression/key_split_2026-10-17.yaml").read_text())
    assert cases["cases"]
    for c in cases["cases"]:
        text = " ".join(c["input"]["point"].values()).lower()
        assert c["must_cover"] and c["input"]["sources"]
        for e in c["must_cover"]:
            assert e["expected"] in ("supported", "not_supported")
            assert all(w.lower() in text for w in e["element"].split()), (c["id"], e)


def test_judgements_must_agree_or_the_claim_goes_to_review(tmp_path):
    q = "improve consumer confidence and trust in more complex products"
    yes = {
        "claims": [
            {
                "id": "c1",
                "checks": {
                    "group": "same",
                    "item": "same",
                    "kind": "same",
                    "degree": "n/a",
                    "modality": "same",
                },
                "verdict": "supported",
                "reason": "r",
                "evidence": [{"passage": "p1", "quote": q, "type": "ia"}],
            },
            {"id": "c2", "verdict": "not_found", "reason": "r"},
        ]
    }
    no = {
        "claims": [
            {"id": "c1", "verdict": "not_found", "reason": "r"},
            {"id": "c2", "verdict": "not_found", "reason": "r"},
        ]
    }
    res, fake = run(point(), sources(tmp_path), SPLIT, [yes, no, yes])
    assert res["claims"][0]["verdict"] == "cant_tell"
    assert "differ" in res["claims"][0]["reason"]
    assert res["claims"][1]["verdict"] == "not_found"
    assert len(fake.calls) == 1 + kc.JUDGE_VOTES


def test_only_wrong_direction_and_unchecked_elements_are_dangerous():
    acc = _acceptance()
    row = lambda exp, got: {"pass": exp == got, "expected": exp, "got": got}  # noqa: E731
    assert acc.dangerous(row("not_supported", "supported"))
    assert acc.dangerous(row("supported", "not_supported"))
    assert acc.dangerous(row("supported", "missing"))
    assert not acc.dangerous(row("not_supported", "cant_tell"))
    assert not acc.dangerous(row("supported", "supported"))


def test_a_supported_verdict_with_a_different_part_goes_to_review(tmp_path):
    q = "improve consumer confidence and trust in more complex products"
    j = {
        "claims": [
            {
                "id": "c1",
                "verdict": "supported",
                "reason": "r",
                "checks": {
                    "group": "same",
                    "item": "different",
                    "kind": "same",
                    "degree": "n/a",
                    "modality": "same",
                },
                "evidence": [{"passage": "p1", "quote": q, "type": "ia"}],
            },
            {"id": "c2", "verdict": "not_found", "reason": "r"},
        ]
    }
    res, _ = run(point(), sources(tmp_path), SPLIT, j)
    assert res["claims"][0]["verdict"] == "cant_tell" and "item" in res["claims"][0]["reason"]
    j["claims"][0].pop("checks")
    res, _ = run(point(), sources(tmp_path / "again"), SPLIT, j)
    assert res["claims"][0]["verdict"] == "cant_tell" and "no check" in res["claims"][0]["reason"]
