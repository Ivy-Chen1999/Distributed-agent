"""Run golden cases through the pipeline and score them (F2, R11-R13, R34).

Local mode writes a JSON report only. With LangSmith configured, the same run is recorded as an
experiment (dataset synced from evals/golden) tagged with the system version, backends and split.
Reports carry ``split`` (one split, or ``mixed``) and a summary per split.

A ``formal`` run is the R34 noise run (``womm eval --split val --repetitions 6 --formal``): it is
the only kind that refuses non-api backends, and it needs one split, at least
``FORMAL_MIN_REPETITIONS`` repetitions and a clean working tree. A ``--baseline`` on the
claude_code backend is still allowed and keeps its ``smoke`` tag.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langsmith import tracing_context
from langsmith.run_helpers import get_current_run_tree

from womm import tracing
from womm.data.fixtures import Fixture
from womm.decisions.service import DecisionService
from womm.eval.evaluators import (
    CaseScore,
    aggregate,
    calibration,
    failure_records,
    noise,
    score_case,
)
from womm.eval.golden import (
    HOLDOUT_REFUSAL,
    GoldenCase,
    GoldenError,
    check_against_fixture,
    load_case_fixture,
)
from womm.eval.trajectory import feedback_scores, trajectory_metrics, trajectory_summary
from womm.evolve.failure_memory import CaseRun, FailureEvent, case_run, failure_events
from womm.graph.build import run_scenario
from womm.llm.base import LLMBackend
from womm.models.run import CodeIdentity, RunResult
from womm.models.system_version import SystemVersion

DATASET_NAME = "womm-golden-v0"
FORMAL_MIN_REPETITIONS = 6
FORMAL_RUN_KIND = "r34_noise"


class BaselineRefused(RuntimeError):
    pass


@dataclass
class EvalReport:
    system_version: str
    metadata: dict[str, Any]
    scores: list[CaseScore] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)
    aborted: str | None = None
    case_splits: dict[str, str] = field(default_factory=dict)
    failure_events: list[FailureEvent] = field(default_factory=list)
    case_runs: list[CaseRun] = field(default_factory=list)

    @property
    def summary(self) -> dict | None:
        """None when the experiment was aborted (e.g. rate limit): no polluted aggregates."""
        return None if self.aborted else aggregate(self.scores)

    @property
    def summary_by_split(self) -> dict[str, dict] | None:
        """One summary (and, with repetitions, noise) per split; None when aborted."""
        if self.aborted:
            return None
        out: dict[str, dict] = {}
        for split in sorted(set(self.case_splits.values())):
            scores = [s for s in self.scores if self.case_splits.get(s.case_id) == split]
            entry: dict[str, Any] = {"summary": aggregate(scores)}
            if self.metadata.get("repetitions", 1) > 1:
                entry["noise"] = noise(scores)
            out[split] = entry
        return out

    def to_json(self) -> str:
        return json.dumps(
            {
                "system_version": self.system_version,
                "metadata": self.metadata,
                "aborted": self.aborted,
                "summary": self.summary,
                "summary_by_split": self.summary_by_split,
                "noise": noise(self.scores) if self.metadata.get("repetitions", 1) > 1 else None,
                "failures": failure_records(self.scores),
                "calibration": calibration(self.scores),
                "trajectory": trajectory_summary(
                    [s.trajectory for s in self.scores if s.outcome == "scored" and s.trajectory]
                ),
                "failure_events": [e.model_dump(mode="json") for e in self.failure_events],
                "case_runs": [r.model_dump(mode="json") for r in self.case_runs],
                "run_ids": self.run_ids,
                "scores": [s.model_dump(mode="json") for s in self.scores],
            },
            indent=2,
            ensure_ascii=False,
        )


def check_formal(sv: SystemVersion, repetitions: int) -> None:
    """A formal R34 noise run: every role on the api backend and enough repetitions. Only
    formal runs refuse other backends (a claude_code ``--baseline`` stays a smoke baseline)."""
    other = sorted({r.backend for r in sv.spec.roles().values()} - {"api"})
    if other:
        raise BaselineRefused(
            f"a formal R34 noise run needs every role on the api backend; this system version "
            f"uses {other}. Drop --formal for a dev noise run"
        )
    if repetitions < FORMAL_MIN_REPETITIONS:
        raise BaselineRefused(
            f"a formal R34 noise run needs at least {FORMAL_MIN_REPETITIONS} repetitions per "
            f"case, got {repetitions}"
        )


def experiment_metadata(
    sv: SystemVersion,
    code: CodeIdentity,
    baseline: bool,
    repetitions: int,
    formal: bool = False,
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
    if formal:
        check_formal(sv, repetitions)
        if code.dirty:
            raise BaselineRefused(
                "refusing a formal run from a dirty working tree; commit your changes first"
            )
        meta["formal"] = True
        meta["run_kind"] = FORMAL_RUN_KIND
    return meta


def case_split(case: GoldenCase) -> str | None:
    """The case's split (train/val/holdout)."""
    return case.split


def split_label(cases: list[GoldenCase]) -> str:
    splits = {c.split for c in cases}
    return splits.pop() if len(splits) == 1 else "mixed"


async def evaluate_cases(
    cases: list[GoldenCase],
    *,
    sv: SystemVersion,
    fixture: Fixture | None = None,
    backends: dict[str, LLMBackend],
    decisions: DecisionService,
    code: CodeIdentity,
    judge_prompt: str,
    repetitions: int = 1,
    baseline: bool = False,
    formal: bool = False,
    runs_dir: Path | None = None,
) -> EvalReport:
    """Run and score ``cases``, each against its own fixture (``case.fixture``).

    ``fixture``, when given, is used for the cases naming its regulation_id; any other fixture
    is loaded from data/fixtures/. Holdout cases are refused: they are scored only through the
    sealed holdout entry point, never traced or written to ``runs/``."""
    fixtures: dict[str, Fixture] = {}
    if fixture is not None:
        fixtures[fixture.regulation.regulation_id] = fixture
    for case in cases:
        if case.split == "holdout":
            raise GoldenError(f"{case.case_id}: {HOLDOUT_REFUSAL}")
        check_against_fixture(case, load_case_fixture(case, fixtures))
    split = split_label(cases)
    if formal and split == "mixed":
        raise BaselineRefused("a formal R34 noise run covers exactly one split; pass --split")
    report = EvalReport(
        system_version=sv.version_id,
        metadata={
            **experiment_metadata(sv, code, baseline, repetitions, formal),
            "fixtures": sorted({c.fixture for c in cases}),
            "split": split,
            "splits": sorted({c.split for c in cases}),
        },
        case_splits={c.case_id: c.split for c in cases},
    )
    judge_role = sv.spec.judge
    judge_backend = backends[judge_role.backend]

    async def one_case(case: GoldenCase, rep: int) -> tuple[RunResult, CaseScore]:
        """One repetition of one case: the pipeline run and its judge call share this trace."""
        split = case_split(case)
        run = await run_scenario(
            case.scenario_id, sv=sv, fixture=fixtures[case.fixture], backends=backends,
            decisions=decisions, code_identity=code, tags=["eval", case.case_id],
            run_mode="eval", case_id=case.case_id, split=split,
        )  # fmt: skip
        score, _ = await score_case(case, run, judge_backend, judge_role, judge_prompt)
        traj = trajectory_metrics(run, case, labeled_decisions=score.decisions, sv=sv)
        score = score.model_copy(update={"trajectory": traj, "graph_run_id": run.trace_run_id})
        rt = None if tracing.is_sealed(split) else get_current_run_tree()
        if rt is not None:
            score = score.model_copy(update={"trace_id": str(rt.trace_id), "trace_url": _url(rt)})
        if score.outcome == "scored":
            outcome = {k: getattr(score, k) for k in ("coverage", "omissions_addressed",
                                                      "grounding")}  # fmt: skip
            tracing.send_feedback(
                run.trace_run_id, {**outcome, **feedback_scores(traj)}, split=split,
                trace_id=score.trace_id,
            )  # fmt: skip
        return run, score

    async def all_cases() -> dict:
        for case in cases:
            for rep in range(repetitions):
                split = case_split(case)
                meta = tracing.run_metadata(
                    sv, scenario_id=case.scenario_id, mode="eval", code=code,
                    case_id=case.case_id, split=split,
                )  # fmt: skip
                traced_case = tracing.traced(
                    one_case, split=split, name="womm:eval_case", run_type="chain",
                    metadata={**meta, "repetition": rep + 1},
                    tags=["eval", case.case_id, f"split:{split}"],
                )  # fmt: skip
                if tracing.is_sealed(split):
                    with tracing_context(enabled=False):
                        run, score = await traced_case(case, rep)
                else:
                    run, score = await traced_case(case, rep)
                report.run_ids.append(run.run_id)
                if runs_dir:
                    _save_run(runs_dir, run)
                report.scores.append(score)
                memory = {"split": case.split, "repetition": rep + 1,
                          "system_version": sv.version_id}  # fmt: skip
                report.failure_events += failure_events(case, score, run, **memory)
                if (scored_run := case_run(case, score, **memory)) is not None:
                    report.case_runs.append(scored_run)
                if _hit_rate_limit(run, score):
                    report.aborted = f"rate_limit during {case.case_id} repetition {rep + 1}"
                    return {"aborted": report.aborted}
        return {"summary": report.summary}

    sealed = any(tracing.is_sealed(case_split(c)) for c in cases)
    traced_all = tracing.traced(
        all_cases,
        split="holdout" if sealed else None,
        name="womm:eval",
        run_type="chain",
        metadata={k: v for k, v in report.metadata.items() if not isinstance(v, dict)},
        tags=["eval", sv.version_id, f"split:{split}"],
    )
    await traced_all()
    return report


def _url(rt) -> str | None:
    try:
        return rt.get_url()
    except Exception:  # noqa: BLE001 - tracing off or no client: no link
        return None


def _hit_rate_limit(run: RunResult, score: CaseScore) -> bool:
    texts = [run.error or "", run.synthesis_error or "", score.error or "", score.judge_error or ""]
    return any(f.error_kind == "rate_limit" for f in run.failures) or any(
        "[rate_limit]" in t for t in texts
    )


def _save_run(runs_dir: Path, run: RunResult) -> None:
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run.run_id}.json").write_text(run.model_dump_json(indent=2))


async def persist_failures(report: EvalReport, database_url: str) -> int:
    """Write R14b failure records and Failure Memory events (U1) to Postgres; returns how many
    were written. A report that contains a holdout case is refused before anything is written
    (AE3)."""
    from womm.api.db import Database

    if report.metadata.get("split") == "holdout" or "holdout" in report.case_splits.values():
        raise GoldenError(f"refusing to persist failures of a holdout run; {HOLDOUT_REFUSAL}")

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
        written = await db.record_failure_events(
            report.failure_events, report.case_runs, git_sha=report.metadata.get("git_sha")
        )
    finally:
        await db.close()
    return len(records) + written


def write_report(report: EvalReport, runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = runs_dir / f"eval_{stamp}_{report.system_version}.json"
    path.write_text(report.to_json())
    return path


# ---------------------------------------------------------------- LangSmith recording


def sync_dataset(client: Any, cases: list[GoldenCase], name: str = DATASET_NAME) -> Any:
    """Idempotent upsert of golden cases as dataset examples, keyed by metadata.case_id.
    Holdout cases are refused before any LangSmith call (R23)."""
    holdout = sum(c.split == "holdout" for c in cases)
    if holdout:
        raise GoldenError(f"refusing to sync {holdout} holdout case(s); {HOLDOUT_REFUSAL}")
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
        meta = {"case_id": case.case_id, "split": case.split}
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
        traj = feedback_scores(outputs.get("trajectory") or {})
        results.extend({"key": k, "score": v} for k, v in traj.items())
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
