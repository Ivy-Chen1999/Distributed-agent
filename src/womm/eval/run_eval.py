"""Run golden cases through the pipeline and score them (F2, R11-R13, R34).

Local mode writes a JSON report only. With LangSmith configured, the same run is recorded as an
experiment (dataset synced from evals/golden) tagged with the system version and backends.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from womm.data.fixtures import Fixture
from womm.decisions.service import DecisionService
from womm.eval.evaluators import CaseScore, aggregate, failure_records, noise, score_case
from womm.eval.golden import GoldenCase, check_against_fixture
from womm.graph.build import run_scenario
from womm.llm.base import LLMBackend
from womm.models.run import CodeIdentity, RunResult
from womm.models.system_version import SystemVersion

DATASET_NAME = "womm-golden-v0"


class BaselineRefused(RuntimeError):
    pass


@dataclass
class EvalReport:
    system_version: str
    metadata: dict[str, Any]
    scores: list[CaseScore] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    aborted: str | None = None

    @property
    def summary(self) -> dict | None:
        """None when the experiment was aborted (e.g. rate limit): no polluted aggregates."""
        return None if self.aborted else aggregate(self.scores)

    def to_json(self) -> str:
        return json.dumps(
            {
                "system_version": self.system_version,
                "metadata": self.metadata,
                "aborted": self.aborted,
                "summary": self.summary,
                "noise": noise(self.scores) if self.metadata.get("repetitions", 1) > 1 else None,
                "failures": failure_records(self.scores),
                "run_ids": self.run_ids,
                "scores": [s.model_dump(mode="json") for s in self.scores],
            },
            indent=2,
            ensure_ascii=False,
        )


def experiment_metadata(
    sv: SystemVersion, code: CodeIdentity, baseline: bool, repetitions: int
) -> dict[str, Any]:
    roles = sv.spec.roles()
    backends = sorted({r.backend for r in roles.values()})
    meta: dict[str, Any] = {
        "system_version": sv.version_id,
        "system_version_file": sv.source_path,
        "roles": {name: f"{r.backend}:{r.model}" for name, r in roles.items()},
        "backends": backends,
        "router": f"{sv.spec.router.mode}/{sv.spec.router.decider}",
        "git_sha": code.git_sha,
        "git_dirty": code.dirty,
        "claude_cli_version": code.claude_cli_version,
        "repetitions": repetitions,
    }
    if baseline:
        if code.dirty:
            raise BaselineRefused(
                "refusing to tag a baseline from a dirty working tree; commit your changes first"
            )
        meta["baseline_kind"] = "smoke" if "claude_code" in backends else "reference"
    return meta


async def evaluate_cases(
    cases: list[GoldenCase],
    *,
    sv: SystemVersion,
    fixture: Fixture,
    backends: dict[str, LLMBackend],
    decisions: DecisionService,
    code: CodeIdentity,
    judge_prompt: str,
    repetitions: int = 1,
    baseline: bool = False,
    runs_dir: Path | None = None,
) -> EvalReport:
    for case in cases:
        check_against_fixture(case, fixture)
    report = EvalReport(
        system_version=sv.version_id,
        metadata=experiment_metadata(sv, code, baseline, repetitions),
    )
    judge_role = sv.spec.judge
    judge_backend = backends[judge_role.backend]

    for case in cases:
        for rep in range(repetitions):
            run = await run_scenario(
                case.scenario_id, sv=sv, fixture=fixture, backends=backends,
                decisions=decisions, code_identity=code, tags=["eval", case.case_id],
            )  # fmt: skip
            report.run_ids.append(run.run_id)
            if runs_dir:
                _save_run(runs_dir, run)
            score, _ = await score_case(case, run, judge_backend, judge_role, judge_prompt)
            report.scores.append(score)
            if _hit_rate_limit(run, score):
                report.aborted = f"rate_limit during {case.case_id} repetition {rep + 1}"
                return report
    return report


def _hit_rate_limit(run: RunResult, score: CaseScore) -> bool:
    texts = [run.error or "", run.synthesis_error or "", score.error or "", score.judge_error or ""]
    return any(f.error_kind == "rate_limit" for f in run.failures) or any(
        "[rate_limit]" in t for t in texts
    )


def _save_run(runs_dir: Path, run: RunResult) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run.run_id}.json").write_text(run.model_dump_json(indent=2))


async def persist_failures(report: EvalReport, database_url: str) -> int:
    """Write R14b failure records to Postgres; returns how many were written."""
    from womm.api.db import Database

    records = failure_records(report.scores)
    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        for r in records:
            await db.record_failure(
                system_version=report.system_version,
                git_sha=report.metadata.get("git_sha"),
                **r,
            )
    finally:
        await db.close()
    return len(records)


def write_report(report: EvalReport, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = runs_dir / f"eval_{stamp}_{report.system_version}.json"
    path.write_text(report.to_json())
    return path


# ---------------------------------------------------------------- LangSmith recording


def sync_dataset(client: Any, cases: list[GoldenCase], name: str = DATASET_NAME) -> Any:
    """Idempotent upsert of golden cases as dataset examples, keyed by metadata.case_id."""
    if client.has_dataset(dataset_name=name):
        dataset = client.read_dataset(dataset_name=name)
    else:
        dataset = client.create_dataset(
            dataset_name=name, description="WOMM golden cases (evals/golden)"
        )
    existing = {
        (ex.metadata or {}).get("case_id"): ex for ex in client.list_examples(dataset_id=dataset.id)
    }
    for case in cases:
        inputs = {"case_id": case.case_id, "scenario_id": case.scenario_id}
        outputs = case.model_dump(mode="json")
        meta = {"case_id": case.case_id}
        ex = existing.get(case.case_id)
        if ex is None:
            client.create_example(
                inputs=inputs, outputs=outputs, metadata=meta, dataset_id=dataset.id
            )
        elif ex.inputs != inputs or ex.outputs != outputs:
            client.update_example(ex.id, inputs=inputs, outputs=outputs, metadata=meta)
    return dataset


async def record_langsmith_experiment(
    report: EvalReport, cases: list[GoldenCase], client: Any, prefix: str
) -> str:
    """Record an already computed report as a LangSmith experiment (no re-running)."""
    from langsmith import aevaluate

    dataset = sync_dataset(client, cases)
    by_case: dict[str, list[CaseScore]] = {}
    for s in report.scores:
        by_case.setdefault(s.case_id, []).append(s)
    served: dict[str, int] = {}

    async def target(inputs: dict) -> dict:
        cid = inputs["case_id"]
        i = served.get(cid, 0)
        served[cid] = i + 1
        scores = by_case.get(cid, [])
        if i >= len(scores):
            raise RuntimeError(f"no computed score for {cid} (aborted or skipped)")
        return scores[i].model_dump(mode="json")

    def metrics(outputs: dict) -> dict:
        keys = ("coverage", "omissions_addressed", "grounding", "latency_s", "cost_usd")
        results = [{"key": k, "score": outputs.get(k)} for k in keys]
        # LangSmith feedback scores are capped at +/-99999.9999, so tokens go in as thousands.
        tokens = outputs.get("tokens")
        results.append({"key": "ktokens", "score": None if tokens is None else tokens / 1000})
        return {"results": results}

    def summary(outputs: list[dict]) -> dict:
        agg = aggregate([CaseScore.model_validate(o) for o in outputs if o])
        return {
            "results": [
                {"key": f"mean_{k}", "score": agg[k]}
                for k in ("coverage", "omissions_addressed", "grounding")
                if agg[k] is not None
            ]
        }

    # Only the cases this report actually scored: a --case subset, or golden cases deleted
    # locally but still in the dataset, must not show up as errored rows.
    examples = [
        ex
        for ex in client.list_examples(dataset_id=dataset.id)
        if (ex.metadata or {}).get("case_id") in by_case
    ]
    reps = report.metadata.get("repetitions", 1)
    results = await aevaluate(
        target,
        data=examples,
        evaluators=[metrics],
        summary_evaluators=[summary],
        experiment_prefix=prefix,
        metadata={
            **report.metadata,
            "partial": report.aborted is not None
            or bool(report.summary and report.summary["partial"]),
            "aborted": report.aborted,
        },
        num_repetitions=reps,
        max_concurrency=1,
        client=client,
    )
    return results.experiment_name
