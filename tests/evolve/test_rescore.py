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

    def _current(self, gate_id):
        return next((r for r in self.rescores if r["gate_id"] == gate_id
                     and not r["superseded"]), None)  # fmt: skip

    async def current_rescore(self, gate_id):
        current = self._current(gate_id)
        return current["result"] if current else None

    async def record_rescore(self, gate_id, rjv, result, *, replace=False):
        current = self._current(gate_id)
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


async def test_a_gate_without_recorded_runs_is_refused(roots):
    """A gate decided before migration 004 (or not checkpointed) has no compare_runs rows: it
    is refused, never recorded as an empty inconclusive re-score."""
    store = await _gate(roots)
    backend = _judge_backend(hf._judge)
    real = store.stored_runs

    async def no_rows(gate_id):
        out = await real(gate_id)
        return {**out, "rows": []}

    store.stored_runs = no_rows
    with pytest.raises(rs.RescoreRefused, match="no recorded runs"):
        await rs.rescore("g1", store=store, judge_sv=_judge_sv(), backends=backend)
    assert backend["fake"].calls == [] and await store.current_rescore("g1") is None


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


# ----------------------------------------------------------------------------- CLI (U3)


def _cli_store(monkeypatch, store):
    from womm import cli
    from womm.evolve import promotion as pm

    from .test_promotion import policy

    result = store.audits[0][0]
    decision = pm.decide(
        result,
        policy(),
        pm.ModeChoice(mode="dev", deployable=False),
        gate_id="g1",
        cycle_id="c1",
        policy_sha256="sha",
        git_sha="abc",
    )
    store.decision = decision.model_dump(mode="json")

    async def migrate():
        return []

    async def decisions_for(version_id):
        return [store.decision | {"recorded_at": "2026-10-15T10:00:00"}]

    store.migrate, store.decisions_for = migrate, decisions_for
    monkeypatch.setattr(cli, "_holdout_store", lambda: store)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    async def fake_prepare(version, *, skip_self_check=False, settings=None):
        return _judge_backend(hf._judge), None, None

    monkeypatch.setattr(cli, "prepare_backends", fake_prepare)
    return cli


def _judge_file(tmp_path):
    import yaml

    path = tmp_path / "judge_sv.yaml"
    path.write_text(yaml.safe_dump(_judge_sv().spec.model_dump(mode="json")))
    return path


async def _seeded(roots):
    return await _gate(roots, reps=1)


def test_evolve_rescore_and_show(roots, tmp_path, monkeypatch, capsys):
    import asyncio

    store = asyncio.run(_seeded(roots))
    cli = _cli_store(monkeypatch, store)
    cand = store.audits[0][0].candidate_version
    assert cli.main(["evolve", "show", cand]) == cli.EXIT_OK
    assert "cross-judge check: not run" in capsys.readouterr().out

    args = ["evolve", "rescore", "g1", "--judge-sv", str(_judge_file(tmp_path))]
    assert cli.main([*args, "--json"]) == cli.EXIT_OK
    out = json.loads(capsys.readouterr().out)
    assert out["label"] in ("agrees", "cross-judge-sensitive") and out["gate_id"] == "g1"
    raw = json.dumps(out)
    stored = asyncio.run(store.stored_runs("g1"))["rows"]
    assert stored and not any(r["case_key"] in raw for r in stored)  # aggregates only
    assert cli.main(args) == cli.EXIT_USAGE and "--replace" in capsys.readouterr().err
    assert cli.main([*args, "--replace"]) == cli.EXIT_OK
    capsys.readouterr()
    assert cli.main(["evolve", "show", cand]) == cli.EXIT_OK
    shown = capsys.readouterr().out
    assert "cross-judge check (fake:" in shown and "not run" not in shown


def test_the_page_four_summary_is_aggregates_only(roots):
    import asyncio

    store = asyncio.run(_gate(roots, reps=1))
    result = asyncio.run(rs.rescore("g1", store=store, judge_sv=_judge_sv(),
                                    backends=_judge_backend(hf._judge)))  # fmt: skip
    summary = rs.cross_judge_summary(result, "2026-10-15")
    assert summary == {"label": result.label, "mean_delta": result.delta.mean_delta,
                       "ci95_low": result.delta.ci95_low, "ci95_high": result.delta.ci95_high,
                       "judge": result.judge, "gate_delta": result.gate_delta,
                       "rescored_on": "2026-10-15"}  # fmt: skip


async def _published(database_url, decision):
    from womm.api.db import Database
    from womm.evolve import promotion as pm

    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        if decision is not None:
            await pm.record_summary(db, pm.GateDecision.model_validate(decision))
    finally:
        await db.close()


def _cross_judge(database_url):
    import psycopg

    with psycopg.connect(database_url) as conn:
        return conn.execute("SELECT gate_id, cross_judge FROM promotion_decisions").fetchall()


@pytest.mark.parametrize("published", [True, False])
def test_rescore_copies_its_label_to_the_published_summary(roots, tmp_path, monkeypatch, capsys,
                                                           database_url, published):  # fmt: skip
    """With DATABASE_URL set, the cross-judge label goes onto the gate's page-4 row; a gate
    whose summary was never published (publish_summary off) gets nothing, and the CLI says so."""
    import asyncio

    store = asyncio.run(_seeded(roots))
    cli = _cli_store(monkeypatch, store)
    asyncio.run(_published(database_url, store.decision if published else None))
    monkeypatch.setenv("DATABASE_URL", database_url)
    args = ["evolve", "rescore", "g1", "--judge-sv", str(_judge_file(tmp_path))]
    assert cli.main(args) == cli.EXIT_OK
    out = capsys.readouterr().out
    rows = _cross_judge(database_url)
    if published:
        ((gate, cross),) = rows
        assert gate == "g1" and cross["label"] in ("agrees", "cross-judge-sensitive")
        assert set(cross) == {"label", "mean_delta", "ci95_low", "ci95_high", "judge",
                              "gate_delta", "rescored_on"}  # fmt: skip
        assert "page 4: the cross-judge label is on gate g1's published summary" in out
    else:
        assert rows == []
        assert "no published summary for gate g1" in out


def test_a_failed_page_four_write_keeps_the_rescore(roots, tmp_path, monkeypatch, capsys):
    import asyncio
    import contextlib

    from womm.evolve import promotion as pm

    store = asyncio.run(_seeded(roots))
    cli = _cli_store(monkeypatch, store)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/main")

    @contextlib.asynccontextmanager
    async def fake_db(_what):
        yield object()

    async def broken(*_a, **_k):
        raise RuntimeError("main database down")

    monkeypatch.setattr(cli, "_evolve_db", fake_db)
    monkeypatch.setattr(pm, "record_cross_judge", broken)
    args = ["evolve", "rescore", "g1", "--judge-sv", str(_judge_file(tmp_path))]
    assert cli.main(args) == cli.EXIT_FAILED
    captured = capsys.readouterr()
    assert "cross-judge check (fake:" in captured.out
    assert "main database down" in captured.err
    assert len(store.rescores) == 1, "the holdout re-score record is kept"


def test_rescore_runs_with_the_holdout_url_set():
    from womm import cli

    assert "rescore" in cli.HOLDOUT_SIDE_EVOLVE
