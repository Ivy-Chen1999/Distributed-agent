"""IA isolation for the cost path (EU cost plan U8; parent R11, R24).

The impact assessment, the IA cost reference and cost scores never reach any agent: no graph,
cost or Planner-side module imports the cost check or reads under evals/ (two allow-listed
Planner-side config reads aside), no prompt, rendered cost batch or fake-run LLM input carries
an IA figure or identifier (canaries derived from the reference file), and the coverage judge
never sees the cost section.
"""

import ast
import re
import shutil
from pathlib import Path

import pytest
import yaml

import womm
from womm.config import REPO_ROOT
from womm.cost.categories import BAND_EDGES_EUR
from womm.cost.estimate import render_batch
from womm.cost.sweep import plan_sweep
from womm.data.corpus import load_default_corpus
from womm.eval.cost_check import REFERENCE_PATH
from womm.evolve.planner_view import ALLOWED_TABLES, QUERIES

from ..evolve.test_planner_boundary import HOLDOUT_SIDE, _imports, _module_file

SRC = Path(womm.__file__).parent
COST_CHECK = "womm.eval.cost_check"

# Canaries are derived from the IA cost reference itself (EU cost plan, IA figure audit), so a
# new item or figure there is a new canary here: every eur_* / fte_* value and range in common
# renderings, the figure_text clauses, the numbers in figure_text / inconsistency / notes, the
# IA's identifiers and its section and table labels. None may appear in any agent input.
# The band edges (EUR 1 000, 5 000, 25 000) are the system's own ordinal scale, stated in the
# cost prompt; a round IA figure equal to an edge is a canary in its ranges and clauses only.
BAND_EDGES = {float(e) for edges in BAND_EDGES_EUR.values() for e in edges if e}
SEP = r"[\s,.'\u00a0\u202f]?"  # thousands separators: space, comma, dot, apostrophe, nbsp
DASH = r"\s*(?:-|–|—|to)\s*"
CURRENCY_BEFORE = r"(?:EUR|€|euros?)\s*"
IA_LABELS = {
    "SWD(2021) 84": r"SWD\s*\(\s*2021\s*\)\s*0*84\b",
    "Standard Cost Model": r"standard\s+cost\s+model",
    "IA section §6.x": r"§\s*6\.\d",
    "IA Table 5/8": r"\bTable\s+(?:5|8[ab]?)\b",
    "IA Annex 3/4": r"\bAnnex\s+[34]\b",
}


def _number(value: float) -> str:
    """A figure in any thousands rendering: 2 763, 2,763, 2.763, 2763 (and 0.1 as is)."""
    if value != int(value):
        return re.escape(f"{value:g}")
    digits = str(int(value))
    groups = []
    while digits:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    return SEP.join(groups)


def _bounded(pattern: str) -> str:
    """No digit (or separator and digit) on either side, so 12 763 or 5 000 000 do not match."""
    return rf"(?<!\d)(?<!\d[\s,.'\u00a0\u202f]){pattern}(?![\s,.'\u00a0\u202f]?\d)"


def _clauses(text: str) -> list[str]:
    parts = re.split(r"[;,()]", text)
    return [p.strip() for p in parts if len(p.split()) >= 4]


def derive_canaries(path: Path = REFERENCE_PATH) -> dict[str, str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    out: dict[str, str] = dict(IA_LABELS)
    texts = [data.get("notes") or ""]
    for item in data["items"]:
        texts += [item.get("figure_text") or "", item.get("inconsistency") or ""]
        for clause in _clauses(item.get("figure_text") or ""):
            words = r"\s+".join(re.escape(w) for w in clause.split())
            out[f"{item['item_id']} text: {clause}"] = words
        for kind, suffix in (("eur", ""), ("fte", r"\s*FTE")):
            low, high = item.get(f"{kind}_low"), item.get(f"{kind}_high")
            for value in {v for v in (low, high) if v is not None}:
                if kind == "fte":
                    out[f"{value:g} FTE"] = _bounded(_number(value)) + suffix
                elif value % 1000:
                    out[f"EUR {value:g}"] = _bounded(_number(value))
                elif value not in BAND_EDGES:  # a round figure counts with a currency mark only
                    out[f"EUR {value:g}"] = CURRENCY_BEFORE + _bounded(_number(value))
            if low is not None and high is not None and low != high:
                out[f"{low:g}-{high:g}{' FTE' if suffix else ''}"] = (
                    _bounded(_number(low)) + DASH + _bounded(_number(high)) + suffix
                )
    for text in texts:  # numbers written with separators in the reference's own words
        for raw in re.findall(r"\d{1,3}(?:[ \u00a0]\d{3})+", text):
            value = float(raw.replace(" ", "").replace("\u00a0", ""))
            if value in BAND_EDGES:
                continue
            pattern = _bounded(_number(value))
            out.setdefault(f"EUR {value:g}", pattern if value % 1000 else CURRENCY_BEFORE + pattern)
    return out


CANARIES = derive_canaries()


def canaries_in(text: str) -> list[str]:
    return [name for name, pattern in CANARIES.items() if re.search(pattern, text, re.I)]


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


def test_the_canaries_cover_every_reference_figure():
    data = yaml.safe_load(REFERENCE_PATH.read_text(encoding="utf-8"))
    for item in data["items"]:
        for field in ("eur_low", "eur_high", "fte_low", "fte_high"):
            if item.get(field) is not None:
                value = item[field]
                named = f"{value:g} FTE" in CANARIES or f"EUR {value:g}" in CANARIES
                ranged = any(k.startswith(f"{value:g}-") or f"-{value:g}" in k for k in CANARIES)
                assert named or (value in BAND_EDGES and ranged), (item["item_id"], field)
    assert {"EUR 7764", "EUR 170000", "1-25 FTE", "5000-8000"} <= set(CANARIES)


def test_no_prompt_file_carries_an_ia_figure():
    for path in sorted((REPO_ROOT / "prompts").rglob("*")):
        if path.is_file():
            assert canaries_in(path.read_text(encoding="utf-8")) == [], path


@pytest.mark.parametrize(
    "text",
    [
        "EUR 2 763 per application", "about €10,733", "1–25 FTE", "2.763", "2763 euro",
        "EUR 5,000-8,000 a year", "5 000 to 8 000", "€ 170.000", "10 FTE at EU level",
        "EUR 3 000–7 500", "see SWD (2021) 84", "the Standard Cost Model", "Table 8b",
        "Part 1 §6.1.3", "Annex 3", "EUR 7 764", "1 720 hours", "EUR 2 000",
        "aggregate EUR 100-500 million for high-risk applications",
        "relies on in-built use logs installed by the provider",
    ],
)  # fmt: skip
def test_the_canary_catches_an_ia_figure_in_any_rendering(text):
    assert canaries_in(text), text


def test_the_canary_ignores_other_numbers():
    assert canaries_in("medium: EUR 5 000 to below 25 000; negligible: below EUR 1 000") == []
    text = (
        "Article 27(63), Annex IV, Table of contents; 12 763 records; fines up to EUR 35 000 000 "
        "or 7 % of turnover; 2 years; 5 000 000; within 15 days"
    )
    assert canaries_in(text) == []


def test_every_rendered_cost_batch_of_every_version_is_canary_free():
    from womm.models.system_version import load_system_version

    sv = load_system_version(REPO_ROOT / "system_versions" / "v1.0-cost.yaml", REPO_ROOT)
    corpus = load_default_corpus()
    assert canaries_in(sv.prompt_text(sv.spec.cost)) == []
    versions = [v.version_id for v in corpus.index.versions]
    assert {"com2021_206", "reg2024_1689"} <= set(versions)
    for version in versions:
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


# ---------------------------------------------------------------- reference-file boundary

# Agent-side modules never read under evals/. The only exceptions are Planner-side modules that
# read fixed, non-IA configuration or a scoring reference of their own, each named with the one
# evals/ location it may build. Nothing agent-side may name the IA cost reference.
EVALS_ALLOWED = {
    # the proposers' fixed prompts and models (never an IA figure, never a cost score)
    "womm.evolve.proposers": {"evals", "evolution.yaml"},
    # the diff-regression reference the gate scores against (a golden case, not the IA)
    "womm.evolve.diff_regression": {"evals", "diff_regression", "demo_penalties_amended.yaml"},
}
EVALS_NAMES = ("REFERENCE_PATH", "IA_INDEX_PATH", "GOLDEN_DIR", "POLICY_PATH", "RECORDS_PATH")


def _docstrings(tree: ast.AST) -> set[int]:
    out = set()
    for node in ast.walk(tree):
        kinds = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        first = node.body[0] if isinstance(node, kinds) and node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            out.add(id(first.value))
    return out


def evals_reads(path: Path) -> list[str]:
    """String constants (docstrings aside) that build a path under evals/ or name the IA cost
    reference, and imported evaluation-file locations."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = _docstrings(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            v = node.value
            if v == "evals" or "evals/" in v or "cost_reference" in v or "swd2021" in v.lower():
                out.append(v)
        if isinstance(node, ast.ImportFrom):
            out += [a.name for a in node.names if a.name in EVALS_NAMES]
    return out


def _closure(root: Path, starts: list[str]) -> set[str]:
    seen: set[str] = set()
    queue = list(starts)
    while queue:
        mod = queue.pop()
        if mod not in seen:
            seen.add(mod)
            queue.extend(_imports(root, mod))
    return seen


def evals_violations(root: Path) -> dict[str, list[str]]:
    out = {}
    for mod in agent_side(root):
        path = _module_file(root, mod)
        found = evals_reads(path) if path else []
        allowed = EVALS_ALLOWED.get(mod, set())
        bad = [v for v in found if v not in allowed]
        if bad:
            out[mod] = bad
    # The cost path itself (graph and cost package, and everything they import) has no
    # exception at all.
    cost_path = [*_modules(root, "womm.graph"), *_modules(root, "womm.cost"), "womm.cost"]
    for mod in sorted(_closure(root, cost_path)):
        path = _module_file(root, mod)
        found = evals_reads(path) if path else []
        if found or mod.startswith("womm.eval"):
            out.setdefault(mod, []).extend(found or [mod])
    return out


def test_no_agent_side_module_reads_under_evals():
    assert set(EVALS_ALLOWED) <= set(agent_side(SRC))
    assert evals_violations(SRC) == {}


def test_only_the_scoring_side_names_the_ia_cost_reference():
    hits = []
    for path in sorted(SRC.rglob("*.py")):
        if "cost_reference" in path.read_text(encoding="utf-8"):
            hits.append(path.relative_to(SRC).as_posix())
    assert hits == ["eval/cost_check.py"]


@pytest.mark.parametrize(
    ("module", "line"),
    [
        ("cost/estimate.py", 'X = open("evals/cost_reference/ai_act_swd2021_84.yaml")'),
        ("cost/payer.py", 'from womm.config import REPO_ROOT\nX = REPO_ROOT / "evals" / "x"'),
        ("graph/cost.py", "from womm.data.ia_index import IA_INDEX_PATH  # noqa"),
        ("evolve/proposers.py", 'X = REPO_ROOT / "evals" / "cost_reference"'),
    ],
)
def test_an_added_evals_read_is_caught(tmp_path, module, line):
    tree = tmp_path / "womm"
    shutil.copytree(SRC, tree, ignore=shutil.ignore_patterns("__pycache__"))
    target = tree / module
    target.write_text(target.read_text() + "\n" + line + "\n")
    name = "womm." + module.removesuffix(".py").replace("/", ".")
    bad = evals_violations(tree)
    assert name in bad or "womm.data.ia_index" in bad, bad
