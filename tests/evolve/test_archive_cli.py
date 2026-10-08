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


def test_twin_archives_the_api_twin_of_a_cycle_candidate(database_url, capsys, monkeypatch):
    """Formal modes compare api twins: `womm evolve twin` archives one under its candidate,
    in the candidate's cycle, so the gate spends that cycle's budget."""
    monkeypatch.setenv("DATABASE_URL", database_url)
    assert cli.main(["evolve", "seed", "--json"]) == cli.EXIT_OK
    capsys.readouterr()
    child = asyncio.run(_archive_child(database_url))
    assert cli.main(["evolve", "twin", child.version_id, "--json"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["twin_of"] == child.version_id and data["version_id"] != child.version_id

    async def row():
        from womm.api.db import Database

        db = Database(database_url)
        await db.open()
        try:
            archive = Archive(db)
            twin = await archive.load_candidate(data["version_id"])
            return twin, await archive.get(data["version_id"])
        finally:
            await db.close()

    twin, archived = asyncio.run(row())
    assert {r.backend for r in twin.spec.roles().values()} == {"api"}
    assert archived["origin"] == "twin" and archived["twin_of"] == child.version_id
    assert cli.main(["evolve", "twin", child.version_id, "--json"]) == cli.EXIT_OK, "idempotent"
    assert json.loads(capsys.readouterr().out)["version_id"] == data["version_id"]
    assert cli.main(["evolve", "twin", "sv_unknown"]) == cli.EXIT_USAGE
