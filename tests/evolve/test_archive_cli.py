"""`womm evolve seed` and `womm evolve materialize` against a throwaway database (U3)."""

import asyncio
import json

from womm import cli
from womm.config import REPO_ROOT
from womm.evolve.archive import Archive, archive_child
from womm.evolve.edits import validate_diff
from womm.models.system_version import load_system_version

from .test_archive import BASE, _fiscal_edit


async def _archive_child(database_url):
    from womm.api.db import Database

    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        return await archive_child(
            Archive(db), BASE, validate_diff(BASE, _fiscal_edit(BASE)), origin="manual"
        )
    finally:
        await db.close()


def test_seed_then_materialize(database_url, tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert cli.main(["evolve", "seed", "--json"]) == cli.EXIT_OK
    seeds = json.loads(capsys.readouterr().out)
    assert [s["name"] for s in seeds] == ["v1.0-unscoped", "v1.0-unscoped-api"]

    child = asyncio.run(_archive_child(database_url))
    code = cli.main(["evolve", "materialize", child.version_id, "--out", str(tmp_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK and data["version_id"] == child.version_id
    assert load_system_version(tmp_path / data["path"], tmp_path).version_id == child.version_id
    assert not (REPO_ROOT / "system_versions" / "candidates").exists()

    assert cli.main(["evolve", "materialize", "sv_unknown", "--out", str(tmp_path)]) == 2


def test_materialize_needs_a_database(capsys, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: None)
    assert cli.main(["evolve", "materialize", "sv_x"]) == cli.EXIT_USAGE
    assert "DATABASE_URL" in capsys.readouterr().err
