"""U6 sealed holdout (no database needed): import validation, scenario injection, the compare
entry point (aggregates only, untraced, no files) and the train/val refusals (R23, AE3)."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langsmith import Client, tracing_context

from womm.config import REPO_ROOT, Settings
from womm.data import fixtures as fixtures_module
from womm.decisions.stub import StubDecisionService
from womm.eval import holdout
from womm.eval import run_eval as run_eval_module
from womm.eval.evaluators import CaseScore
from womm.eval.golden import GoldenCase, GoldenError, load_all_golden
from womm.eval.run_eval import EvalReport, evaluate_cases, persist_failures, sync_dataset
from womm.llm.fake import FakeBackend
from womm.models.regulation import Scenario
from womm.models.run import CodeIdentity

from ..graph.conftest import fake_sv
from . import holdout_factory as hf

CLEAN = CodeIdentity(git_sha="abc", dirty=False)
LANGSMITH_SENDS = ("create_run", "update_run", "batch_ingest_runs", "multipart_ingest",
                   "create_example", "create_dataset", "update_example")  # fmt: skip


@pytest.fixture
def roots(tmp_path, monkeypatch):
    root = hf.make_fixtures_root(tmp_path)
    monkeypatch.setattr(fixtures_module, "FIXTURES_ROOT", root)
    golden = tmp_path / "golden"
    golden.mkdir()
    return tmp_path


def _prepare(fixture="prop_alpha", **kw):
    return holdout.prepare_import(
        hf.handoff(fixture), hf.scenario_specs(), hf.ia_index(), golden_dir=kw.pop("golden")
    )


# ----------------------------------------------------------------------------- configuration


def test_holdout_url_is_required_and_never_the_api_database():
    with pytest.raises(holdout.HoldoutError, match="not set"):
        holdout.holdout_database_url({})
    same = "postgresql://u:p@h/womm"
    with pytest.raises(holdout.HoldoutError, match="never given"):
        holdout.holdout_database_url({"HOLDOUT_DATABASE_URL": same, "DATABASE_URL": same + "/"})
    url = "postgresql://u:p@h/womm_holdout"
    assert holdout.holdout_database_url({"HOLDOUT_DATABASE_URL": url, "DATABASE_URL": same}) == url


def test_the_api_is_never_given_the_holdout_url():
    """Settings (what the API reads) has no holdout field; the app never names the variable."""
    assert not [f for f in Settings.__dataclass_fields__ if "holdout" in f]
    for path in (REPO_ROOT / "src/womm/api").rglob("*.py"):
        assert "HOLDOUT" not in path.read_text(), path
    from womm.api.db import MIGRATIONS_DIR

    assert holdout.MIGRATIONS_DIR != MIGRATIONS_DIR
    assert not list(MIGRATIONS_DIR.glob("*holdout*"))


# ----------------------------------------------------------------------------- scenarios


def test_scenarios_are_read_only_from_the_private_directory(tmp_path):
    private = tmp_path / "private"
    path = hf.write_scenarios(private)
    specs = holdout.load_holdout_scenarios(path, private)
    assert specs["prop_alpha"][0].articles == hf.ARTICLES
    elsewhere = tmp_path / "public" / "holdout_scenarios.yaml"
    elsewhere.parent.mkdir()
    elsewhere.write_text(path.read_text())
    with pytest.raises(holdout.HoldoutError, match="must come from"):
        holdout.load_holdout_scenarios(elsewhere, private)


def test_build_scenario_refuses_a_public_scenario_and_unknown_articles(roots):
    fixture = fixtures_module.load_fixture(fixtures_module.fixture_dir("ai_act"))
    public = holdout.HoldoutScenarioSpec(
        scenario_id="eval_sme_impacts", description="d", articles=["55"],
        after_version="com2021_206",
    )  # fmt: skip
    with pytest.raises(holdout.HoldoutError, match="public fixture"):
        holdout.build_scenario(fixture, public)
    bad = public.model_copy(update={"scenario_id": "eval_new", "articles": ["55", "999"]})
    with pytest.raises(holdout.HoldoutError, match=r"\['999'\]"):
        holdout.build_scenario(fixture, bad)
    sc = holdout.build_scenario(fixture, bad.model_copy(update={"articles": ["55", "71"]}))
    assert sc.provision_keys == ["ai_act/innovation/sme_measures", "ai_act/penalties/penalties"]
    assert sc.kind == "evaluation" and sc.before_version is None


def test_injection_does_not_touch_the_public_fixture(roots):
    fixture = fixtures_module.load_fixture(fixtures_module.fixture_dir("prop_alpha"))
    sc = holdout.build_scenario(fixture, hf.scenario_specs()["prop_alpha"][0])
    injected = holdout.with_scenarios(fixture, [sc])
    assert hf.SCENARIO_ID in injected.scenarios and hf.SCENARIO_ID not in fixture.scenarios
    reloaded = fixtures_module.load_fixture(fixtures_module.fixture_dir("prop_alpha"))
    assert reloaded.scenarios == {}


# ----------------------------------------------------------------------------- import


def test_prepare_import_happy_path(roots):
    bundle = _prepare(golden=roots / "golden")
    assert bundle.case.split == "holdout" and bundle.scenario.scenario_id == hf.SCENARIO_ID
    assert bundle.ia.ia_celex and bundle.verified_by == "jdoe"
    assert bundle.body_sha256 == _prepare(golden=roots / "golden").body_sha256


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda h: h.update(format="other/1"), "not a holdout handoff"),
        (lambda h: h["case"].update(split="val"), "only holdout cases"),
        (lambda h: h.update(verified_by=""), "no reviewer"),
        (lambda h: h["case"].update(scenario_id="eval_unknown"), "no local holdout scenario"),
        (lambda h: h["case"]["expected_impacts"][0].update(provision_keys=["ai_act/x"]),
         "not in scenario"),
    ],
)  # fmt: skip
def test_prepare_import_refusals(roots, mutate, message):
    h = hf.handoff("prop_alpha")
    mutate(h)
    with pytest.raises(holdout.HoldoutError, match=message):
        holdout.prepare_import(h, hf.scenario_specs(), hf.ia_index(), golden_dir=roots / "golden")


def test_prepare_import_needs_an_ia_record(roots):
    with pytest.raises(holdout.HoldoutError, match="no IA record"):
        holdout.prepare_import(hf.handoff("prop_alpha"), hf.scenario_specs(), {},
                               golden_dir=roots / "golden")  # fmt: skip


def test_a_proposal_with_public_cases_cannot_be_holdout(roots):
    golden = roots / "golden"
    public = {**hf.case_dict("prop_alpha"), "case_id": "case_50_public", "split": "train"}
    (golden / "case_50_public.yaml").write_text(json.dumps(public))
    with pytest.raises(holdout.HoldoutError, match="share a split"):
        _prepare(golden=golden)


# ----------------------------------------------------------------------------- train/val refusals


def test_sync_dataset_refuses_holdout_before_any_langsmith_call():
    client = MagicMock()
    case = GoldenCase.model_validate(hf.case_dict("prop_alpha"))
    with pytest.raises(GoldenError, match="refusing to sync 1 holdout"):
        sync_dataset(client, [*load_all_golden(), case])
    assert client.mock_calls == []


async def test_persist_failures_refuses_a_holdout_run():
    report = EvalReport(
        system_version="sv_x", metadata={"repetitions": 1},
        scores=[CaseScore(case_id="c", scenario_id="s", outcome="scored", coverage=0.0)],
        case_splits={"c": "holdout"},
    )  # fmt: skip
    # The URL is never reached: the refusal comes before any connection.
    with pytest.raises(GoldenError, match="holdout run"):
        await persist_failures(report, "postgresql://nobody@127.0.0.1:1/none")


# ----------------------------------------------------------------------------- compare


class MemoryStore(holdout.HoldoutStore):
    """The store's two compare-facing methods, without Postgres."""

    def __init__(self, sealed):
        self.sealed = sealed
        self.audits = []

    async def _sealed(self):
        return self.sealed

    async def record_audit(self, result, git_sha):
        self.audits.append((result, git_sha))
        return len(self.audits)


def _sealed(roots, proposals=hf.PROPOSALS):
    out = []
    for name in proposals:
        b = holdout.prepare_import(hf.handoff(name), hf.scenario_specs(), hf.ia_index(),
                                   golden_dir=roots / "golden")  # fmt: skip
        out.append((b.case, Scenario.model_validate(b.scenario.model_dump())))
    return out


async def _compare(store, reps=2, backends=None, **kw):
    cand, base = fake_sv(), fake_sv()
    backends = backends or hf.compare_backends(runs_per_version=len(store.sealed) * reps)
    return await holdout.compare(
        cand, base, reps, store=store,
        backends=lambda sv: backends["candidate"] if sv is cand else backends["baseline"],
        decisions=StubDecisionService(), code=CLEAN, n_boot=200, **kw,
    )  # fmt: skip


async def test_compare_returns_aggregates_only(roots):
    store = MemoryStore(_sealed(roots, hf.FIVE_PROPOSALS))
    result = await _compare(store)
    n_expected = len(store.sealed[0][0].expected_impacts)
    cov = result.deltas["coverage"]
    assert cov.mean_delta == pytest.approx(1 - 1 / n_expected)
    assert cov.ci95_low == pytest.approx(cov.mean_delta) == pytest.approx(cov.ci95_high)
    assert cov.n_cases == 5 and result.n_cases == 5 and result.n_proposals == 5
    assert result.flags == [] and result.missing_cases == {"candidate": 0, "baseline": 0}
    assert result.deltas["grounding"].mean_delta == pytest.approx(0.0)
    assert result.noise["coverage"].candidate_sd == 0.0  # deterministic fake: no noise
    assert result.scored_runs == {"candidate": 10, "baseline": 10} and not result.aborted
    assert set(result.noise["coverage"].model_dump()) == {"candidate_sd", "baseline_sd"}

    dumped = result.model_dump_json()
    for case, scenario in store.sealed:
        assert case.case_id not in dumped and scenario.scenario_id not in dumped
        assert case.fixture not in dumped and case.ia_reference not in dumped
        for e in case.expected_impacts:
            assert e.expected_id not in dumped and e.impact not in dumped
    assert store.audits == [(result, "abc")], "results go only to the audit table"


async def test_compare_is_untraced_writes_no_files_and_logs_nothing_sealed(
    roots, tmp_path, monkeypatch, caplog, capsys
):
    """AE3 / R23: zero LangSmith sends (a mocked client is the active one, tracing forced on),
    no evaluate_cases wrapper, no runs/ files, no sealed text in logs or output."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "test-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://127.0.0.1:9")
    sent = []
    for name in LANGSMITH_SENDS:
        monkeypatch.setattr(Client, name, lambda *a, _n=name, **k: sent.append(_n))
    for name in ("evaluate_cases", "write_report", "record_langsmith_experiment", "_save_run",
                 "sync_dataset", "persist_failures"):  # fmt: skip
        monkeypatch.setattr(run_eval_module, name, _forbidden(name))
    monkeypatch.chdir(tmp_path)
    runs = REPO_ROOT / "runs"
    before = _tree(runs) | _tree(tmp_path)
    client = MagicMock(spec=Client)
    caplog.set_level(logging.DEBUG)

    store = MemoryStore(_sealed(roots))
    with tracing_context(enabled=True, client=client):
        result = await _compare(store)

    assert not result.aborted
    assert [c for c in client.mock_calls if c[0].split(".")[0] in LANGSMITH_SENDS] == []
    assert sent == []
    assert (_tree(runs) | _tree(tmp_path)) == before, "no files written"
    out = capsys.readouterr()
    text = caplog.text + out.out + out.err
    for case, _ in store.sealed:
        assert case.case_id not in text and hf.SECRET_IMPACT not in text


async def test_the_langsmith_check_detects_a_traced_run(fixture, monkeypatch):
    """Control for the test above: the same mocked client does see a train/val eval."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    client = MagicMock(spec=Client)
    case = next(c for c in load_all_golden() if c.scenario_id == "eval_sme_impacts")
    script = hf.pipeline_script("CANDIDATE", 1)
    script["judge"] = [hf._judge]
    with tracing_context(enabled=True, client=client):
        await evaluate_cases(
            [case], sv=fake_sv(), fixture=fixture, backends={"fake": FakeBackend(script)},
            decisions=StubDecisionService(), code=CLEAN, judge_prompt="p",
        )  # fmt: skip
    assert any(c[0] == "create_run" for c in client.mock_calls)


async def test_compare_stops_on_a_rate_limit_without_deltas(roots):
    from womm.llm.base import LLMError

    store = MemoryStore(_sealed(roots))
    cand, base = fake_sv(), fake_sv()
    backends = hf.compare_backends(runs_per_version=4)
    limited = hf.pipeline_script("CANDIDATE", 4)
    limited["synthesis"] = [LLMError("rate_limit", "slow down")] * 4
    backends["candidate"] = {"fake": FakeBackend(limited)}
    result = await holdout.compare(
        cand, base, 2, store=store,
        backends=lambda sv: backends["candidate"] if sv is cand else backends["baseline"],
        decisions=StubDecisionService(), code=CLEAN, n_boot=50,
    )  # fmt: skip
    assert result.aborted and result.deltas["coverage"].mean_delta is None
    assert "rate_limited" in result.flags
    assert len(store.audits) == 1


# ----------------------------------------------------------------------------- failing cases (P1-2)


def _backends_with_failed_candidate_case(n_cases, reps=1):
    """The candidate's first run times out (an infrastructure error); every other run is
    scored. The case is then errored on the candidate side for that repetition."""
    from womm.llm.base import LLMError

    backends = hf.compare_backends(runs_per_version=n_cases * reps)
    script = hf.pipeline_script("CANDIDATE", n_cases * reps)
    script["synthesis"] = [LLMError("timeout", "too slow")] + script["synthesis"][1:]
    backends["candidate"] = {"fake": FakeBackend(script)}
    return backends


async def test_a_case_missing_on_one_side_aborts_without_deltas(roots):
    store = MemoryStore(_sealed(roots, hf.FIVE_PROPOSALS))
    result = await _compare(store, reps=1, backends=_backends_with_failed_candidate_case(5))
    assert result.errored_runs["candidate"] == 1
    assert result.aborted and "missing_scores" in result.flags
    assert result.missing_cases == {"candidate": 1, "baseline": 0}
    assert all(d.mean_delta is None and d.ci95_low is None and d.n_cases == 0
               for d in result.deltas.values())  # fmt: skip
    assert store.audits == [(result, "abc")], "the abort is recorded, counts only"
    dumped = result.model_dump_json()
    assert not any(c.case_id in dumped for c, _ in store.sealed)


async def test_score_zero_policy_scores_the_missing_side_as_zero(roots):
    store = MemoryStore(_sealed(roots, hf.FIVE_PROPOSALS))
    result = await _compare(store, reps=1, backends=_backends_with_failed_candidate_case(5),
                            failure_policy="score_zero")  # fmt: skip
    assert not result.aborted and result.failure_policy == "score_zero"
    assert "missing_scores" in result.flags and result.missing_cases["candidate"] == 1
    cov = result.deltas["coverage"]
    n_expected = len(store.sealed[0][0].expected_impacts)
    # Four cases gain 1 - 1/n; the failed one scores 0 against the baseline's 1/n.
    assert cov.n_cases == 5
    assert cov.mean_delta == pytest.approx((4 * (1 - 1 / n_expected) - 1 / n_expected) / 5)
    assert cov.ci95_low is not None


async def test_compare_refuses_an_unknown_failure_policy(roots):
    with pytest.raises(holdout.HoldoutError, match="failure_policy"):
        await _compare(MemoryStore(_sealed(roots)), failure_policy="drop")


# ----------------------------------------------------------------------------- small n (P2-4)


async def test_fewer_than_five_proposals_give_null_cis(roots):
    store = MemoryStore(_sealed(roots))
    result = await _compare(store)
    assert result.n_proposals == 2 and "insufficient_proposals" in result.flags
    for delta in result.deltas.values():
        assert delta.ci95_low is None and delta.ci95_high is None
    assert result.deltas["coverage"].mean_delta is not None and not result.aborted


async def test_compare_refuses_an_empty_store_and_bad_repetitions():
    with pytest.raises(holdout.HoldoutError, match="no cases"):
        await holdout.compare(fake_sv(), fake_sv(), 1, store=MemoryStore([]), backends={},
                              decisions=StubDecisionService(), code=CLEAN)  # fmt: skip
    with pytest.raises(holdout.HoldoutError, match="at least 1"):
        await holdout.compare(fake_sv(), fake_sv(), 0, store=MemoryStore([]), backends={},
                              decisions=StubDecisionService(), code=CLEAN)  # fmt: skip


def test_cluster_bootstrap_resamples_whole_proposals():
    diffs = {"k0": 1.0, "k1": 1.0, "k2": 0.0}
    point, lo, hi = holdout.paired_cluster_bootstrap(
        diffs, {"k0": "a", "k1": "a", "k2": "b"}, n_boot=500, seed=1
    )
    assert point == pytest.approx(2 / 3)
    # Only three cluster resamples exist: {a,a}=1, {a,b}=2/3, {b,b}=0.
    assert lo == pytest.approx(0.0) and hi == pytest.approx(1.0)
    assert holdout.paired_cluster_bootstrap({}, {}, 10, 0) == (None, None, None)


def _forbidden(name):
    def fail(*_a, **_k):
        raise AssertionError(f"holdout.compare must never call run_eval.{name}")

    return fail


def _tree(root: Path) -> set[str]:
    return {str(p) for p in root.rglob("*")} if root.exists() else set()


# ----------------------------------------------------------------------------- plaintext cleanup


def _importer():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "import_holdout_case", REPO_ROOT / "scripts/import_holdout_case.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["import_holdout_case"] = module
    spec.loader.exec_module(module)
    return module


def _plaintext(tmp_path, cid="case_90_x"):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    draft = drafts / f"{cid}.yaml"
    draft.write_text("plaintext holdout draft\n")
    decisions = drafts / f"{cid}.decisions.yaml"
    decisions.write_text("x: {decision: verified}\n")
    handoff = tmp_path / "handoff.yaml"
    handoff.write_text("h\n")
    return drafts, draft, decisions, handoff


def test_matching_draft_and_its_decisions_are_deleted(tmp_path):
    import hashlib

    drafts, draft, decisions, handoff = _plaintext(tmp_path)
    sha = hashlib.sha256(draft.read_bytes()).hexdigest()
    _importer().delete_plaintext(handoff, "case_90_x", sha, drafts)
    assert not handoff.exists() and not draft.exists() and not decisions.exists()


@pytest.mark.parametrize("sha", ["0" * 64, None])
def test_a_changed_draft_keeps_its_decisions(tmp_path, capsys, sha):
    """P3-2: the decisions belong to the draft on disk; deleting them would lose that review."""
    drafts, draft, decisions, handoff = _plaintext(tmp_path)
    _importer().delete_plaintext(handoff, "case_90_x", sha, drafts)
    assert not handoff.exists()
    assert draft.exists() and decisions.exists()
    assert "it and its decisions stay" in capsys.readouterr().err


def test_same_import_compares_the_ia_record_too(roots):
    bundle = holdout.prepare_import(hf.handoff("prop_alpha"), hf.scenario_specs(), hf.ia_index(),
                                    golden_dir=roots / "golden")  # fmt: skip
    ia = bundle.ia
    row = {"body_sha256": bundle.body_sha256, "scenario": bundle.scenario.model_dump(mode="json"),
           "celex": ia.celex, "ia_reference": bundle.case.ia_reference, "ia_celex": ia.ia_celex,
           "ia_date": ia.ia_date, "rsb_ref": ia.rsb_ref}  # fmt: skip
    assert holdout._same_import(row, bundle)
    assert not holdout._same_import(row | {"rsb_ref": "SEC(2099) 99"}, bundle)
    assert not holdout._same_import(row | {"ia_celex": None}, bundle)
    assert not holdout._same_import(row | {"body_sha256": "x"}, bundle)
