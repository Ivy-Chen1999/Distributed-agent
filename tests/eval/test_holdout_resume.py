"""Resumable holdout comparisons (self-evolution plan U7, R29): ``compare(checkpoint=True)``
saves every scored run and a restarted comparison re-runs only what was not finished."""

import pytest

from womm.config import REPO_ROOT
from womm.data import fixtures as fixtures_module
from womm.decisions.stub import StubDecisionService
from womm.eval import holdout
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity
from womm.models.system_version import build_system_version

from ..graph.conftest import fake_sv
from . import holdout_factory as hf
from .test_holdout import CLEAN, MemoryStore, _sealed


class Crash(RuntimeError):
    pass


class ProgressStore(MemoryStore):
    """A MemoryStore with compare progress; ``crash_after`` saves raise (a killed process)."""

    def __init__(self, sealed, progress=None, crash_after=None):
        super().__init__(sealed)
        self.progress = {} if progress is None else progress
        self.crash_after = crash_after
        self.loads = []

    async def load_progress(self, version_ids, judge_version, code_version):
        self.loads.append((tuple(version_ids), judge_version, code_version))
        return {(v, k, r): s for (v, j, c, k, r), s in self.progress.items()
                if v in version_ids and j == judge_version and c == code_version}  # fmt: skip

    async def save_progress(self, version_id, judge_version, code_version, key, rep, score):
        if self.crash_after is not None and len(self.progress) >= self.crash_after:
            raise Crash("killed mid-comparison")
        self.progress[(version_id, judge_version, code_version, key, rep)] = score

    async def record_audit(self, result, git_sha, **tags):
        self.audits.append((result, git_sha, tags))
        return len(self.audits)


@pytest.fixture
def roots(tmp_path, monkeypatch):
    monkeypatch.setattr(fixtures_module, "FIXTURES_ROOT", hf.make_fixtures_root(tmp_path))
    (tmp_path / "golden").mkdir()
    return tmp_path


def _versions():
    """Two distinct fake versions (the candidate's description differs)."""
    base = fake_sv()
    spec = base.spec.model_copy(update={"description": "candidate under the gate"})
    cand = build_system_version(spec, REPO_ROOT)
    assert cand.version_id != base.version_id
    return cand, base


def _backends(n_runs):
    raw = hf.compare_backends(runs_per_version=n_runs)
    return raw["candidate"]["fake"], raw["baseline"]["fake"]


async def _run(store, cand, base, cand_be, base_be, reps=2, code=CLEAN, **kw):
    return await holdout.compare(
        cand, base, reps, store=store,
        backends=lambda sv: {"fake": cand_be if sv is cand else base_be},
        decisions=StubDecisionService(), code=code, n_boot=200, checkpoint=True, **kw,
    )  # fmt: skip


def _graph_runs(backend: FakeBackend) -> int:
    return sum(c.role_name == "planner" for c in backend.calls)


async def test_an_interrupted_comparison_resumes_without_rerunning_finished_runs(roots):
    sealed = _sealed(roots, hf.FIVE_PROPOSALS)  # 5 cases x 2 repetitions per side
    cand, base = _versions()

    # Uninterrupted reference run.
    ref_store = ProgressStore(sealed)
    ref = await _run(ref_store, cand, base, *_backends(10))

    # Killed after 3 checkpointed runs, then restarted on the same progress table.
    progress = {}
    killed = ProgressStore(sealed, progress, crash_after=3)
    c1, b1 = _backends(10)
    with pytest.raises(Crash):
        await _run(killed, cand, base, c1, b1)
    assert len(progress) == 3 and killed.audits == []
    resumed = ProgressStore(sealed, progress)
    c2, b2 = _backends(10)
    result = await _run(resumed, cand, base, c2, b2)

    assert _graph_runs(c1) == 4  # 3 saved, the 4th crashed while saving
    assert _graph_runs(c2) == 10 - 3, "finished candidate runs are not re-run"
    assert _graph_runs(b2) == 10
    assert result == ref
    assert len(progress) == 20
    # Progress keys are internal hashes, never case ids.
    for (_v, _j, _c, key, _r), score in progress.items():
        assert key.startswith("k") and score.case_id == key
        assert not any(key == c.case_id for c, _ in sealed)


async def test_a_later_comparison_reuses_the_baselines_runs(roots):
    sealed = _sealed(roots, hf.FIVE_PROPOSALS)
    cand, base = _versions()
    progress = {}
    await _run(ProgressStore(sealed, progress), cand, base, *_backends(10))
    other = build_system_version(
        base.spec.model_copy(update={"description": "a second candidate"}), REPO_ROOT
    )
    c, b = _backends(10)
    store = ProgressStore(sealed, progress)
    result = await _run(store, other, base, c, b)
    assert _graph_runs(b) == 0, "the incumbent's holdout runs are reused across cycles"
    assert _graph_runs(c) == 10 and not result.aborted


async def test_checkpoints_are_per_code_version_and_skipped_for_unidentified_code(roots):
    sealed = _sealed(roots)
    cand, base = _versions()
    progress = {}
    await _run(ProgressStore(sealed, progress), cand, base, *_backends(4))
    c, b = _backends(4)
    newer = CodeIdentity(git_sha="def", dirty=False)
    await _run(ProgressStore(sealed, progress), cand, base, c, b, code=newer)
    assert _graph_runs(c) == 4, "another git sha never reuses scores"
    unknown = ProgressStore(sealed)
    await _run(unknown, cand, base, *_backends(4), code=CodeIdentity(git_sha=None, dirty=False))
    assert unknown.progress == {} and unknown.loads == []


async def test_gate_tags_reach_the_audit_row(roots):
    sealed = _sealed(roots)
    cand, base = _versions()
    store = ProgressStore(sealed)
    await _run(store, cand, base, *_backends(4), gate_id="gate_1", cycle_id="cycle_a")
    assert store.audits[0][2] == {"gate_id": "gate_1", "cycle_id": "cycle_a"}


async def test_without_checkpoint_nothing_is_saved(roots):
    sealed = _sealed(roots)
    cand, base = _versions()
    store = ProgressStore(sealed)
    c, b = _backends(4)
    await holdout.compare(
        cand, base, 2, store=store, backends=lambda sv: {"fake": c if sv is cand else b},
        decisions=StubDecisionService(), code=CLEAN, n_boot=50,
    )  # fmt: skip
    assert store.progress == {} and store.loads == []


def _claude_code(sv):
    from pathlib import Path

    from womm.models.system_version import derive_system_version

    return derive_system_version(sv, Path("."), backends=dict.fromkeys(sv.spec.roles(),
                                                                      "claude_code"))  # fmt: skip


def test_the_checkpoint_key_includes_the_claude_cli_version_for_claude_code_versions():
    _, base = _versions()
    cc = _claude_code(base)
    code = CodeIdentity(git_sha="abc", dirty=False, claude_cli_version="2.1.0 (Claude Code)")
    assert holdout.code_version(code, base) == "abc", "no claude_code role: the CLI is irrelevant"
    assert holdout.code_version(code, cc) == "abc+claude_cli.2.1.0 (Claude Code)"
    assert holdout.code_version(code.model_copy(update={"claude_cli_version": None}), cc) is None


async def test_a_new_claude_cli_version_never_reuses_claude_code_checkpoints(roots):
    sealed = _sealed(roots)
    cand, base = (_claude_code(sv) for sv in _versions())
    progress = {}

    async def run(cli):
        c, b = _backends(4)
        await holdout.compare(
            cand, base, 2, store=ProgressStore(sealed, progress),
            backends=lambda sv: {"claude_code": c if sv is cand else b},
            decisions=StubDecisionService(), n_boot=50, checkpoint=True,
            code=CodeIdentity(git_sha="abc", dirty=False, claude_cli_version=cli),
        )  # fmt: skip
        return _graph_runs(c) + _graph_runs(b)

    assert await run("2.1.0") == 8
    assert await run("2.1.0") == 0, "same CLI: every run is reused"
    assert await run("2.2.0") == 8, "another CLI version never reuses scores"
