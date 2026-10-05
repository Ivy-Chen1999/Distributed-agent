"""Golden-case drafting tool (U4) on the fake backend. The IA text here is synthetic."""

import datetime as dt
import importlib.util
import sys
from unittest.mock import MagicMock

import httpx
import pytest
import yaml
from langsmith import traceable, tracing_context

from womm.config import REPO_ROOT
from womm.data.fixtures import fixture_dir, load_fixture
from womm.data.ia_index import IaRecord, update_ia_index
from womm.eval import drafting as drafting_module
from womm.eval.drafting import (
    DraftingConfig,
    DraftingError,
    DraftInputs,
    audit_seed_for,
    draft_case,
    draft_path,
    draw_audit,
    item_digest,
    load_draft,
    load_drafting_config,
    write_draft,
)
from womm.eval.ia_sources import cache_ia
from womm.llm.fake import FakeBackend
from womm.models.system_version import RoleConfig

from .test_ia_sources import BASE, doc, h, p

spec = importlib.util.spec_from_file_location(
    "draft_golden_case", REPO_ROOT / "scripts/draft_golden_case.py"
)
script = importlib.util.module_from_spec(spec)
sys.modules["draft_golden_case"] = script
spec.loader.exec_module(script)

CASE = "case_90_widget_switching"
SCENARIO = "eval_cloud_switching"
KEYS = [f"data_act/proposal/art/{n}" for n in (23, 24, 25, 26, 27, 29)]
IA = (
    "6.2.3. Intervention on widget services\n\n"
    "Removing switching charges would let widget customers move between providers at lower "
    "cost.\n\n"
    "Widget providers with proprietary interfaces would face redesign costs to meet the switching "
    "timeframes.\n\n"
    "Smaller widget providers would win new customers who are currently locked in by large "
    "providers.\n\n"
    "Third-country access safeguards would raise trust among business users of widget services."
)
RSB = "The Board asked the report to explain how the switching costs fall on smaller providers."
A1 = "Removing switching charges would let widget customers move between providers at lower cost"
A2 = "Widget providers with proprietary interfaces would face redesign costs"
A3 = "Smaller widget providers would win new customers who are currently locked in"
A4 = "Third-country access safeguards would raise trust among business users"
A_RSB = "explain how the switching costs fall on smaller providers"

ROLE_NAMES = ("drafter", "recall", "judge_anchor", "judge_derivability", "judge_category")


def config() -> DraftingConfig:
    roles = {
        n: RoleConfig(backend="fake", model=f"model-{n}", prompt=f"p/{n}.md") for n in ROLE_NAMES
    }
    return DraftingConfig(
        roles=roles,
        prompts={f"p/{n}.md": f"SYSTEM PROMPT {n}" for n in ROLE_NAMES},
        prompt_hashes={f"p/{n}.md": "h" for n in ROLE_NAMES},
    )


def impact(anchor, keys=None, actor="Widget customers", category="market_competition", der="yes"):
    return {
        "affected_actor": actor,
        "mechanism": f"mechanism for {actor}",
        "impact": f"effect on {actor}",
        "provision_keys": keys or KEYS[:2],
        "ia_section": "6.2.3. Intervention on widget services",
        "ia_anchor": anchor,
        "category": category,
        "derivability": {"verdict": der, "reason": "the articles say so"},
    }


def omission(anchor, source="ia"):
    return {
        "description": f"omission anchored in {source}",
        "provision_keys": KEYS[2:3],
        "source": source,
        "ia_section": "Annex 1" if source == "rsb" else "6.2.3.",
        "ia_anchor": anchor,
        "category": "sme_specific",
        "derivability": {"verdict": "partly", "reason": "r"},
    }


def draft_out(impacts=None, omissions=None):
    return {
        "expected_impacts": impacts
        if impacts is not None
        else [
            impact(A1),
            impact(A2, actor="Widget providers", category="compliance_cost"),
            impact(A3, actor="Smaller widget providers"),
            impact(A4, keys=KEYS[4:5], actor="Business users", category="international"),
        ],
        "important_omissions": omissions
        if omissions is not None
        else [omission(A_RSB, "rsb"), omission(A2, "ia")],
    }


def recall_out(cands=None):
    if cands is None:
        cands = [impact(A3, actor="Widget start-ups") | {"why_missing": "different actor"}]
    return {"possibly_missing": cands}


def all_agree(_system, user):
    ids = sorted(set(_ids(user)))
    return {"verdicts": [{"item_id": i, "verdict": "agree", "reason": "ok"} for i in ids]}


def _ids(user: str) -> list[str]:
    import re

    return re.findall(r'"item_id": "([^"]+)"', user)


def script_for(draft=None, recall=None, judges=None):
    judges = judges or {}
    return {
        "drafter": [draft or draft_out()],
        "recall": [recall or recall_out()],
        "judge_anchor": [judges.get("anchor", all_agree)],
        "judge_derivability": [judges.get("derivability", all_agree)],
        "judge_category": [judges.get("category", all_agree)],
    }


@pytest.fixture(scope="module")
def data_act():
    return load_fixture(fixture_dir("data_act"))


def inputs(fixture, split="train", record=None, seed=None, ia=IA) -> DraftInputs:
    return DraftInputs(
        case_id=CASE,
        split=split,
        fixture=fixture,
        scenario=fixture.scenario(SCENARIO),
        ia_full_text=ia,
        ia_sections_text=ia,
        ia_section_titles=["6.2.3. Intervention on widget services"],
        rsb_text=RSB,
        rsb_status="opinion none found in Cellar; using the IA's procedural-information annex",
        ia_reference="Synthetic IA, section 6.2.3",
        ia_record=record,
        test_audit_seed=seed,
        today=dt.date(2026, 10, 4),
    )


async def run_draft(fixture, script, **kw):
    fake = FakeBackend(script)
    draft = await draft_case(inputs(fixture, **kw), config(), {"fake": fake})
    return draft, fake


# ----------------------------------------------------------------------------- happy path


async def test_scripted_draft_writes_a_file_whose_anchors_all_verify(data_act, tmp_path):
    draft, _ = await run_draft(data_act, script_for())
    path = write_draft(draft, tmp_path)
    assert path == tmp_path / f"{CASE}.yaml"
    loaded = load_draft(path)
    assert loaded == draft
    assert path.read_text().startswith("# DRAFT golden case")
    s = loaded.stats
    assert (s.expected_impacts, s.important_omissions, s.possibly_missing) == (4, 2, 1)
    assert s.items == 7 and s.anchors_verified == 7
    assert all(i.anchor.status == "verified" and not i.flags for i in loaded.items())
    assert [i.expected_id for i in loaded.expected_impacts] == [f"c90_e0{n}" for n in range(1, 5)]
    assert loaded.possibly_missing[0].candidate_id == "c90_m01"
    assert loaded.provenance.drafting_model == "model-drafter"
    assert loaded.possibly_missing[0].provenance.origin == "llm_recall"
    assert loaded.possibly_missing[0].provenance.drafting_model == "model-recall"
    assert loaded.provenance.roles["judge_category"]["model"] == "model-judge_category"
    assert s.judge_agree == 7 and s.auto_accepted + s.audited == 7


async def test_omission_sources_rsb_and_ia_verify_against_their_own_text(data_act):
    draft, _ = await run_draft(data_act, script_for())
    rsb, ia = draft.important_omissions
    assert (rsb.source, rsb.anchor.against, rsb.anchor.status) == ("rsb", "rsb", "verified")
    assert (ia.source, ia.anchor.against, ia.anchor.status) == ("ia", "ia", "verified")
    # An IA quote cited as RSB is not in the RSB text.
    wrong, _ = await run_draft(
        data_act, script_for(draft=draft_out(omissions=[omission(A1, "rsb")]))
    )
    assert wrong.important_omissions[0].anchor.status == "anchor_not_found"


# ----------------------------------------------------------------------------- flags


async def test_anchor_not_in_ia_is_flagged_and_left_for_a_human(data_act):
    bad = impact("Widget providers would close all their offices in the coming year")
    draft, _ = await run_draft(data_act, script_for(draft=draft_out(impacts=[bad, impact(A1)])))
    item = draft.expected_impacts[0]
    assert item.anchor.status == "anchor_not_found"
    assert "anchor_not_found" in item.flags
    assert item.judge.overall == "agree"  # the judges agreed, the deterministic check did not
    assert item.review.decision == "pending" and item.provenance.status == "needs_human"
    assert draft.stats.anchors_verified == draft.stats.items - 1


async def test_unknown_provision_key_is_flagged_and_draft_still_writes(data_act, tmp_path):
    bad = impact(A1, keys=["data_act/proposal/art/4", KEYS[0]])
    draft, _ = await run_draft(data_act, script_for(draft=draft_out(impacts=[bad])))
    item = draft.expected_impacts[0]
    assert item.flags == ["unknown_provision_keys: ['data_act/proposal/art/4']"]
    assert item.review.decision == "pending"
    assert write_draft(draft, tmp_path).exists()


async def test_not_derivable_is_flagged(data_act):
    draft, _ = await run_draft(
        data_act, script_for(draft=draft_out(impacts=[impact(A1, der="no")]))
    )
    assert "not_derivable_from_provisions" in draft.expected_impacts[0].flags


# ----------------------------------------------------------------------------- judges


async def test_three_judge_calls_are_separate_and_isolated(data_act):
    _, fake = await run_draft(data_act, script_for())
    judge_calls = [c for c in fake.calls if c.role_name.startswith("judge_")]
    assert sorted(c.role_name for c in judge_calls) == [
        "judge_anchor", "judge_category", "judge_derivability",
    ]  # fmt: skip
    assert len({c.system_prompt for c in judge_calls}) == 3
    by_role = {c.role_name: c.user_content for c in judge_calls}
    for user in by_role.values():  # every judge sees every item, impacts to candidates
        assert {"c90_e01", "c90_o01", "c90_m01"} <= set(_ids(user))
    anchor, der, cat = (
        by_role["judge_anchor"],
        by_role["judge_derivability"],
        by_role["judge_category"],
    )
    # Anchor judge: anchor in IA context, nothing about category, derivability or provisions.
    assert "anchor_in_context" in anchor and "lower cost" in anchor
    assert "proposed_category" not in anchor and "drafter_derivability" not in anchor
    assert "Scenario provisions" not in anchor
    # Derivability judge: the provisions and the drafter's verdict, no IA anchor or category.
    assert "Scenario provisions" in der and "drafter_derivability" in der
    assert "anchor_quote" not in der and "proposed_category" not in der
    # Category judge: the claim and the taxonomy only.
    assert "proposed_category" in cat and "market_competition" in cat
    assert "anchor_quote" not in cat and "Scenario provisions" not in cat
    assert "drafter_derivability" not in cat


async def test_unknown_and_missing_verdicts_make_an_item_uncertain(data_act):
    def category(_s, user):
        ids = sorted(set(_ids(user)))
        verdicts = [{"item_id": ids[0], "verdict": "unknown", "reason": "two categories fit"}]
        verdicts += [{"item_id": i, "verdict": "agree", "reason": "ok"} for i in ids[2:]]
        return {"verdicts": verdicts}  # ids[1] has no verdict at all

    draft, _ = await run_draft(data_act, script_for(judges={"category": category}))
    by_id = {i.item_id: i for i in draft.items()}
    ids = sorted(by_id)
    first, second = by_id[ids[0]], by_id[ids[1]]
    assert first.judge.category.verdict == "unknown" and first.judge.overall == "uncertain"
    assert second.judge.category.verdict == "unknown"
    assert second.judge.category.reason == "the judge returned no verdict"
    assert first.review.decision == second.review.decision == "pending"
    assert draft.stats.judge_uncertain == 2


async def test_any_disagree_makes_an_item_disagree(data_act):
    def anchor(_s, user):
        out = all_agree(_s, user)
        out["verdicts"][0] = out["verdicts"][0] | {"verdict": "disagree", "reason": "wrong actor"}
        return out

    draft, _ = await run_draft(data_act, script_for(judges={"anchor": anchor}))
    disagreed = [i for i in draft.items() if i.judge.overall == "disagree"]
    assert len(disagreed) == 1 and disagreed[0].review.decision == "pending"
    assert disagreed[0].judge.anchor_faithfulness.reason == "wrong actor"


# ----------------------------------------------------------------------------- audit


def test_audit_sample_is_deterministic():
    ids = [f"c01_e{n:02d}" for n in range(1, 11)]
    first = draw_audit(ids, 0.2, 42)
    assert first == draw_audit(list(reversed(ids)), 0.2, 42)  # order of input is irrelevant
    assert len(first) == 2 and set(first) <= set(ids)
    assert len(draw_audit(ids[:3], 0.2, 1)) == 1  # ceil: at least one when any are eligible
    assert draw_audit([], 0.2, 1) == [] and draw_audit(ids, 0.0, 1) == []
    assert audit_seed_for("case_03_x") == audit_seed_for("case_03_x")


async def test_audit_sample_recorded_in_draft_and_reproducible(data_act):
    a, _ = await run_draft(data_act, script_for(), seed=7)
    b, _ = await run_draft(data_act, script_for(), seed=7)
    assert a.provenance.audit == b.provenance.audit
    audit = a.provenance.audit
    # Six auto-accepted impacts and omissions are eligible; the candidate never is.
    assert audit.seed == 7 and audit.rate == 0.2 and audit.eligible == 6
    assert audit.eligible_ids == sorted(i.item_id for i in [*a.expected_impacts,
                                                            *a.important_omissions])  # fmt: skip
    assert len(audit.sampled) == 2  # ceil(0.2 x 6)
    assert not any(i.startswith("c90_m") for i in audit.sampled)
    sampled = [i for i in a.items() if i.review.audit]
    assert sorted(i.item_id for i in sampled) == audit.sampled
    assert all(i.review.decision == "pending" for i in sampled)
    assert a.stats.audited == 2 and a.stats.auto_accepted == 5
    assert a.possibly_missing[0].review.decision == "auto_accepted"  # a human decides it anyway
    assert audit.non_publishable_test_seed, "a test seed marks the draft non-publishable"
    default, _ = await run_draft(data_act, script_for())
    assert default.provenance.audit.seed == audit_seed_for(CASE)
    assert not default.provenance.audit.non_publishable_test_seed


async def test_draft_records_a_digest_per_item_and_the_drafter(data_act):
    draft, _ = await run_draft(data_act, script_for())
    digests = draft.provenance.item_digests
    assert set(digests) == {i.item_id for i in draft.items()}
    assert all(digests[i.item_id] == item_digest(i) for i in draft.items())
    # The digest covers tool-written fields, not the review block a human fills in.
    item = draft.expected_impacts[0]
    reviewed = item.model_copy(update={"review": item.review.model_copy(update={
        "decision": "verified", "reviewer": "octo-cat"})})  # fmt: skip
    assert item_digest(reviewed) == digests[item.item_id]
    changed = item.model_copy(update={"impact": "something else"})
    assert item_digest(changed) != digests[item.item_id]
    named = await draft_case(
        inputs(data_act).model_copy(update={"drafted_by": "case-owner"}),
        config(),
        {"fake": FakeBackend(script_for())},
    )
    assert named.provenance.drafted_by == "case-owner"


# ----------------------------------------------------------------------------- identifiers


async def test_case_ia_identifiers_are_scrubbed(data_act):
    record = IaRecord(celex="52022PC0068", ia_celex="52099SC0034", rsb_ref="SEC(2099) 81")
    leaky = impact(A1) | {"ia_section": "SWD(2099) 34 final, section 6.2.3"}
    draft, _ = await run_draft(
        data_act, script_for(draft=draft_out(impacts=[leaky])), record=record
    )
    text = yaml.safe_dump(draft.model_dump(mode="json"))
    assert "SWD(2099) 34" not in text and "52099SC0034" not in text
    assert draft.expected_impacts[0].ia_section.startswith("[identifier withheld]")


# ----------------------------------------------------------------------------- holdout


def test_holdout_never_written_under_evals():
    with pytest.raises(DraftingError, match="holdout"):
        draft_path(CASE, "holdout", REPO_ROOT / "evals" / "golden" / "drafts")
    with pytest.raises(DraftingError, match="holdout"):
        draft_path(CASE, "holdout", REPO_ROOT / "evals")
    assert draft_path(CASE, "holdout") == (REPO_ROOT / ".cache/drafts" / f"{CASE}.yaml").resolve()
    assert (
        draft_path(CASE, "train") == (REPO_ROOT / "evals/golden/drafts" / f"{CASE}.yaml").resolve()
    )


@pytest.mark.parametrize("where", ["docs", "data/fixtures", "src", ""])
def test_holdout_drafts_only_under_the_cache(where):
    with pytest.raises(DraftingError, match=r"only under \.cache/"):
        draft_path(CASE, "holdout", REPO_ROOT / where)
    elsewhere = draft_path(CASE, "holdout", REPO_ROOT / ".cache" / "elsewhere")
    assert elsewhere.parent.name == "elsewhere"


async def test_write_draft_refuses_holdout_under_evals(data_act, tmp_path, monkeypatch):
    draft, _ = await run_draft(data_act, script_for(), split="holdout")
    with pytest.raises(DraftingError):
        write_draft(draft, REPO_ROOT / "evals" / "golden" / "drafts")
    assert not (REPO_ROOT / "evals/golden/drafts" / f"{CASE}.yaml").exists()
    with pytest.raises(DraftingError, match=r"only under \.cache/"):
        write_draft(draft, tmp_path)
    monkeypatch.setattr(drafting_module, "CACHE_DIR", tmp_path)
    assert write_draft(draft, tmp_path).parent == tmp_path


# ----------------------------------------------------------------------------- script


class TracedFake(FakeBackend):
    """A fake backend whose calls are LangSmith-traced like the real backends."""

    async def _invoke(self, role_name, *args):
        traced = traceable(run_type="llm", name=f"fake:{role_name}")(super()._invoke)
        return await traced(role_name, *args)


SYNTH_IA = doc(
    h(1, "6. What are the impacts of the policy options?")
    + h(2, "6.2. Impact on businesses")
    + p(IA.replace("\n\n", " "))
    + h(1, "Annex 1: Procedural information")
    + p(RSB)
)


@pytest.fixture
def local_ia(tmp_path):
    """A cached synthetic IA and a local IA index for fixture data_act, all under tmp_path."""
    ia_root, index = tmp_path / "ia", tmp_path / "ia_index.yaml"
    routes = {f"{BASE}/celex/52099SC0002": httpx.Response(200, content=SYNTH_IA)}
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: routes.get(str(r.url), httpx.Response(404)))
    )
    cache_ia("data_act", "52099SC0002", None, root=ia_root, client=client)
    update_ia_index("data_act", IaRecord(celex="52022PC0068", ia_celex="52099SC0002"), index)
    return ia_root, index


def argv(local_ia, split, out_dir, *extra):
    ia_root, index = local_ia
    registry = index.parent / "private" / "holdout_scenarios.yaml"  # absent unless a test adds it
    return [
        "--fixture", "data_act", "--case", CASE, "--split", split, "--ia-index", str(index),
        "--ia-root", str(ia_root), "--out-dir", str(out_dir), "--holdout-registry", str(registry),
        "--drafted-by", "case-owner", *extra,
    ]  # fmt: skip


@pytest.mark.parametrize("split", ["train", "val"])
def test_script_refuses_train_val_drafting_of_a_holdout_proposal(
    local_ia, tmp_path, monkeypatch, capsys, split
):
    monkeypatch.setattr(script, "load_drafting_config", lambda _p: config())
    registry = tmp_path / "private" / "holdout_scenarios.yaml"
    registry.parent.mkdir()
    registry.write_text(
        "data_act:\n  - {scenario_id: eval_hidden_one, description: d, articles: ['1']}\n"
        "space_act:\n  - {scenario_id: eval_hidden_two, description: d, articles: ['2']}\n"
    )
    fake = FakeBackend({})
    out = tmp_path / "drafts"
    rc = script.main(argv(local_ia, split, out, "--scenario", SCENARIO), {"fake": fake})
    assert rc == 2 and fake.calls == [] and not out.exists()
    err = capsys.readouterr().err
    assert "'data_act' is registered as a holdout proposal" in err
    for secret in ("eval_hidden_one", "eval_hidden_two", "space_act"):
        assert secret not in err


def test_script_has_no_audit_seed_option(local_ia, tmp_path):
    with pytest.raises(SystemExit):
        script.parse_args(argv(local_ia, "train", tmp_path, "--scenario", SCENARIO,
                               "--audit-seed", "7"))  # fmt: skip


def test_script_writes_train_draft(local_ia, tmp_path, monkeypatch):
    monkeypatch.setattr(script, "load_drafting_config", lambda _p: config())
    out = tmp_path / "drafts"
    rc = script.main(
        argv(local_ia, "train", out, "--scenario", SCENARIO), {"fake": FakeBackend(script_for())}
    )
    assert rc == 0
    draft = load_draft(out / f"{CASE}.yaml")
    assert draft.split == "train" and draft.stats.anchors_verified == draft.stats.items
    assert draft.provenance.ia_sections == [
        "6. What are the impacts of the policy options?",
    ]  # the procedural annex is RSB material, not a drafting section
    assert "52099SC0002" not in (out / f"{CASE}.yaml").read_text()


def test_script_refuses_holdout_under_evals_before_any_call(local_ia, monkeypatch):
    monkeypatch.setattr(script, "load_drafting_config", lambda _p: config())
    fake = FakeBackend({})
    out = REPO_ROOT / "evals" / "golden" / "drafts"
    rc = script.main(argv(local_ia, "holdout", out, "--articles", "23", "24"), {"fake": fake})
    assert rc == 2 and fake.calls == []
    assert not (out / f"{CASE}.yaml").exists()


def test_holdout_drafting_posts_zero_langsmith_runs(local_ia, tmp_path, monkeypatch):
    monkeypatch.setattr(script, "load_drafting_config", lambda _p: config())
    monkeypatch.setattr(drafting_module, "CACHE_DIR", tmp_path)
    # Positive control: the same traced backend on a train split does post runs.
    train_client = MagicMock()
    with tracing_context(enabled=True, client=train_client, project_name="t"):
        rc = script.main(
            argv(local_ia, "train", tmp_path / "t", "--scenario", SCENARIO),
            {"fake": TracedFake(script_for())},
        )
    assert rc == 0 and train_client.create_run.call_count >= 5

    client = MagicMock()
    out = tmp_path / "holdout"
    with tracing_context(enabled=True, client=client, project_name="t"):
        rc = script.main(
            argv(local_ia, "holdout", out, "--articles", "23", "24", "25", "26", "27", "29"),
            {"fake": TracedFake(script_for())},
        )
    assert rc == 0
    assert client.method_calls == []  # no create_run / update_run / batch ingest at all
    draft = load_draft(out / f"{CASE}.yaml")
    assert draft.split == "holdout" and draft.scenario_id == "eval_widget_switching"
    assert not (REPO_ROOT / "evals/golden/drafts" / f"{CASE}.yaml").exists()


# ----------------------------------------------------------------------------- config


def test_repo_drafting_config_loads_with_isolated_claude_code_roles():
    cfg = load_drafting_config()
    roles = cfg.roles.as_dict()
    assert set(roles) == set(ROLE_NAMES)
    assert all(r.backend == "claude_code" for r in roles.values())
    judge_prompts = {roles[n].prompt for n in ROLE_NAMES if n.startswith("judge_")}
    assert len(judge_prompts) == 3  # one prompt per dimension
    assert roles["drafter"].model != roles["judge_anchor"].model  # judges do not grade own draft
    assert set(cfg.prompt_hashes) == {r.prompt for r in roles.values()}
    assert not hasattr(cfg, "audit_rate") and drafting_module.AUDIT_RATE == 0.2
