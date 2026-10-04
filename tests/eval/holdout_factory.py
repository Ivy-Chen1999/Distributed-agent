"""Synthetic holdout material for the U6 tests: two proposal fixtures (copies of the AI Act
fixture under other regulation ids), local scenario definitions and verified handoffs."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import yaml

from womm.data import fixtures as fixtures_module
from womm.data.ia_index import IaRecord
from womm.eval.golden import load_all_golden
from womm.eval.holdout import HANDOFF_FORMAT, HoldoutScenarioSpec
from womm.llm.fake import FakeBackend

from ..graph.conftest import good_script

PROPOSALS = ("prop_alpha", "prop_beta")
SCENARIO_ID = "eval_sealed_sme"
ARTICLES = ["53", "54", "55", "71"]
SECRET_IMPACT = "SEALED-EXPECTED-IMPACT-TEXT"
_BASE = next(c for c in load_all_golden() if c.scenario_id == "eval_sme_impacts")


def case_id(fixture: str) -> str:
    return f"case_9{PROPOSALS.index(fixture)}_sealed_{fixture}"


def make_fixtures_root(tmp_path: Path) -> Path:
    """``ai_act`` plus one copy per holdout proposal, registered under its own id."""
    root = tmp_path / "fixtures"
    shutil.copytree(fixtures_module.DEFAULT_FIXTURE_DIR, root / "ai_act")
    for name in PROPOSALS:
        shutil.copytree(fixtures_module.DEFAULT_FIXTURE_DIR, root / name)
        for version_file in fixtures_module.VERSION_FILES:
            path = root / name / version_file
            data = json.loads(path.read_text())
            data["regulation_id"] = name
            path.write_text(json.dumps(data))
        # Public proposal fixtures carry no evaluation scenario for the holdout.
        (root / name / "scenarios.yaml").write_text("scenarios: []\n")
    return root


def scenario_specs() -> dict[str, list[HoldoutScenarioSpec]]:
    spec = HoldoutScenarioSpec(
        scenario_id=SCENARIO_ID,
        description="Sandboxes, SME measures and penalties (local holdout scenario)",
        articles=ARTICLES,
        after_version="com2021_206",
    )
    return {name: [spec] for name in PROPOSALS}


def write_scenarios(private_dir: Path) -> Path:
    path = private_dir / "holdout_scenarios.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: [s.model_dump(exclude_none=True) for s in v] for k, v in scenario_specs().items()}
    path.write_text(yaml.safe_dump(data))
    return path


def ia_index() -> dict[str, IaRecord]:
    return {
        # Synthetic identifiers (year 2099): no real IA or RSB reference in tracked files.
        name: IaRecord(celex=f"52099PC000{n}", ia_celex=f"52099SC000{n}", ia_date="2026-01-15",
                       rsb_ref=f"SEC(2099) {n}")
        for n, name in enumerate(PROPOSALS, start=1)
    }  # fmt: skip


def write_ia_index(private_dir: Path) -> Path:
    path = private_dir / "ia_index.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({k: v.model_dump(mode="json") for k, v in ia_index().items()}))
    return path


def case_dict(fixture: str) -> dict:
    data = _BASE.model_dump(mode="json", exclude_none=True)
    cid = case_id(fixture)
    data.update(case_id=cid, fixture=fixture, split="holdout", scenario_id=SCENARIO_ID,
                ia_reference=f"SWD(2099) {PROPOSALS.index(fixture) + 1} sealed")  # fmt: skip
    for n, item in enumerate(data["expected_impacts"], 1):
        item["expected_id"] = f"{cid}_e{n:02d}"
    data["expected_impacts"][0]["impact"] = f"{SECRET_IMPACT} {fixture}"
    for n, item in enumerate(data["important_omissions"], 1):
        item["omission_id"] = f"{cid}_o{n:02d}"
    return data


def handoff(fixture: str, draft_sha: str | None = None) -> dict:
    return {
        "format": HANDOFF_FORMAT,
        "consumer": "scripts/import_holdout_case.py",
        "verified_by": "jdoe",
        "verified_on": "2026-10-21",
        "draft_sha256": draft_sha,
        "case": case_dict(fixture),
    }


# ----------------------------------------------------------------------------- compare backends


def _synth(label: str):
    def synth(_system, user):
        rows = json.loads(user.split("Validated findings:\n", 1)[1])
        return {
            "impacts": [{"impact_id": "I1", "summary": label,
                         "finding_ids": [r["finding_id"] for r in rows]}],
            "chains": [], "disagreements": [], "open_questions": [], "discarded": [],
        }  # fmt: skip

    return synth


def _judge(_system, user):
    """Covers every expected impact of a CANDIDATE dossier, only the first of a BASELINE one."""
    expected = json.loads(user.split("Expected impacts:\n", 1)[1].split("\n\nImportant", 1)[0])
    omissions = json.loads(user.split("Important omissions:\n", 1)[1])
    full = "CANDIDATE" in user.split("Expected impacts:", 1)[0]
    return {
        "expected": [{"expected_id": e["expected_id"], "covered": full or i == 0,
                      "impact_id": "I1", "justification": "j"} for i, e in enumerate(expected)],
        "omissions": [{"omission_id": o["omission_id"], "addressed": full,
                       "impact_id": None, "justification": "j"} for o in omissions],
    }  # fmt: skip


def pipeline_script(label: str, runs: int) -> dict:
    s = good_script()
    for key in ("planner", "expert/legal", "expert/fiscal", "expert/stakeholder"):
        s[key] = s[key] * runs
    s["synthesis"] = [_synth(label)] * runs
    return s


def compare_backends(runs_per_version: int) -> dict[str, dict[str, FakeBackend]]:
    """Per-version fake backends; the baseline's backend also serves the (shared) judge."""
    base = pipeline_script("BASELINE", runs_per_version)
    base["judge"] = [_judge] * (2 * runs_per_version)
    return {
        "candidate": {"fake": FakeBackend(pipeline_script("CANDIDATE", runs_per_version))},
        "baseline": {"fake": FakeBackend(base)},
    }
