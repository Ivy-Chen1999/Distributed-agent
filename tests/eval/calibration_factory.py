"""Synthetic eval reports and run files for the calibration and noise-report tests (no LLM)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from womm.eval.golden import GoldenCase, load_all_golden

JV = "jv_test0000001"
SV = "sv_test0000001"


def golden(n_cases: int = 4, split: str = "train") -> dict[str, GoldenCase]:
    """Synthetic public cases built from the real ones (copies under new ids)."""
    real = load_all_golden()
    out = {}
    for i in range(n_cases):
        base = real[i % len(real)]
        case = base.model_copy(update={"case_id": f"case_9{i}_synthetic", "split": split})
        out[case.case_id] = case
    return out


def make_report(
    cases: dict[str, GoldenCase],
    *,
    reps: int = 2,
    covered: Callable[[str, str, int], bool] = lambda c, e, r: True,
    metric: Callable[[str, str, int], float | None] | None = None,
    jv: str | None = JV,
    sv: str = SV,
    formal: bool = False,
    backends: tuple[str, ...] = ("fake",),
    split: str | None = None,
    runs_dir: Path | None = None,
) -> dict:
    scores, case_runs = [], []
    for case in cases.values():
        for rep in range(1, reps + 1):
            run_id = f"run_{case.case_id}_{rep}"
            verdicts = [
                {"expected_id": e.expected_id, "covered": covered(case.case_id, e.expected_id, rep),
                 "impact_id": "I1", "justification": "SECRET_JUSTIFICATION"}
                for e in case.expected_impacts
            ]  # fmt: skip
            values = {
                m: (metric(case.case_id, m, rep) if metric
                    else sum(v["covered"] for v in verdicts) / len(verdicts))
                for m in ("coverage", "omissions_addressed", "grounding")
            }  # fmt: skip
            scores.append({"case_id": case.case_id, "scenario_id": case.scenario_id,
                           "run_id": run_id, "outcome": "scored", **values,
                           "judge": {"expected": verdicts, "omissions": []},
                           "judge_error": None})  # fmt: skip
            case_runs.append({"case_id": case.case_id, "fixture": case.fixture,
                              "split": case.split, "run_id": run_id, "repetition": rep,
                              "system_version": sv, "judge_version": jv})  # fmt: skip
            if runs_dir is not None:
                runs_dir.mkdir(parents=True, exist_ok=True)
                run = {"run_id": run_id, "scenario_id": case.scenario_id, "dossier": {"impacts": [
                    {"impact_id": "I1", "summary": f"Dossier summary for {case.case_id}",
                     "findings": [{"affected_actor": "SMEs", "mechanism": "conformity costs",
                                   "impact": "higher compliance costs"}]}]}}  # fmt: skip
                (runs_dir / f"{run_id}.json").write_text(json.dumps(run))
    splits = sorted({c.split for c in cases.values()})
    meta = {
        "system_version": sv,
        "backends": list(backends),
        "repetitions": reps,
        "git_dirty": False,
        "split": split or (splits[0] if len(splits) == 1 else "mixed"),
        "splits": splits,
        "langsmith_experiment": "womm-x-val-formal-1234" if formal else None,
    }
    if formal:
        meta |= {"formal": True, "run_kind": "r34_noise"}
    return {
        "system_version": sv,
        "metadata": meta,
        "aborted": None,
        "scores": scores,
        "case_runs": case_runs if jv else [],
        "failure_events": [],
    }


def write_report(path: Path, report: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report))
    return path
