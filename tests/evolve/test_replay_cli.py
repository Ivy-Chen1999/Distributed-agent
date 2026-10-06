"""`womm evolve replay` and `womm evolve worker` on the fake backend (U4)."""

import asyncio
import json

from womm import cli
from womm.api.db import Database
from womm.config import REPO_ROOT
from womm.evolve.archive import Archive
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
