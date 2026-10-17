"""`womm evolve replay` and `womm evolve worker` on the fake backend (U4)."""

import asyncio
import json

import yaml

from womm import cli
from womm.api.db import Database
from womm.config import REPO_ROOT
from womm.evolve.archive import Archive
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend
from womm.models.system_version import load_system_version

from .test_replay import script


async def _archive(database_url, path):
    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        sv = load_system_version(path, REPO_ROOT)
        await Archive(db).archive(sv, origin="seed")
        return sv
    finally:
        await db.close()


def test_replay_then_resume_is_cached(
    database_url, fake_version, tmp_path, capsys, use_script, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    sv = asyncio.run(_archive(database_url, fake_version))
    use_script(script(1))
    common = ["--seed", str(fake_version), "--runs-dir", str(tmp_path / "runs"), "--json"]
    code = cli.main(["evolve", "replay", sv.version_id, "--split", "train",
                     "--case", "case_02_sme_impacts", *common])  # fmt: skip
    status = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK and status["done"] == 1 and status["complete"]

    use_script({})  # a resumed finished batch makes no LLM call
    code = cli.main(["evolve", "worker", status["batch_id"], *common])
    assert code == cli.EXIT_OK and json.loads(capsys.readouterr().out)["done"] == 1

    code = cli.main(["evolve", "replay", "sv_unknown", "--split", "train", *common])
    assert code == cli.EXIT_USAGE


def test_halted_batch_exits_non_zero_until_resumed(
    database_url, fake_version, tmp_path, capsys, use_script, monkeypatch
):
    monkeypatch.setenv("DATABASE_URL", database_url)
    sv = asyncio.run(_archive(database_url, fake_version))
    limited = script(1)
    limited["judge"] = [LLMError("rate_limit", "usage limit reached")]
    use_script(limited)
    common = ["--seed", str(fake_version), "--runs-dir", str(tmp_path / "runs"), "--json"]
    code = cli.main(["evolve", "replay", sv.version_id, "--split", "train",
                     "--case", "case_02_sme_impacts", *common])  # fmt: skip
    status = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_FAILED and status["halted"] and not status["complete"]

    use_script(script(1))
    code = cli.main(["evolve", "worker", status["batch_id"], *common])
    assert code == cli.EXIT_FAILED and json.loads(capsys.readouterr().out)["halted"]
    code = cli.main(["evolve", "worker", status["batch_id"], "--resume", *common])
    status = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK and status["complete"] and status["done"] == 1


def test_replay_builds_the_judge_backend_from_the_pinned_seed(
    database_url, fake_version, tmp_path, capsys, use_script, monkeypatch
):
    """The candidate runs on ``fake`` only; the seed's judge is on another backend, which the
    worker must also have."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    sv = asyncio.run(_archive(database_url, fake_version))
    seed = yaml.safe_load(fake_version.read_text())
    seed["judge"]["backend"] = "api"
    seed_path = tmp_path / "seed.yaml"
    seed_path.write_text(yaml.safe_dump(seed))
    use_script({})
    backend = FakeBackend(script(1))
    prepared = []

    async def fake_prepare(version, *, skip_self_check=False, settings=None):
        prepared.append(version.version_id)
        return {r.backend: backend for r in version.spec.roles().values()}, None, None

    monkeypatch.setattr(cli, "prepare_backends", fake_prepare)
    common = ["--seed", str(seed_path), "--runs-dir", str(tmp_path / "runs"), "--json"]
    code = cli.main(["evolve", "replay", sv.version_id, "--split", "train",
                     "--case", "case_02_sme_impacts", *common])  # fmt: skip
    status = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK and status["done"] == 1 and status["complete"]
    assert any(c.role_name == "judge" for c in backend.calls)
