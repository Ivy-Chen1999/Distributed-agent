"""Cross-family judge re-score of a gate comparison (plan 2026-10-15-001, U2): the stored runs of
the comparison are re-judged by another judge; aggregates only; report-only label."""

import json

import pytest

from womm.config import REPO_ROOT
from womm.data import fixtures as fixtures_module
from womm.eval import holdout
from womm.eval.evaluators import judge_version
from womm.evolve import rescore as rs
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend
from womm.models.system_version import build_system_version

from ..eval import holdout_factory as hf
from ..eval.test_holdout import _sealed
from ..eval.test_holdout_resume import ProgressStore, _backends, _run, _versions
from ..graph.conftest import fake_sv


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures_module, "FIXTURES_ROOT", hf.make_fixtures_root(tmp_path))
    (tmp_path / "golden").mkdir()
    return tmp_path


class RescoreStore(ProgressStore):
    """A ProgressStore that answers the rescore's reads and keeps its writes in memory."""

    def __init__(self, sealed, decision="promoted"):
        super().__init__(sealed)
        self.decision = {"decision": decision}
        self.rescore_progress = {}
        self.rescores = []  # dicts: gate_id, judge_version, result, superseded

    async def stored_runs(self, gate_id):
        audit = next((a for a in self.audits if a[2].get("gate_id") == gate_id), None)
        if audit is None:
            return None
        result, _sha, tags = audit
        jv, cvs = tags["judge_version"], tags["code_versions"]
        rows = []
        for v, k, r in sorted(self.compare_runs.get(gate_id, ())):
            if r > result.repetitions:
                continue
            key = (v, jv, cvs[v], k, r)
            score = self.progress.get(key)
            rows.append({"version_id": v, "case_key": k, "repetition": r,
                         "score": score.model_dump(mode="json") if score else None,
                         "run": self.runs.get(key)})  # fmt: skip
        return {"audit": {"candidate_version": result.candidate_version,
                          "baseline_version": result.baseline_version,
                          "repetitions": result.repetitions, "judge_version": jv,
                          "code_versions": cvs, "result": result.model_dump(mode="json"),
                          "decision": self.decision}, "rows": rows}  # fmt: skip

    async def load_rescore_progress(self, gate_id, rjv):
        return {(v, k, r): s for (g, j, v, k, r), s in self.rescore_progress.items()
                if g == gate_id and j == rjv}  # fmt: skip

    async def save_rescore_progress(self, gate_id, rjv, version_id, key, rep, score):
        self.rescore_progress[(gate_id, rjv, version_id, key, rep)] = score

    async def clear_rescore_progress(self, gate_id, rjv):
        for k in [k for k in self.rescore_progress if k[:2] == (gate_id, rjv)]:
            del self.rescore_progress[k]

    async def current_rescore(self, gate_id):
        return next((r for r in self.rescores if r["gate_id"] == gate_id
                     and not r["superseded"]), None)  # fmt: skip

    async def record_rescore(self, gate_id, rjv, result, *, replace=False):
        current = await self.current_rescore(gate_id)
        if current is not None:
            if not replace:
                raise holdout.HoldoutError("already re-scored")
            current["superseded"] = True
        self.rescores.append({"gate_id": gate_id, "judge_version": rjv, "result": result,
                              "superseded": False})  # fmt: skip


async def _gate(roots, reps=2, decision="promoted", proposals=hf.FIVE_PROPOSALS):
    store = RescoreStore(_sealed(roots, proposals), decision)
    cand, base = _versions()
    await _run(store, cand, base, *_backends(len(store.sealed) * reps), reps=reps, gate_id="g1")
    return store


def _flip_judge(_system, user):
    """Covers every expected impact of a BASELINE dossier, only the first of a CANDIDATE one."""
    swapped = user.replace("CANDIDATE", "__C__").replace("BASELINE", "CANDIDATE")
    return hf._judge(_system, swapped.replace("__C__", "BASELINE"))


def _all_judge(_system, user):
    return hf._judge(_system, user.replace("BASELINE", "CANDIDATE"))


def _judge_backend(fn, n=40):
    return {"fake": FakeBackend({"judge": [fn] * n})}


def _judge_sv():
    return build_system_version(fake_sv().spec.model_copy(update={"description": "other judge"}),
                                REPO_ROOT)  # fmt: skip


async def test_an_agreeing_rescore_is_recorded_with_aggregates_only(roots):
    store = await _gate(roots)
    result = await rs.rescore("g1", store=store, judge_sv=_judge_sv(),
                              backends=_judge_backend(hf._judge))  # fmt: skip
    assert result.label == "agrees" and result.sign_agrees is True
    assert result.delta.mean_delta > 0 and result.delta.n_cases == 5
    assert result.arms["candidate"].scored == 10 and result.arms["baseline"].scored == 10
    assert result.rescore_judge_version == judge_version(_judge_sv())
    assert len(store.rescores) == 1
    assert store.rescores[0]["result"] == result.model_dump(mode="json")
    dumped = json.dumps(store.rescores[0]["result"])
    for case, _ in store.sealed:
        assert case.case_id not in dumped and case.fixture not in dumped


async def test_opposite_signs_label_cross_judge_sensitive(roots):
    store = await _gate(roots)
    result = await rs.rescore("g1", store=store, judge_sv=_judge_sv(),
                              backends=_judge_backend(_flip_judge))  # fmt: skip
    assert result.delta.mean_delta < 0 and result.sign_agrees is False
    assert result.label == "cross-judge-sensitive"


async def test_a_promotion_with_a_rescored_delta_at_zero_is_sensitive(roots):
    store = await _gate(roots)
    result = await rs.rescore("g1", store=store, judge_sv=_judge_sv(),
                              backends=_judge_backend(_all_judge))  # fmt: skip
    assert result.delta.mean_delta == pytest.approx(0.0)
    assert result.label == "cross-judge-sensitive"


async def test_runs_stored_before_the_migration_are_refused(roots):
    store = await _gate(roots)
    store.runs = dict.fromkeys(store.runs)  # every run None, scores kept
    with pytest.raises(rs.RescoreRefused, match="before runs were stored"):
        await rs.rescore("g1", store=store, judge_sv=_judge_sv(),
                         backends=_judge_backend(hf._judge))  # fmt: skip


async def test_refusals_before_any_judge_call(roots):
    store = await _gate(roots)
    backend = _judge_backend(hf._judge)
    with pytest.raises(rs.RescoreRefused, match="no comparison"):
        await rs.rescore("g_missing", store=store, judge_sv=_judge_sv(), backends=backend)
    store.decision = None
    with pytest.raises(rs.RescoreRefused, match="no decision"):
        await rs.rescore("g1", store=store, judge_sv=_judge_sv(), backends=backend)
    store.decision = {"decision": "promoted"}
    store.sealed = store.sealed[1:]  # a case key no longer in the sealed store
    with pytest.raises(rs.RescoreRefused, match="no longer"):
        await rs.rescore("g1", store=store, judge_sv=_judge_sv(), backends=backend)
    assert backend["fake"].calls == []


async def test_an_aborted_comparison_is_refused(roots):
    store = await _gate(roots)
    result, sha, tags = store.audits[0]
    store.audits[0] = (result.model_copy(update={"aborted": True}), sha, tags)
    with pytest.raises(rs.RescoreRefused, match="aborted"):
        await rs.rescore("g1", store=store, judge_sv=_judge_sv(),
                         backends=_judge_backend(hf._judge))  # fmt: skip


def _failing_on_candidate(n_fail):
    calls = {"n": 0}

    def judge(_system, user):
        if "CANDIDATE" in user.split("Expected impacts:", 1)[0] and calls["n"] < n_fail:
            calls["n"] += 1
            raise LLMError("schema_invalid", "bad output", attempts=3)
        return hf._judge(_system, user)

    return judge


async def test_judge_errors_are_counted_and_the_failure_policy_applies(roots):
    store = await _gate(roots, reps=1)  # 5 cases, 1 repetition
    result = await rs.rescore("g1", store=store, judge_sv=_judge_sv(),
                              backends=_judge_backend(_failing_on_candidate(1)))  # fmt: skip
    assert result.arms["candidate"].errored == 1 and result.dropped_cases == 1
    assert result.label == "inconclusive" and result.delta.mean_delta is None  # abort policy

    store = await _gate(roots, reps=1)
    for i, (res, sha, tags) in enumerate(store.audits):
        store.audits[i] = (res.model_copy(update={"failure_policy": "score_zero"}), sha, tags)
    result = await rs.rescore("g1", store=store, judge_sv=_judge_sv(),
                              backends=_judge_backend(_failing_on_candidate(1)))  # fmt: skip
    assert result.dropped_cases == 1 and result.delta.n_cases == 5
    assert result.label in ("agrees", "cross-judge-sensitive")


async def test_a_rate_limit_stops_and_the_rescore_resumes(roots):
    store = await _gate(roots, reps=1)
    calls = {"n": 0}

    def limited(_system, user):
        calls["n"] += 1
        if calls["n"] == 4:
            raise LLMError("rate_limit", "usage limit reached")
        return hf._judge(_system, user)

    with pytest.raises(rs.RescoreStopped):
        await rs.rescore("g1", store=store, judge_sv=_judge_sv(), backends=_judge_backend(limited))
    assert len(store.rescore_progress) == 3 and store.rescores == []
    backend = _judge_backend(hf._judge)
    result = await rs.rescore("g1", store=store, judge_sv=_judge_sv(), backends=backend)
    assert len(backend["fake"].calls) == 10 - 3, "finished re-scores are not repeated"
    assert result.label == "agrees" and len(store.rescores) == 1


async def test_a_second_rescore_needs_replace_and_supersedes(roots):
    store = await _gate(roots, reps=1)
    await rs.rescore("g1", store=store, judge_sv=_judge_sv(), backends=_judge_backend(hf._judge))
    backend = _judge_backend(hf._judge)
    with pytest.raises(rs.RescoreRefused, match="--replace"):
        await rs.rescore("g1", store=store, judge_sv=_judge_sv(), backends=backend)
    assert backend["fake"].calls == []
    await rs.rescore("g1", store=store, judge_sv=_judge_sv(), backends=backend, replace=True)
    assert len(backend["fake"].calls) == 10, "replace clears the old progress"
    assert [r["superseded"] for r in store.rescores] == [True, False]
