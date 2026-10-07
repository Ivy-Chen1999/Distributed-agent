"""Planner data boundary (U6; R23, R26, R28, AE3): holdout cases, holdout results and promotion
decisions cannot reach the Improvement Planner."""

import ast
import json
import re
import shutil
from pathlib import Path

import psycopg
import pytest

import womm
from womm.config import REPO_ROOT
from womm.eval import holdout
from womm.evolve.archive import Archive, archive_child, seed_archive
from womm.evolve.edits import validate_diff
from womm.evolve.failure_memory import CaseRun, FailureEvent
from womm.evolve.planner_view import (
    ALLOWED_TABLES,
    HOLDOUT_URL_ENV,
    QUERIES,
    HoldoutEnvRefused,
    PlannerView,
    refuse_holdout_env,
)
from womm.llm.claude_code import build_child_env
from womm.models.run import CodeIdentity, RunResult, RunStatus

from .test_archive import BASE, PROVENANCE, _fiscal_edit

SRC = Path(womm.__file__).parent
# The one womm.evolve module allowed to reach the holdout: the promotion gate (U7).
HOLDOUT_SIDE = frozenset({"promotion"})
# The IA cost check (EU cost plan U8) is scoring-side only: cost scores never reach the Planner.
FORBIDDEN = ("womm.eval.holdout", "womm.evolve.promotion", "womm.eval.cost_check")
DYNAMIC_IMPORTS = frozenset({"import_module", "__import__"})


def planner_side(root: Path) -> list[str]:
    """Every module of the ``womm.evolve`` package except the promotion gate: a new module is
    Planner-side by default."""
    names = [p.stem for p in (root / "evolve").glob("*.py")]
    return sorted(n for n in names if n not in HOLDOUT_SIDE)


CANARY = "CANARY_7f3a_holdout_decision"


# ---------------------------------------------------------------- import boundary


def _module_file(root: Path, name: str) -> Path | None:
    parts = name.split(".")[1:]
    candidates = (root.joinpath(*parts).with_suffix(".py"), root.joinpath(*parts, "__init__.py"))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _imports(root: Path, name: str) -> set[str]:
    """Every womm module ``name`` imports, including packages whose __init__ runs."""
    path = _module_file(root, name)
    if path is None:
        return set()
    package = name if path.name == "__init__.py" else name.rsplit(".", 1)[0]
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and node.args:
            # importlib.import_module("womm...") / __import__("womm..."): a string literal
            # anywhere in the arguments counts as an import of that module.
            func = node.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if fname in DYNAMIC_IMPORTS:
                out |= {a.value for a in ast.walk(node) if isinstance(a, ast.Constant)
                        and isinstance(a.value, str) and a.value.startswith("womm")}  # fmt: skip
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1]
                                + ([node.module] if node.module else []))  # fmt: skip
            out.add(base)
            out |= {f"{base}.{a.name}" for a in node.names}
    found = set()
    for mod in out:
        if mod == "womm" or mod.startswith("womm."):
            parts = mod.split(".")
            found |= {".".join(parts[:i]) for i in range(2, len(parts) + 1)}
    return {m for m in found if _module_file(root, m) is not None or m in FORBIDDEN}


def forbidden_reach(root: Path, forbidden: tuple[str, ...] = FORBIDDEN) -> dict[str, list[str]]:
    """For each existing Planner-side module, the import chain to a forbidden module, if any."""
    bad: dict[str, list[str]] = {}
    for short in planner_side(root):
        start = f"womm.evolve.{short}"
        if start in forbidden:
            continue
        parent: dict[str, str | None] = {start: None}
        queue = [start]
        while queue:
            mod = queue.pop()
            if mod in forbidden:
                chain = [mod]
                while parent[chain[-1]] is not None:
                    chain.append(parent[chain[-1]])
                bad[start] = chain[::-1]
                break
            for dep in _imports(root, mod):
                if dep not in parent:
                    parent[dep] = mod
                    queue.append(dep)
    return bad


def test_planner_side_never_imports_the_holdout():
    assert forbidden_reach(SRC) == {}
    assert _module_file(SRC, "womm.evolve.planner_view") is not None


# The R37 diff check (U8) is a monitor the Planner must not optimise: no other Planner-side module
# (the GEPA adapter, the proposers, the cycle, the PlannerView, ...) reaches the module that
# reads its reference answers.
DIFF_CHECK = ("womm.evolve.diff_regression",)


def test_no_planner_input_module_reaches_the_r37_diff_check():
    assert _module_file(SRC, DIFF_CHECK[0]) is not None
    assert forbidden_reach(SRC, DIFF_CHECK) == {}
    sources = {p.stem: p.read_text(encoding="utf-8") for p in (SRC / "evolve").glob("*.py")}
    for name, text in sources.items():
        if name != "diff_regression":
            assert "diff_regression/" not in text and "REFERENCE_PATH" not in text, name


def test_the_diff_check_import_check_catches_an_added_import(tmp_path):
    tree = tmp_path / "womm"
    shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__"))
    (tree / "evolve" / "gepa_adapter.py").write_text("from womm.evolve import diff_regression\n")
    assert "womm.evolve.gepa_adapter" in forbidden_reach(tree, DIFF_CHECK)


def test_import_graph_check_catches_an_added_import(tmp_path):
    tree = tmp_path / "womm"
    shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__"))
    (tree / "evolve" / "proposers.py").write_text("from womm.evolve import archive\n")
    archive = tree / "evolve" / "archive.py"
    archive.write_text(archive.read_text() + "\nimport womm.eval.holdout  # noqa\n")
    bad = forbidden_reach(tree)
    assert bad["womm.evolve.proposers"] == [
        "womm.evolve.proposers", "womm.evolve.archive", "womm.eval.holdout",
    ]  # fmt: skip
    assert "womm.evolve.archive" in bad


@pytest.mark.parametrize(
    "source",
    [
        'import importlib\nholdout = importlib.import_module("womm.eval.holdout")\n',
        'from importlib import import_module\nh = import_module("womm.eval.holdout")\n',
        'h = __import__("womm.eval.holdout", fromlist=["x"])\n',
        'import importlib\np = importlib.import_module("womm.evolve.promotion")\n',
    ],
)
def test_import_graph_check_catches_a_dynamic_import(tmp_path, source):
    tree = tmp_path / "womm"
    shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__"))
    (tree / "evolve" / "proposers.py").write_text(source)
    assert "womm.evolve.proposers" in forbidden_reach(tree)


def test_a_new_evolve_module_is_planner_side_by_default(tmp_path):
    tree = tmp_path / "womm"
    shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__"))
    (tree / "evolve" / "brand_new.py").write_text("from womm.eval import holdout  # noqa\n")
    (tree / "evolve" / "promotion.py").write_text("from womm.eval import holdout  # noqa\n")
    bad = forbidden_reach(tree)
    assert "womm.evolve.brand_new" in bad and "womm.evolve.promotion" not in bad
    assert {"replay", "cycle", "planner_view"} <= set(planner_side(SRC))


# ---------------------------------------------------------------- process boundary


def test_holdout_url_env_name_matches_the_holdout_module():
    assert HOLDOUT_URL_ENV == holdout.HOLDOUT_URL_ENV


def test_planner_view_refuses_a_process_with_the_holdout_url(tmp_path):
    env = {HOLDOUT_URL_ENV: "postgresql://x/holdout"}
    with pytest.raises(HoldoutEnvRefused, match=HOLDOUT_URL_ENV):
        refuse_holdout_env(env)
    with pytest.raises(HoldoutEnvRefused):
        PlannerView("postgresql://unused/db", runs_dir=tmp_path, env=env)
    refuse_holdout_env({})


EVOLVE_ARGV = {
    "failures": ["failures", "--sv", "sv_x", "--from-db"],
    "seed": ["seed"],
    "materialize": ["materialize", "sv_x"],
    "replay": ["replay", "sv_x", "--split", "train"],
    "worker": ["worker", "rb_x"],
    "cycle": ["cycle", "--base", "sv_x"],
    "diffcheck": ["diffcheck", "sv_x"],
    "twin": ["twin", "sv_x"],
}
# The promotion side: the only evolve commands that may (and must) reach the holdout.
HOLDOUT_ARGV = {
    "promote": ["promote", "sv_x", "--incumbent", "sv_y"],
    "show": ["show", "sv_x"],
}


def test_every_evolve_subcommand_is_covered():
    from womm import cli

    assert set(EVOLVE_ARGV) | set(HOLDOUT_ARGV) == set(cli.EVOLVE_HANDLERS)
    assert set(HOLDOUT_ARGV) == cli.HOLDOUT_SIDE_EVOLVE


@pytest.mark.parametrize("cmd", sorted(HOLDOUT_ARGV))
def test_promotion_side_commands_need_the_holdout_url(cmd, monkeypatch, capsys):
    from womm import cli

    def no_db(*_a, **_k):
        raise AssertionError("touched the main database before finding the holdout")

    monkeypatch.delenv(HOLDOUT_URL_ENV, raising=False)
    monkeypatch.setattr(cli, "_evolve_db", no_db)
    assert cli.main(["evolve", *HOLDOUT_ARGV[cmd]]) == cli.EXIT_USAGE
    assert HOLDOUT_URL_ENV in capsys.readouterr().err


@pytest.mark.parametrize("cmd", sorted(EVOLVE_ARGV))
def test_evolve_command_refuses_the_holdout_url(cmd, monkeypatch, capsys):
    from womm import cli

    def no_db(*_a, **_k):
        raise AssertionError("an evolve command touched the database before the holdout check")

    monkeypatch.setenv(HOLDOUT_URL_ENV, "postgresql://x/holdout")
    monkeypatch.setattr(cli, "_evolve_db", no_db)
    assert cli.main(["evolve", *EVOLVE_ARGV[cmd]]) == cli.EXIT_USAGE
    assert HOLDOUT_URL_ENV in capsys.readouterr().err


def test_claude_code_subprocess_env_has_no_holdout_url():
    env = build_child_env({"PATH": "/usr/bin", "HOME": "/tmp", HOLDOUT_URL_ENV: "postgresql://x"})
    assert HOLDOUT_URL_ENV not in env


# ---------------------------------------------------------------- query boundary


def test_queries_only_touch_allowed_tables():
    for name, sql in QUERIES.items():
        tables = set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]+)", sql, flags=re.I)) - {"up"}
        assert tables <= ALLOWED_TABLES, (name, tables)
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER)\b", sql, flags=re.I), name
    assert not {"promotion_decisions", "compare_audit", "replay_items"} & ALLOWED_TABLES


# ---------------------------------------------------------------- canaries (AE3)


async def _populate(db, tmp_path):
    archive = Archive(db)
    await seed_archive(archive, REPO_ROOT)
    child = await archive_child(archive, BASE, validate_diff(BASE, _fiscal_edit(BASE)),
                                origin="gepa")  # fmt: skip
    run = CaseRun(case_id="case_02_sme_impacts", fixture="ai_act", split="train", run_id="r1",
                  repetition=1, system_version=child.version_id)  # fmt: skip
    event = FailureEvent(kind="missed_impact", case_id="case_02_sme_impacts", fixture="ai_act",
                         split="train", item_id="c02_e01", category="compliance_cost",
                         owner="none", run_id="r1", repetition=1,
                         system_version=child.version_id)  # fmt: skip
    await db.record_failure_events([event], [run])
    row = {"level": "split", "subject": "", "metric": "coverage", "n": 1}
    await archive.record_metrics(child.version_id, "train", [row | {"mean": 0.5, "sd": None}],
                                 **PROVENANCE)  # fmt: skip
    await archive.record_metrics(child.version_id, "diff_check", [row | {"mean": 0.123456,
                                                                         "sd": None}],
                                 **PROVENANCE | {"batch_id": "rb_diff"})  # fmt: skip
    async with db.pool.connection() as conn:
        # What U7 writes: a decision summary and a sealed audit, both with canaries.
        await conn.execute(
            "INSERT INTO promotion_decisions (gate_id, candidate_version, incumbent_version, mode,"
            " deployable, decision, label, reasons, n_proposals, policy_sha256) VALUES"
            " ('g1', %s, %s, 'dev', false, 'rejected', %s, %s, 5, 'sha')",
            (child.version_id, BASE.version_id, CANARY, json.dumps([CANARY])),
        )  # fmt: skip
        await conn.execute("CREATE SCHEMA holdout")
        await conn.execute("CREATE TABLE holdout.compare_audit (result text)")
        await conn.execute("INSERT INTO holdout.compare_audit VALUES (%s)", (CANARY,))
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    for run_id in ("r1", "r_holdout_like"):
        saved = RunResult(run_id=run_id, scenario_id="s", status=RunStatus.succeeded,
                          system_version=child.version_id,
                          code_identity=CodeIdentity(git_sha="g", dirty=False))  # fmt: skip
        (runs_dir / f"{run_id}.json").write_text(saved.model_dump_json())
    return child, runs_dir


async def test_no_planner_view_method_returns_a_canary(db, database_url, tmp_path):
    child, runs_dir = await _populate(db, tmp_path)
    async with PlannerView(database_url, runs_dir=runs_dir, env={}) as view:
        results = [
            await view.failure_events(child.version_id),
            await view.case_runs(child.version_id),
            await view.failure_patterns(child.version_id),
            await view.candidate(child.version_id),
            await view.lineage(child.version_id),
            await view.children(BASE.version_id),
            await view.metrics(child.version_id),
            await view.run("r1"),
            await view.run("../etc/passwd"),
        ]
        assert results[7].run_id == "r1"
        # A run file that is not a recorded train/val run is not readable.
        assert await view.run("r_holdout_like") is None and results[8] is None
        dumped = json.dumps(results, default=lambda o: o.model_dump(mode="json")
                            if hasattr(o, "model_dump") else str(o))  # fmt: skip
        assert CANARY not in dumped
        assert "0.123456" not in dumped  # the R37 diff check is not Planner input
        assert [m["split"] for m in results[6]] == ["train"]
        assert results[3]["prompts"] == child.prompts
        assert not {"decision", "promoted"} & set(results[3])
        assert [r["version_id"] for r in results[4]] == [BASE.version_id, child.version_id]

        # The connection is read-only.
        async with view._pool.connection() as conn:
            with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
                await conn.execute("DELETE FROM sv_metrics")


# ---------------------------------------------------------------- the GEPA adapter (U5)


def test_gepa_adapter_receives_only_a_planner_view():
    """The adapter's one data-access dependency is a PlannerView: no field is a database, an
    archive, a replay store or anything holdout-shaped. Writes go through a CandidateEvaluator
    whose replay refuses holdout splits."""
    import dataclasses
    import typing

    from womm.evolve.gepa_adapter import CandidateEvaluator, WommAdapter

    hints = typing.get_type_hints(WommAdapter)
    fields = {f.name: hints[f.name] for f in dataclasses.fields(WommAdapter)}
    assert fields["view"] is PlannerView
    assert fields["evaluator"] is CandidateEvaluator
    names = " ".join(str(t) for t in fields.values())
    for forbidden in ("Database", "Archive", "ReplayStore", "Holdout", "AsyncConnectionPool"):
        assert forbidden not in names, forbidden


def test_planner_view_cases_are_train_val_only(tmp_path):
    from womm.eval.golden import GoldenError

    from ..eval.test_run_eval_fake import CASE

    sealed = CASE.model_copy(update={"case_id": "h1", "split": "holdout"})
    view = PlannerView("postgresql://unused/db", runs_dir=tmp_path, env={},
                       load_cases=lambda split: [CASE, sealed])  # fmt: skip
    assert list(view.cases("train")) == [CASE.case_id]
    assert view.cases("val") == {}
    with pytest.raises(GoldenError, match="train/val"):
        view.cases("holdout")


async def test_failure_reads_take_train_or_val_splits_only(db, database_url, tmp_path):
    from womm.eval.golden import GoldenError

    async with PlannerView(database_url, runs_dir=tmp_path, env={}) as view:
        assert await view.failure_events("sv_x", ("train",)) == []
        for bad in (("holdout",), ("train", "holdout"), (), "train"):
            with pytest.raises(GoldenError, match="train/val"):
                await view.failure_patterns("sv_x", bad)
