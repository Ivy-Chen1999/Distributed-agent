"""IA isolation for the cost path (EU cost plan U8; parent R11, R24).

The impact assessment, the IA cost reference and cost scores never reach any agent: no graph,
cost or Planner-side module imports the cost check, no prompt or rendered cost input carries an
IA figure, and the coverage judge never sees the cost section.
"""

import re
import shutil
from pathlib import Path

import pytest

import womm
from womm.config import REPO_ROOT
from womm.cost.estimate import render_batch
from womm.cost.sweep import plan_sweep
from womm.data.corpus import load_default_corpus
from womm.eval.cost_check import REFERENCE_PATH
from womm.evolve.planner_view import ALLOWED_TABLES, QUERIES

from ..evolve.test_planner_boundary import HOLDOUT_SIDE, _imports, _module_file

SRC = Path(womm.__file__).parent
COST_CHECK = "womm.eval.cost_check"

# SWD(2021) 84 figures (EU cost plan, IA figure audit): none may appear in any agent input.
CANARIES = {
    "2 763": r"2[\s,.  ]?763",
    "4 390": r"4[\s,.  ]?390",
    "3 627": r"3[\s,.  ]?627",
    "10 733": r"10[\s,.  ]?733",
    "7 764": r"7[\s,.  ]?764",
    "170 000": r"170[\s,.  ]?000",
    "1-25 FTE": r"1\s*[-–—]\s*25\s*FTE",
}


def canaries_in(text: str) -> list[str]:
    return [
        name for name, pattern in CANARIES.items() if re.search(rf"(?<!\d){pattern}(?!\d)", text)
    ]


# ---------------------------------------------------------------- import boundary


def _modules(root: Path, package: str) -> list[str]:
    base = root.joinpath(*package.split(".")[1:])
    return sorted(f"{package}.{p.stem}" for p in base.glob("*.py") if p.stem != "__init__")


def agent_side(root: Path) -> list[str]:
    """Every module that builds or feeds an agent input: the graph, the cost package and the
    Planner-side womm.evolve modules (all but the promotion gate)."""
    evolve = [m for m in _modules(root, "womm.evolve") if m.rsplit(".", 1)[1] not in HOLDOUT_SIDE]
    return [*_modules(root, "womm.graph"), *_modules(root, "womm.cost"), "womm.cost", *evolve]


def reaches(root: Path, start: str, target: str) -> list[str] | None:
    parent: dict[str, str | None] = {start: None}
    queue = [start]
    while queue:
        mod = queue.pop()
        if mod == target:
            chain = [mod]
            while parent[chain[-1]] is not None:
                chain.append(parent[chain[-1]])
            return chain[::-1]
        for dep in _imports(root, mod) | ({target} if _names(root, mod, target) else set()):
            if dep not in parent:
                parent[dep] = mod
                queue.append(dep)
    return None


def _names(root: Path, mod: str, target: str) -> bool:
    """``_imports`` keeps only modules that exist under ``root``; the target always counts."""
    path = _module_file(root, mod)
    if path is None:
        return False
    text = path.read_text(encoding="utf-8")
    short = target.rsplit(".", 1)[1]
    return target in text or re.search(rf"from womm\.eval import .*\b{short}\b", text) is not None


def leaks(root: Path) -> dict[str, list[str]]:
    out = {}
    for start in agent_side(root):
        chain = reaches(root, start, COST_CHECK)
        if chain:
            out[start] = chain
    return out


def test_no_agent_side_module_reaches_the_cost_check():
    assert _module_file(SRC, COST_CHECK) is not None
    assert "womm.graph.cost" in agent_side(SRC) and "womm.cost.estimate" in agent_side(SRC)
    assert leaks(SRC) == {}


def test_the_cost_package_never_imports_womm_eval():
    for mod in [*_modules(SRC, "womm.cost"), "womm.cost"]:
        assert not [d for d in _imports(SRC, mod) if d.startswith("womm.eval")], mod


def test_an_added_import_in_the_estimator_is_caught(tmp_path):
    tree = tmp_path / "womm"
    shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__"))
    estimate = tree / "cost" / "estimate.py"
    estimate.write_text(estimate.read_text() + "\nimport womm.eval.cost_check  # noqa\n")
    bad = leaks(tree)
    assert bad["womm.cost.estimate"] == ["womm.cost.estimate", COST_CHECK]
    assert "womm.graph.cost" in bad  # transitively, through the estimator


def test_an_added_from_import_in_a_planner_module_is_caught(tmp_path):
    tree = tmp_path / "womm"
    shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__"))
    (tree / "evolve" / "proposers.py").write_text("from womm.eval import cost_check  # noqa\n")
    assert "womm.evolve.proposers" in leaks(tree)


# ---------------------------------------------------------------- content canaries


def test_no_prompt_file_carries_an_ia_figure():
    for path in sorted((REPO_ROOT / "prompts").rglob("*.md")):
        assert canaries_in(path.read_text(encoding="utf-8")) == [], path


@pytest.mark.parametrize("text", ["EUR 2 763 per application", "about €10,733", "1–25 FTE"])
def test_the_canary_catches_an_ia_figure_in_any_spacing(text):
    assert canaries_in(text)


def test_the_canary_ignores_other_numbers():
    assert canaries_in("Article 27(63) and EUR 1 000 to 5 000; 12 763 records") == []


def test_every_rendered_cost_batch_of_both_versions_is_canary_free():
    from womm.models.system_version import load_system_version

    sv = load_system_version(REPO_ROOT / "system_versions" / "v1.0-cost.yaml", REPO_ROOT)
    corpus = load_default_corpus()
    assert canaries_in(sv.prompt_text(sv.spec.cost)) == []
    for version in ("com2021_206", "reg2024_1689"):
        for batch in plan_sweep(sv, corpus, version).batches:
            assert canaries_in(render_batch(batch)) == [], (version, batch.batch_id)


async def test_a_full_fake_cost_run_sends_no_ia_figure_to_any_agent(fixture):
    from womm.decisions.stub import StubDecisionService
    from womm.eval.evaluators import judge_input
    from womm.eval.golden import GOLDEN_DIR, load_golden
    from womm.graph.build import run_scenario
    from womm.llm.fake import FakeBackend, fake_cost_batch
    from womm.models.run import CodeIdentity

    from ..graph.test_cost_node import SCENARIO, _fake, _script

    backend = FakeBackend(_script(fixture, [fake_cost_batch] * 5))
    result = await run_scenario(
        SCENARIO, sv=_fake("v1.0-cost.yaml", cost=True), fixture=fixture,
        backends={"fake": backend}, decisions=StubDecisionService(),
        code_identity=CodeIdentity(git_sha="t", dirty=False), run_id="run_canary",
    )  # fmt: skip
    assert {c.role_name for c in backend.calls} >= {"planner", "expert", "cost", "synthesis"}
    for call in backend.calls:
        assert canaries_in(call.system_prompt + call.user_content) == [], call.key
    # The coverage judge reads the impacts only: no cost record reaches it.
    case = load_golden(GOLDEN_DIR / "case_01_provider_compliance_costs.yaml")
    text = judge_input(case, result)
    assert result.dossier.costs.records
    assert not any(r.obligation_id in text for r in result.dossier.costs.records)
    assert "one_off" not in text and "payer_basis" not in text


@pytest.fixture(scope="module")
def fixture():
    from womm.data.fixtures import load_fixture

    return load_fixture()


# ---------------------------------------------------------------- data boundary


def test_the_planner_view_reads_database_tables_only_and_never_cost_reports():
    assert not {t for t in ALLOWED_TABLES if "cost" in t}
    for sql in QUERIES.values():
        assert "cost" not in sql.lower()
    text = (SRC / "evolve" / "planner_view.py").read_text(encoding="utf-8")
    assert "cost_check" not in text and "cost_reference" not in text


def test_cost_reports_and_the_reference_stay_out_of_agent_inputs():
    """Reports go under runs/cost_check/; the reference under evals/. No agent-side module names
    either location."""
    assert REFERENCE_PATH.is_relative_to(REPO_ROOT / "evals")
    for mod in agent_side(SRC):
        path = _module_file(SRC, mod)
        text = path.read_text(encoding="utf-8") if path else ""
        assert "cost_reference" not in text and "cost_check" not in text, mod
