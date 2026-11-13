"""Publishing a train/val eval report to the console (``womm eval publish``).

The CLI turns a ``runs/eval_*.json`` report into a compact payload: per run, the counts behind
coverage and omissions (no judge text, no case content) and the run's split. The golden cases
live only in the repository, so the CLI supplies the totals a quality failure is scored against
(a failed run covers 0 of the case's reference impacts). The server re-checks every split and
summarises the payload with ``summarize``: counts pooled over runs, and a 95% range from a
case-level bootstrap, because a case's impacts are scored together and are not independent.

Holdout never leaves the sealed path: ``read_report`` refuses a holdout report, ``payload``
refuses any case outside train/val, and the server refuses the whole payload again.
"""

from __future__ import annotations

import os
import random
import re
from collections.abc import Mapping
from typing import Any

import httpx

from womm.eval.golden import HOLDOUT_REFUSAL, GoldenCase, GoldenError

PUBLISHABLE_SPLITS = ("train", "val")
TOKEN_ENV = "WOMM_API_TOKEN"
URL_ENV = "WOMM_API_URL"
BOOTSTRAP_ROUNDS = 2000
BOOTSTRAP_SEED = 20261113
_LS_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class PublishError(ValueError):
    pass


def _run_counts(score: Mapping[str, Any], case: GoldenCase) -> dict[str, Any]:
    """The counts one scored run contributes, or ``{"excluded": reason}``."""
    judge = score.get("judge")
    base = {
        "grounding": score.get("grounding"),
        "precision": score.get("precision"),
        "run_status": score.get("run_status"),
    }
    if score.get("outcome") != "scored":
        return {"excluded": "errored"}
    if score.get("judge_error"):
        return {"excluded": "judge_error"}
    if judge is not None:
        expected = judge.get("expected") or []
        omissions = judge.get("omissions") or []
        return {
            **base,
            "covered": sum(1 for e in expected if e.get("covered")),
            "expected": len(expected),
            "addressed": sum(1 for o in omissions if o.get("addressed")),
            "omissions": len(omissions),
        }
    if score.get("coverage") == 0.0:
        # A quality failure (failed run, no dossier or no impacts): 0 of everything.
        return {
            **base,
            "covered": 0,
            "expected": len(case.expected_impacts),
            "addressed": 0,
            "omissions": len(case.important_omissions),
        }
    return {"excluded": "no_verdict"}


def payload(
    report: Mapping[str, Any],
    cases: Mapping[str, GoldenCase],
    *,
    experiment_ids: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """The compact payload of a report. Refuses an aborted report and any case that is not a
    train/val golden case."""
    if report.get("aborted"):
        raise PublishError(f"the report was aborted ({report['aborted']}); nothing to publish")
    meta = report.get("metadata") or {}
    runs: list[dict[str, Any]] = []
    repetition: dict[str, int] = {}
    for score in report.get("scores") or []:
        case = cases.get(score["case_id"])
        if case is None:
            raise PublishError(f"case {score['case_id']!r} is not a golden case of this repo")
        if case.split not in PUBLISHABLE_SPLITS:
            raise GoldenError(f"{case.case_id} is a {case.split} case; {HOLDOUT_REFUSAL}")
        repetition[case.case_id] = repetition.get(case.case_id, 0) + 1
        run = {"case_id": case.case_id, "split": case.split, "repetition": repetition[case.case_id]}
        runs.append({**run, **_run_counts(score, case)})
    if not runs:
        raise PublishError("the report has no scored runs")
    judges = sorted(
        {r["judge_version"] for r in report.get("case_runs", []) if r.get("judge_version")}
    )
    out: dict[str, Any] = {
        "system_version": report["system_version"],
        "git_sha": meta.get("git_sha"),
        "judge_version": judges[0] if len(judges) == 1 else (",".join(judges) or None),
        "repetitions": int(meta.get("repetitions") or 1),
        "experiment_name": meta.get("langsmith_experiment"),
        "experiment_tenant_id": None,
        "experiment_project_id": None,
        "runs": runs,
    }
    if experiment_ids is not None:
        out["experiment_tenant_id"], out["experiment_project_id"] = experiment_ids
    return out


def check_payload(data: Mapping[str, Any]) -> None:
    """Server-side checks; any failure refuses the whole payload."""
    runs = data.get("runs")
    if not isinstance(runs, list) or not runs:
        raise PublishError("a report needs at least one run")
    bad = sorted({str(r.get("split")) for r in runs} - set(PUBLISHABLE_SPLITS))
    if bad:
        raise PublishError(f"only train/val reports can be published; got split(s) {bad}")
    for key in ("experiment_tenant_id", "experiment_project_id"):
        value = data.get(key)
        if value is not None and not _LS_ID.match(str(value)):
            raise PublishError(f"{key} must be a LangSmith id")
    if not str(data.get("system_version", "")).startswith("sv_"):
        raise PublishError("system_version must be a version id (sv_…)")


def _ratio(runs: list[dict], num: str, den: str) -> float | None:
    total = sum(r[den] for r in runs)
    return sum(r[num] for r in runs) / total if total else None


def _mean(runs: list[dict], key: str) -> float | None:
    vals = [r[key] for r in runs if r.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def _bootstrap(by_case: dict[str, list[dict]], stat) -> tuple[float, float] | None:
    ids = sorted(by_case)
    if len(ids) < 2:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    values = []
    for _ in range(BOOTSTRAP_ROUNDS):
        sample = [r for _ in ids for r in by_case[rng.choice(ids)]]
        v = stat(sample)
        if v is not None:
            values.append(v)
    if not values:
        return None
    values.sort()
    return values[int(0.025 * (len(values) - 1))], values[int(0.975 * (len(values) - 1))]


def summarize(data: Mapping[str, Any]) -> dict[str, Any]:
    """Counts and ranges over the runs that carry a verdict; excluded runs are counted apart."""
    runs = [r for r in data["runs"] if "excluded" not in r]
    excluded = [r for r in data["runs"] if "excluded" in r]
    by_case: dict[str, list[dict]] = {}
    for r in runs:
        by_case.setdefault(r["case_id"], []).append(r)

    def metric(stat, **counts) -> dict[str, Any]:
        return {"value": stat(runs), "range": _bootstrap(by_case, stat), **counts}

    splits: dict[str, int] = {}
    for case_id in {r["case_id"] for r in data["runs"]}:
        split = next(r["split"] for r in data["runs"] if r["case_id"] == case_id)
        splits[split] = splits.get(split, 0) + 1
    return {
        "cases": len({r["case_id"] for r in data["runs"]}),
        "splits": dict(sorted(splits.items())),
        "runs": len(runs),
        "excluded_runs": len(excluded),
        "reference_impacts": metric(
            lambda rs: _ratio(rs, "covered", "expected"),
            found=sum(r["covered"] for r in runs),
            total=sum(r["expected"] for r in runs),
        ),  # fmt: skip
        "omissions_addressed": metric(
            lambda rs: _ratio(rs, "addressed", "omissions"),
            found=sum(r["addressed"] for r in runs),
            total=sum(r["omissions"] for r in runs),
        ),  # fmt: skip
        "grounding": metric(lambda rs: _mean(rs, "grounding")),
        "precision": metric(lambda rs: _mean(rs, "precision")),
    }


def resolve_experiment(name: str | None) -> tuple[str, str] | None:
    """The tenant and project ids of a LangSmith experiment, when it can be found."""
    if not name:
        return None
    try:
        from langsmith import Client

        project = Client().read_project(project_name=name)
    except Exception:  # noqa: BLE001 - the report publishes without a link
        return None
    return str(project.tenant_id), str(project.id)


def post(
    data: Mapping[str, Any], url: str | None = None, *, client: httpx.Client | None = None
) -> dict:
    url = (url or os.environ.get(URL_ENV, "")).rstrip("/")
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not url:
        raise PublishError(f"set {URL_ENV} (the console's base URL) or pass --url")
    if not token:
        raise PublishError(f"set {TOKEN_ENV} (the console's token)")
    client = client or httpx.Client(timeout=60)
    r = client.post(f"{url}/eval-reports", json=dict(data),
                    headers={"Authorization": f"Bearer {token}"})  # fmt: skip
    if r.status_code >= 400:
        raise PublishError(f"publish failed: HTTP {r.status_code}: {r.text[:300]}")
    return r.json()
