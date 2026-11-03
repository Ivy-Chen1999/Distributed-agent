"""scripts/second_judge_holdout.py (plan 2026-10-26-001): acceptance, then settlement."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from unittest.mock import MagicMock

import pytest
import yaml

from womm.config import REPO_ROOT
from womm.data.ia_index import IaRecord
from womm.eval import drafting as drafting_module
from womm.eval import ia_sources
from womm.eval import second_judge as sj
from womm.eval.golden import GoldenCase
from womm.eval.holdout import HoldoutScenarioSpec, prepare_import

from .eval.draft_factory import KEYS, draft_dict, write
from .eval.test_second_judge import PricedFake
from .eval.test_tiebreak import IA, holdout_scenario
from .test_verify_golden_case_script import SPOT_SCENARIO, _tiebroken_holdout, importer

spec = importlib.util.spec_from_file_location(
    "second_judge_holdout", REPO_ROOT / "scripts/second_judge_holdout.py"
)
script = importlib.util.module_from_spec(spec)
sys.modules["second_judge_holdout"] = script
spec.loader.exec_module(script)

CASE = "case_90_widget_switching"
MODEL = "openai:gpt-6.1-sol"


def judge_by_point(drop=(), unsure=()):
    """A scripted judge: keep, unless the point names one of ``drop`` or ``unsure``."""

    def reply(system, user):
        cards = json.loads(user.split("## Items\n\n", 1)[1])
        out = []
        for c in cards:
            text = json.dumps(c["point"])
            v = ("drop" if any(d in text for d in drop)
                 else "unsure" if any(u in text for u in unsure) else "keep")  # fmt: skip
            out.append({"item_id": c["item_id"], "verdict": v, "reason": f"judged {v}"})
        return {"verdicts": out}

    return reply


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(drafting_module, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(script, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(script, "MIN_PROPOSALS", 1)
    monkeypatch.setattr(
        script.tiebreak_script,
        "holdout_scenario",
        lambda p, d, f, a: holdout_scenario(SPOT_SCENARIO),
    )
    monkeypatch.setattr(
        ia_sources, "load_cached_ia", lambda *a, **k: MagicMock(full_text=IA, rsb_text="")
    )
    calls = []

    def use(reply):
        fake = PricedFake({sj.ROLE: [reply] * 10})
        calls.append(fake)
        monkeypatch.setattr(script, "backends_for", lambda config: {"api": fake})
        return fake

    return tmp_path, use


def _record(tmp_path, accepted=True, sha=None):
    config = drafting_module.load_drafting_config()
    path = tmp_path / "acceptance.yaml"
    path.write_text(yaml.safe_dump({
        "date": "2026-11-02", "model": MODEL, "reasoning_effort": "medium", "accepted": accepted,
        "prompt_sha256": sha or sj.prompt_sha256(config),
    }))  # fmt: skip
    return path


def _settle_argv(tmp_path, record, *extra):
    return ["settle", "--drafts-dir", str(tmp_path / "cache" / "drafts"),
            "--out-dir", str(tmp_path / "cache" / "handoff"), "--record", str(record),
            "--ledger", str(tmp_path / "spend.json"), "--cache-dir", str(tmp_path / "sj"),
            "--today", "2026-11-02", *extra]  # fmt: skip


def _write_settled(tmp_path):
    path = tmp_path / "cache" / "drafts" / f"{CASE}.yaml"
    write(path, _tiebroken_holdout())
    return path


# ----------------------------------------------------------------------------- settle


def test_settle_keeps_only_what_both_families_keep_and_the_importer_accepts_it(env, monkeypatch):
    """Covers AE1 and AE2."""
    tmp_path, use = env
    draft_path = _write_settled(tmp_path)
    fake = use(judge_by_point(drop=["Impact 7"], unsure=["Impact 8"]))
    assert script.main(_settle_argv(tmp_path, _record(tmp_path))) == 0
    path = tmp_path / "cache" / "handoff" / f"{CASE}.yaml"
    handoff = yaml.safe_load(path.read_text())
    assert handoff["verified_by"] == f"two-family agreement: claude tie-break chain + {MODEL}"
    record = handoff["second_judge"]
    assert record["model"] == MODEL and record["excluded_drop"] == 1
    assert record["excluded_unsure"] == 1 and not record["under_floor"]
    case = GoldenCase.model_validate(handoff["case"])
    kept = {e.expected_id: e.provenance for e in case.expected_impacts}
    assert "c90_e07" not in kept and "c90_e08" not in kept  # the second judge's drop and doubt
    assert "c90_m01" not in kept  # the tie-break dropped it; the judge's keep cannot restore it
    assert kept["c90_e05"] == "llm_tiebroken" and kept["c90_e01"] == "llm_judged"
    assert "No human review" in case.notes and "Human review by" not in case.notes
    assert "Excluded by the second judge: drop ['c90_e07'], unsure ['c90_e08']" in case.notes
    assert fake.calls and all("c90_" not in c.user_content for c in fake.calls)

    monkeypatch.setattr(importer, "EVALS_DIR", REPO_ROOT / "evals")
    spec_ = HoldoutScenarioSpec(scenario_id=SPOT_SCENARIO, description="Sealed switching",
                                articles=[k.rsplit("/", 1)[1] for k in KEYS])  # fmt: skip
    ia = IaRecord(celex="52099PC0009", ia_celex="52099SC0009", ia_date="2026-01-15")
    golden = tmp_path / "golden"
    golden.mkdir()
    bundle = prepare_import(importer.read_handoff(path), {"data_act": [spec_]},
                            {"data_act": ia}, golden_dir=golden)  # fmt: skip
    assert bundle.case == case and bundle.verified_by.startswith("two-family agreement")
    assert draft_path.exists()


def test_a_draft_under_the_floor_is_not_handed_off():
    """Covers AE3."""
    draft = drafting_module.GoldenDraft.model_validate(_tiebroken_holdout(extra_impacts=0))
    kept = [i.item_id for i in draft.expected_impacts if i.review.decision != "llm_dropped"]
    verdicts = {
        i.item_id: sj.SecondJudgeVerdict(
            item_id=i.item_id, verdict="keep" if i.item_id in kept[:2] else "drop", reason="r"
        )
        for i in draft.items()
    }
    case, counts = script.settle_draft(draft, verdicts, f"second-judge:{MODEL}", dt.date.today())
    assert case is None and counts["under_floor"]


def test_too_few_cases_write_no_handoff(env, monkeypatch):
    tmp_path, use = env
    _write_settled(tmp_path)
    use(judge_by_point())
    monkeypatch.setattr(script, "MIN_PROPOSALS", 2)
    assert script.main(_settle_argv(tmp_path, _record(tmp_path))) == 1
    assert not (tmp_path / "cache" / "handoff").exists()


@pytest.mark.parametrize(
    ("record", "message"),
    [
        (None, "no acceptance record"),
        ({"accepted": True, "sha": "0" * 64}, "another model, reasoning effort or prompt"),
        ({"accepted": False}, "did not pass"),
    ],
)
def test_settle_is_refused_before_any_call_without_a_passed_acceptance(env, capsys, record,
                                                                       message):  # fmt: skip
    tmp_path, use = env
    _write_settled(tmp_path)
    fake = use(judge_by_point())
    path = tmp_path / "missing.yaml" if record is None else _record(tmp_path, **record)
    assert script.main(_settle_argv(tmp_path, path)) == 1
    assert message in capsys.readouterr().err and fake.calls == []


def test_an_untiebroken_draft_is_refused(env, capsys):
    tmp_path, use = env
    data = draft_dict("holdout") | {"scenario_id": SPOT_SCENARIO}
    write(tmp_path / "cache" / "drafts" / f"{CASE}.yaml", data)
    fake = use(judge_by_point())
    assert script.main(_settle_argv(tmp_path, _record(tmp_path))) == 1
    assert "not tie-broken" in capsys.readouterr().err and fake.calls == []


def test_a_gate_problem_is_found_before_any_call(env, capsys):
    tmp_path, use = env
    data = _tiebroken_holdout()
    data["expected_impacts"][0]["provision_keys"] = ["data_act/proposal/art/999"]
    write(tmp_path / "cache" / "drafts" / f"{CASE}.yaml", data)
    fake = use(judge_by_point())
    assert script.main(_settle_argv(tmp_path, _record(tmp_path))) == 1
    assert "art/999" in capsys.readouterr().err and fake.calls == []


def test_dry_run_makes_no_call(env, capsys):
    tmp_path, use = env
    _write_settled(tmp_path)
    fake = use(judge_by_point())
    assert script.main(_settle_argv(tmp_path, _record(tmp_path), "--dry-run")) == 0
    assert "no call made" in capsys.readouterr().out and fake.calls == []


# ----------------------------------------------------------------------------- acceptance


def _acceptance_items(cases):
    """Items for the real regression ids, the label hidden in the point for the scripted judge."""
    w = [sj.JudgeItem(c["id"], "effect", {"impact": c["expected"].upper()}, "official report",
                      "", ("t",), ()) for c in cases]  # fmt: skip
    c = [i for i, case in zip(w, cases, strict=True) if case["expected"] == "supported"]
    return w, c, []


def _acceptance_argv(tmp_path):
    return ["acceptance", "--record", str(tmp_path / "acceptance.yaml"),
            "--ledger", str(tmp_path / "spend.json"), "--cache-dir", str(tmp_path / "sj"),
            "--today", "2026-11-02"]  # fmt: skip


def test_acceptance_passes_a_judge_that_keeps_only_supported_points(env, monkeypatch):
    tmp_path, use = env
    monkeypatch.setattr(sj, "acceptance_items", lambda cases, *_: _acceptance_items(cases))
    use(judge_by_point(drop=["NOT_SUPPORTED"]))
    assert script.main(_acceptance_argv(tmp_path)) == 0
    record = yaml.safe_load((tmp_path / "acceptance.yaml").read_text())
    config = drafting_module.load_drafting_config()
    assert record["accepted"] and record["prompt_sha256"] == sj.prompt_sha256(config)
    assert len(record["passes"]) == 2 and record["model"] == MODEL
    assert all(p["gate_b"]["kept"] == 15 for p in record["passes"])


def test_acceptance_fails_a_judge_that_keeps_everything(env, monkeypatch):
    tmp_path, use = env
    monkeypatch.setattr(sj, "acceptance_items", lambda cases, *_: _acceptance_items(cases))
    use(judge_by_point())
    assert script.main(_acceptance_argv(tmp_path)) == 1
    record = yaml.safe_load((tmp_path / "acceptance.yaml").read_text())
    assert not record["accepted"] and record["passes"][0]["gate_a"]["kept_unsupported"]
