"""Whole-version cost sweep (EU cost plan U5): deterministic batches, resumable, per repetition."""

import ast
import json
from pathlib import Path

import pytest

import womm.cost.sweep as sweep_mod
from womm.config import REPO_ROOT
from womm.cost.sweep import SweepError, load_sweep, plan_sweep, run_sweep, sweep_dir
from womm.data.corpus import load_corpus
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend, fake_cost_batch
from womm.models.run import CodeIdentity
from womm.models.system_version import build_system_version, load_system_version

PROPOSAL = "com2021_206"
CODE = CodeIdentity(git_sha="abc1234def5678", dirty=False)


@pytest.fixture(scope="module")
def corpus():
    return load_corpus()


def _sv(max_records=60):
    base = load_system_version(REPO_ROOT / "system_versions" / "v1.0-cost.yaml", REPO_ROOT)
    data = base.spec.model_dump()
    data["cost"].update(backend="fake", max_records_per_call=max_records)
    return build_system_version(type(base.spec).model_validate(data), REPO_ROOT)


def test_a_proposal_sweep_plans_every_duty_and_prohibition(corpus):
    plan = plan_sweep(_sv(), corpus, PROPOSAL)
    assert sum(len(b.items) for b in plan.batches) == 506
    assert len(plan.batches) == 9
    again = plan_sweep(_sv(), corpus, PROPOSAL)
    assert [b.batch_id for b in plan.batches] == [b.batch_id for b in again.batches]


def test_an_unknown_corpus_version_is_refused_with_the_list(corpus):
    with pytest.raises(SweepError, match="com2021_206.*reg2024_1689"):
        plan_sweep(_sv(), corpus, "com2099_1")


def test_the_sweep_directory_names_version_code_and_sv(tmp_path):
    sv = _sv()
    d = sweep_dir(tmp_path, sv, PROPOSAL, CODE)
    assert d == tmp_path / sv.version_id / PROPOSAL / "git-abc1234def56"
    dirty = CodeIdentity(git_sha="abc1234def5678", dirty=True, diff_sha="ffee")
    assert sweep_dir(tmp_path, sv, PROPOSAL, dirty).name == "git-abc1234def56-dirty-ffee"
    other = _sv(max_records=50)
    assert sweep_dir(tmp_path, other, PROPOSAL, CODE) != d


async def test_a_full_sweep_writes_one_file_per_batch_and_loads_back(corpus, tmp_path):
    sv = _sv()
    plan = plan_sweep(sv, corpus, PROPOSAL)
    backend = FakeBackend({"cost": [fake_cost_batch] * 20})
    out = await run_sweep(plan, backend=backend, root=tmp_path, code=CODE, repetitions=1)
    files = sorted((out / "rep1").glob("*.json"))
    assert [f.stem for f in files] == sorted(b.batch_id for b in plan.batches)
    first = json.loads(files[0].read_text())
    assert first["code_identity"]["git_sha"] == CODE.git_sha
    assert first["system_version"] == sv.version_id
    result = load_sweep(out)
    assert result.version == PROPOSAL and result.system_version == sv.version_id
    assert len(result.repetitions[1]) == 506
    assert result.missing == {}


async def test_a_restart_skips_finished_batches(corpus, tmp_path):
    plan = plan_sweep(_sv(), corpus, PROPOSAL)
    steps = [fake_cost_batch, fake_cost_batch] + [LLMError("rate_limit", "stop")] * 7
    out = await run_sweep(
        plan, backend=FakeBackend({"cost": steps}), root=tmp_path, code=CODE, repetitions=1,
        max_parallel=1,
    )  # fmt: skip
    assert len(list((out / "rep1").glob("*.json"))) == 2
    assert len(load_sweep(out).missing[1]) == 7
    backend = FakeBackend({"cost": [fake_cost_batch] * 20})
    await run_sweep(plan, backend=backend, root=tmp_path, code=CODE, repetitions=1)
    assert len(backend.calls) == 7
    finished = {c.user_content for c in backend.calls}
    first_two = {sweep_mod.render_batch(b) for b in plan.batches[:2]}
    assert not finished & first_two
    assert load_sweep(out).missing == {}


async def test_a_second_repetition_does_not_reuse_the_first(corpus, tmp_path):
    plan = plan_sweep(_sv(), corpus, PROPOSAL)
    backend = FakeBackend({"cost": [fake_cost_batch] * 40})
    out = await run_sweep(plan, backend=backend, root=tmp_path, code=CODE, repetitions=1)
    await run_sweep(plan, backend=backend, root=tmp_path, code=CODE, repetitions=2)
    assert len(backend.calls) == 18
    assert sorted(load_sweep(out).repetitions) == [1, 2]


async def test_the_sweep_stops_at_max_usd(corpus, tmp_path):
    plan = plan_sweep(_sv(), corpus, PROPOSAL)

    class Priced(FakeBackend):
        async def _invoke(self, *a, **kw):
            step, usage = await super()._invoke(*a, **kw)
            return step, usage.model_copy(update={"cost_usd": 1.0})

    backend = Priced({"cost": [fake_cost_batch] * 20})
    out = await run_sweep(
        plan, backend=backend, root=tmp_path, code=CODE, repetitions=1, max_usd=2.5,
        max_parallel=1,
    )  # fmt: skip
    assert len(backend.calls) == 3
    assert len(load_sweep(out).missing[1]) == 6


def test_the_sweep_never_reads_evals():
    """No reference to the evals/ tree or womm.eval in the sweep module."""
    path = Path(sweep_mod.__file__)
    text = path.read_text(encoding="utf-8")
    assert "evals" not in text
    imports = [
        n.module for n in ast.walk(ast.parse(text)) if isinstance(n, ast.ImportFrom) and n.module
    ]
    assert not [m for m in imports if m.startswith("womm.eval")]
