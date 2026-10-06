"""`womm evolve cycle` on the fake backend (U5)."""

import asyncio
import json

import yaml

from womm import cli
from womm.api.db import Database
from womm.config import REPO_ROOT
from womm.evolve.archive import Archive
from womm.evolve.planner_view import HOLDOUT_URL_ENV

from .test_gepa_adapter import CASES, LONG, MARKER, graph_script, improve
from .test_replay_cli import _archive


def fake_config(tmp_path):
    data = yaml.safe_load((REPO_ROOT / "evals" / "evolution.yaml").read_text())
    for role in data["roles"].values():
        role["backend"] = "fake"
    data["budget"].update(train_repetitions=1, val_repetitions=2)
    path = tmp_path / "evolution.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_cycle_names_one_candidate(database_url, fake_version, tmp_path, capsys, use_script,
                                   monkeypatch):  # fmt: skip
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv(HOLDOUT_URL_ENV, raising=False)
    for module in ("womm.evolve.replay", "womm.evolve.planner_view"):
        monkeypatch.setattr(f"{module}.load_all_golden", lambda split=None: CASES.get(split, []))
    sv = asyncio.run(_archive(database_url, fake_version))
    use_script(graph_script() | {"improvement_planner": [improve] * LONG})
    code = cli.main([
        "evolve", "cycle", "--base", sv.version_id, "--seed", str(fake_version),
        "--config", str(fake_config(tmp_path)), "--max-metric-calls", "40",
        "--cycle-id", "c_cli", "--runs-dir", str(tmp_path / "runs"), "--json",
    ])  # fmt: skip
    out = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK, out
    assert out["cycle_id"] == "c_cli" and out["chosen"] == out["prompt_stage"]["best"]
    assert out["chosen"] != sv.version_id and out["prompt_stage"]["archived"]
    assert out["topology_stage"]["candidate"] is None
    chosen = asyncio.run(_load(database_url, out["chosen"]))
    assert MARKER in "".join(chosen.prompts.values())  # the fiscal edit made it


async def _load(database_url, version_id):
    db = Database(database_url)
    await db.open()
    try:
        return await Archive(db).load_candidate(version_id)
    finally:
        await db.close()


def test_cycle_refuses_the_holdout_url(monkeypatch, capsys):
    monkeypatch.setenv(HOLDOUT_URL_ENV, "postgresql://x/holdout")
    assert cli.main(["evolve", "cycle", "--base", "sv_x"]) == cli.EXIT_USAGE
    assert HOLDOUT_URL_ENV in capsys.readouterr().err


def test_cycle_without_val_cases_is_a_usage_error(database_url, fake_version, tmp_path, capsys,
                                                  use_script, monkeypatch):  # fmt: skip
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.delenv(HOLDOUT_URL_ENV, raising=False)
    sv = asyncio.run(_archive(database_url, fake_version))
    use_script({})
    monkeypatch.setattr("womm.evolve.planner_view.load_all_golden",
                        lambda split=None: CASES["train"] if split == "train" else [])  # fmt: skip
    code = cli.main(["evolve", "cycle", "--base", sv.version_id, "--seed", str(fake_version),
                     "--config", str(fake_config(tmp_path))])  # fmt: skip
    assert code == cli.EXIT_USAGE and "val case" in capsys.readouterr().err
