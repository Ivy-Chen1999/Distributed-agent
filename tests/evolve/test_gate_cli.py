"""`womm evolve promote`, `show` and `diffcheck` (U7, U8): refusals before any backend or
holdout run, the sealed audit reader, and the R37 check without reference answers."""

import asyncio

from womm import cli
from womm.config import REPO_ROOT

from .test_promotion import _rows

# ----------------------------------------------------------------------------- CLI


async def _archive_api_pair(database_url):
    from womm.api.db import Database
    from womm.evolve.archive import Archive, archive_child, seed_archive
    from womm.evolve.edits import validate_diff

    from .test_archive import _fiscal_edit

    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        archive = Archive(db)
        _, api_seed = await seed_archive(archive, REPO_ROOT)
        diff = validate_diff(api_seed, _fiscal_edit(api_seed))
        child = await archive_child(archive, api_seed, diff, origin="gepa", cycle_id="cycle_cli")
        return child, api_seed
    finally:
        await db.close()


def test_promote_cli_refuses_an_unsigned_formal_gate_before_any_backend(
    database_url, holdout_url, monkeypatch, capsys
):
    child, api_seed = asyncio.run(_archive_api_pair(database_url))
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("HOLDOUT_DATABASE_URL", holdout_url)

    def no_backends(*_a, **_k):
        raise AssertionError("prepared a backend before the gate's preconditions")

    monkeypatch.setattr(cli, "prepare_backends", no_backends)
    argv = ["evolve", "promote", child.version_id, "--incumbent", api_seed.version_id]
    assert cli.main(argv) == cli.EXIT_USAGE
    assert "not signed" in capsys.readouterr().err
    assert _rows(holdout_url, "SELECT count(*) FROM holdout.compare_audit") == [(0,)]
    assert cli.main([*argv[:2], "sv_unknown", *argv[3:]]) == cli.EXIT_USAGE
    assert "only archived versions" in capsys.readouterr().err


def test_show_cli_reads_the_sealed_audit(holdout_url, monkeypatch, capsys):
    monkeypatch.setenv("HOLDOUT_DATABASE_URL", holdout_url)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert cli.main(["evolve", "show", "sv_none"]) == cli.EXIT_OK
    assert "no gate decision recorded for sv_none" in capsys.readouterr().out


# ----------------------------------------------------------------------------- CLI


def test_diffcheck_reports_not_available_without_touching_the_database(monkeypatch, capsys):
    def no_db(*_a, **_k):
        raise AssertionError("diffcheck opened the database without reference answers")

    monkeypatch.delenv("HOLDOUT_DATABASE_URL", raising=False)
    monkeypatch.setattr(cli, "_evolve_db", no_db)
    assert cli.main(["evolve", "diffcheck", "sv_x"]) == cli.EXIT_OK
    assert "R37 diff check: not_available" in capsys.readouterr().out
