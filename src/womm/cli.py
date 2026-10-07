"""WOMM command line.

Data goes to stdout (text, or JSON with --json); diagnostics and errors go to stderr.
Exit codes are listed in EXIT_CODES_HELP.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from womm.backends import prepare_backends
from womm.config import REPO_ROOT, ConfigError, load_settings
from womm.data.fixtures import FixtureError, load_fixture
from womm.decisions.factory import make_decision_service
from womm.eval.golden import SELECTABLE_SPLITS, GoldenError, load_all_golden
from womm.eval.run_eval import (
    BaselineRefused,
    check_formal,
    evaluate_cases,
    persist_failures,
    record_langsmith_experiment,
    write_report,
)
from womm.graph.build import run_scenario
from womm.identity import code_identity
from womm.llm.base import LLMError
from womm.llm.claude_code import IsolationCheckFailed
from womm.models.run import RunResult, RunStatus
from womm.models.system_version import (
    SystemVersion,
    derive_system_version,
    load_system_version,
)

EXIT_OK = 0
EXIT_FAILED = 1  # run failed / eval aborted
EXIT_USAGE = 2  # bad input: unknown scenario or case, invalid config, refused baseline
EXIT_BACKEND = 3  # backend unavailable: not logged in, isolation self-check failed, ...
EXIT_DEGRADED = 4  # run finished, but some experts or synthesis failed

EXIT_CODES_HELP = """\
exit codes:
  0  success (run succeeded or had no changes; eval completed)
  1  run failed, or eval aborted (e.g. rate limit)
  2  bad input (unknown scenario/case, invalid system version, refused baseline)
  3  backend error (not logged in, isolation self-check failed)
  4  run degraded (some experts or synthesis failed)

examples:
  womm scenarios --json
  womm selfcheck --json
  womm run eval_sme_impacts
  womm eval --case case_02_sme_impacts --local --json
  womm eval --split val --local
  womm eval --baseline
  womm eval --split val --repetitions 6 --formal   # R34 noise run (api backend only)
  womm calibrate sample --sv sv_761d872bb18e --report runs/eval_X.json --n 30 --seed 1 \
      --annotator alice --annotator bob --out .cache/calibration/2026-10
  womm calibrate score --dir .cache/calibration/2026-10 --record
  womm noise report --report runs/eval_X.json --holdout-cases 8 --holdout-proposals 8 --record
  womm cost sweep --sv v1.0-cost --version com2021_206 --repetitions 3
  womm cost check --sweep runs/cost_sweeps/<sv>/com2021_206/<code>
  womm cost late-added --sweep runs/cost_sweeps/<sv>/reg2024_1689/<code>
"""

_RUN_EXIT = {
    RunStatus.succeeded: EXIT_OK,
    RunStatus.no_changes: EXIT_OK,
    RunStatus.degraded: EXIT_DEGRADED,
    RunStatus.failed: EXIT_FAILED,
}


class UsageError(Exception):
    """Bad user input: reported on stderr with EXIT_USAGE."""


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def emit(args: argparse.Namespace, data: Any, text: str) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str) if args.json else text)


def _stamp() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _load_sv(args: argparse.Namespace) -> SystemVersion:
    sv = load_system_version(Path(args.system_version), REPO_ROOT)
    decider = getattr(args, "decider", None)
    if decider and decider != sv.spec.router.decider:
        sv = derive_system_version(sv, REPO_ROOT, decider=decider)
        info(f"router decider set to {decider}: derived system version {sv.version_id}")
    return sv


def summarize(result: RunResult) -> str:
    d = result.dossier
    lines = [
        f"run {result.run_id}  scenario={result.scenario_id}  status={result.status.value}",
        f"system_version={result.system_version}  git={result.code_identity.git_sha}"
        f"{' (dirty)' if result.code_identity.dirty else ''}",
    ]
    if d:
        rate = result.grounding.rate
        lines.append(
            f"impacts={len(d.impacts)}  open_questions={len(d.open_questions)}  "
            f"disagreements={len(d.disagreements)}  unprocessed={len(d.unprocessed)}  "
            f"failed_experts={[f.agent for f in d.failed_experts]}"
        )
        lines.append(
            f"grounding (quote existence) = {result.grounding.passed}/{result.grounding.total}"
            + (f" = {rate:.0%}" if rate is not None else "")
        )
        for i in d.impacts:
            agents = ", ".join(f.agent for f in i.findings)
            lines.append(f"  {i.impact_id}: {i.summary}  [{agents}]")
        lines.extend(f"  note: {n}" for n in d.notes)
        if d.costs is not None:
            c = d.costs.coverage
            lines.append(
                f"cost records={c.relevant}  estimated={c.estimated}  not_costed={c.not_costed}  "
                f"not_estimated={c.not_estimated}  added_after_proposal={len(d.costs.late_added)}"
            )
            for h in [h for h in d.costs.hotspots if h.dimension == "provision"][:5]:
                lines.append(
                    f"  hotspot ({h.recurrence}): {h.value}  medium/high={h.medium_or_high}  "
                    f"low={h.low}"
                )
    tokens = sum(u.input_tokens + u.output_tokens for u in result.usage)
    cost = sum(u.cost_usd or 0 for u in result.usage)
    lines.append(f"llm calls={len(result.usage)}  tokens={tokens}  cost≈${cost:.3f}")
    return "\n".join(lines)


async def cmd_run(args: argparse.Namespace) -> int:
    sv = _load_sv(args)
    fixture = load_fixture()
    fixture.scenario(args.scenario)  # fail fast on an unknown scenario, before the self-check
    decisions = make_decision_service(sv, load_settings())  # config errors before LLM spend
    backends, cli_version, _ = await prepare_backends(sv, skip_self_check=args.skip_self_check)
    result = await run_scenario(
        args.scenario,
        sv=sv,
        fixture=fixture,
        backends=backends,
        decisions=decisions,
        code_identity=code_identity(cli_version),
    )
    runs_dir = Path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    out = runs_dir / f"{result.run_id}.json"
    out.write_text(result.model_dump_json(indent=2))
    data = {
        "run_id": result.run_id,
        "scenario_id": result.scenario_id,
        "status": result.status.value,
        "system_version": result.system_version,
        "result_path": str(out),
        "impacts": len(result.dossier.impacts) if result.dossier else 0,
        "grounding": result.grounding.rate,
        "error": result.error,
    }
    emit(args, data, summarize(result) + f"\nsaved {out}")
    return _RUN_EXIT[result.status]


async def cmd_eval(args: argparse.Namespace) -> int:
    sv = _load_sv(args)
    cases = load_all_golden()
    if args.case:
        unknown = sorted(set(args.case) - {c.case_id for c in cases})
        if unknown:
            raise UsageError(f"unknown golden case(s): {unknown}")
        cases = [c for c in cases if c.case_id in args.case]
    if args.formal and (args.case or not args.split):
        raise UsageError("--formal runs a whole split: pass --split and no --case")
    if args.split:
        cases = [c for c in cases if c.split == args.split]
    if not cases:
        raise UsageError(
            f"no golden cases selected (split={args.split or 'any'}, case={args.case or 'any'})"
        )
    if args.formal:
        check_formal(sv, args.repetitions)  # before any self-check or LLM spend
    settings = load_settings()
    decisions = make_decision_service(sv, settings)  # config errors before LLM spend
    backends, cli_version, _ = await prepare_backends(sv, skip_self_check=args.skip_self_check)
    report = await evaluate_cases(
        cases,
        sv=sv,
        backends=backends,
        decisions=decisions,
        code=code_identity(cli_version),
        judge_prompt=sv.prompt_text(sv.spec.judge),
        repetitions=args.repetitions,
        baseline=args.baseline,
        formal=args.formal,
        runs_dir=Path(args.runs_dir),
    )
    # The report is written first, so a failing side effect below can never lose the results.
    path = write_report(report, Path(args.runs_dir))
    if not args.local and settings.langsmith_api_key:
        try:
            from langsmith import Client

            # LangSmith prints the experiment link to stdout; stdout is reserved for data.
            with contextlib.redirect_stdout(sys.stderr):
                name = await record_langsmith_experiment(
                    report, cases, Client(), prefix=_experiment_prefix(sv, report)
                )
            report.metadata["langsmith_experiment"] = name
            info(f"LangSmith experiment: {name}")
        except Exception as exc:  # noqa: BLE001
            info(f"warning: LangSmith recording failed: {type(exc).__name__}: {exc}")
    if settings.database_url and not report.aborted:
        try:
            n = await persist_failures(report, settings.database_url)
            info(f"recorded {n} failure record(s) in Postgres")
        except Exception as exc:  # noqa: BLE001
            info(f"warning: failure records not stored: {type(exc).__name__}: {exc}")
    path.write_text(report.to_json())  # now including the experiment name, if recorded

    lines = [json.dumps(report.summary, indent=2) if report.summary else "ABORTED"]
    for s in report.scores:
        err = s.error or s.judge_error
        lines.append(
            f"  {s.case_id}: {s.outcome} coverage={s.coverage} "
            f"omissions={s.omissions_addressed} grounding={s.grounding}"
            + (f" error={err}" if err else "")
        )
    lines.append(f"report {path}")
    data = json.loads(report.to_json()) | {"report_path": str(path)}
    emit(args, data, "\n".join(lines))
    if report.aborted:
        info(f"eval aborted: {report.aborted}")
        return EXIT_FAILED
    return EXIT_OK


def _experiment_prefix(sv: SystemVersion, report: Any) -> str:
    prefix = f"womm-{sv.spec.name}-{report.metadata['split']}"
    return prefix + "-formal" if report.metadata.get("formal") else prefix


async def cmd_selfcheck(args: argparse.Namespace) -> int:
    sv = _load_sv(args)
    if not any(r.backend == "claude_code" for r in sv.spec.roles().values()):
        emit(args, {"passed": None, "note": "no claude_code backend in this version"}, "n/a")
        return EXIT_OK
    try:
        _, _, report = await prepare_backends(sv)
        exit_code = EXIT_OK
    except IsolationCheckFailed as exc:
        report, exit_code = exc.report, EXIT_BACKEND
    runs_dir = Path(args.runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / f"selfcheck_{_stamp()}.json"
    data = dataclasses.asdict(report) | {"report_path": str(path)}
    path.write_text(json.dumps(data, indent=2, default=str))
    text = f"isolation self-check {'passed' if report.passed else 'FAILED'}"
    if report.problems:
        text += "\n" + "\n".join(f"  problem: {p}" for p in report.problems)
    emit(args, data, text + f"\nreport {path}")
    return exit_code


async def cmd_scenarios(args: argparse.Namespace) -> int:
    scenarios = list(load_fixture().scenarios.values())
    data = [s.model_dump(mode="json") for s in scenarios]
    text = "\n".join(
        f"{s.scenario_id:34} {s.kind:10} "
        + (
            f"{len(s.provision_keys):2} provisions"
            if s.mode == "preset"
            else f"explore {s.before_version or '-'} -> {s.after_version}"
        )
        for s in scenarios
    )
    emit(args, data, text)
    return EXIT_OK


# ---------------------------------------------------------------- self-evolution (F3)


@contextlib.asynccontextmanager
async def _evolve_db(what: str):
    """The main database, migrated; evolution state lives there."""
    from womm.api.db import Database

    url = load_settings().database_url
    if not url:
        raise UsageError(f"{what} needs DATABASE_URL")
    db = Database(url)
    await db.open()
    try:
        await db.migrate()
        yield db
    finally:
        await db.close()


async def cmd_evolve_failures(args: argparse.Namespace) -> int:
    """Failure Memory patterns (U1) of one system version, from saved train/val eval reports
    or, with --from-db, from Postgres."""
    from womm.evolve.failure_memory import format_patterns, load_report_events, patterns
    from womm.evolve.planner_view import PlannerView

    version_id = args.sv or _load_sv(args).version_id
    if args.from_db:
        url = load_settings().database_url
        if not url:
            raise UsageError("--from-db needs DATABASE_URL")
        async with PlannerView(url, runs_dir=Path(args.runs_dir)) as view:
            rows = await view.failure_patterns(version_id)
    else:
        rows = patterns(*load_report_events(Path(args.runs_dir), version_id))
    emit(args, {"system_version": version_id, "patterns": rows},
         f"failure patterns of {version_id}\n{format_patterns(rows)}")  # fmt: skip
    return EXIT_OK


async def cmd_evolve_seed(args: argparse.Namespace) -> int:
    """Archive the self-evolution base and its api twin (U3)."""
    from womm.evolve.archive import Archive, seed_archive

    async with _evolve_db("womm evolve seed") as db:
        seeds = await seed_archive(Archive(db), REPO_ROOT)
    data = [{"version_id": s.version_id, "name": s.spec.name} for s in seeds]
    emit(args, data, "\n".join(f"archived {s.spec.name}: {s.version_id}" for s in seeds))
    return EXIT_OK


async def cmd_evolve_twin(args: argparse.Namespace) -> int:
    """Archive the api twin of an archived candidate (every role on the api backend), in the
    candidate's cycle: formal gate modes compare api twins (U3, U7). Idempotent."""
    from womm.evolve.archive import Archive, archive_twin

    async with _evolve_db("womm evolve twin") as db:
        archive = Archive(db)
        try:
            sv = await archive.load_candidate(args.version_id)
        except KeyError as exc:
            raise UsageError(f"{exc.args[0]} (womm evolve seed / cycle?)") from None
        row = await archive.get(sv.version_id)
        twin = await archive_twin(archive, sv, cycle_id=row["cycle_id"])
    data = {"version_id": twin.version_id, "twin_of": sv.version_id, "cycle_id": row["cycle_id"]}
    emit(args, data, f"api twin of {sv.version_id}: {twin.version_id}")
    return EXIT_OK


async def cmd_evolve_materialize(args: argparse.Namespace) -> int:
    """Write an archived candidate as a SystemVersion YAML plus its prompt files (U2/U3)."""
    from womm.evolve.archive import Archive
    from womm.evolve.edits import materialize

    async with _evolve_db("womm evolve materialize") as db:
        try:
            sv = await Archive(db).load_candidate(args.version_id)
        except KeyError as exc:
            raise UsageError(str(exc.args[0])) from None
    path = materialize(sv, Path(args.out))
    emit(args, {"version_id": sv.version_id, "path": str(path)}, f"wrote {path}")
    return EXIT_OK


async def _replay_backends(sv: SystemVersion, judge_sv: SystemVersion, args: argparse.Namespace):
    """The candidate's backends plus the pinned judge's (``judge_sv`` may use another one)."""
    backends, cli_version, _ = await prepare_backends(sv, skip_self_check=args.skip_self_check)
    if judge_sv.spec.judge.backend not in backends:
        extra, judge_cli, _ = await prepare_backends(judge_sv, skip_self_check=args.skip_self_check)
        backends = {**extra, **backends}
        cli_version = cli_version or judge_cli
    return backends, cli_version


async def _replay(args: argparse.Namespace, batch_id: str | None) -> int:
    """Submit (when ``batch_id`` is None) and run a replay batch in this process (U4)."""
    from womm.evolve.archive import Archive
    from womm.evolve.replay import ReplayRefused, ReplayStore, ReplayWorker

    seed = load_system_version(Path(args.seed), REPO_ROOT)
    async with _evolve_db("womm evolve replay") as db:
        archive, store = Archive(db), ReplayStore(db)
        if batch_id is None:
            if await archive.get(args.version_id) is None:
                raise UsageError(f"{args.version_id} is not archived (womm evolve seed?)")
            code = code_identity()
            try:
                batch_id = await store.submit(
                    args.version_id, args.split, args.repetitions,
                    judge_sv=seed, code=code, case_ids=args.case,
                )  # fmt: skip
            except ValueError as exc:
                raise UsageError(str(exc)) from None
            info(f"replay batch {batch_id}")
        try:
            batch = await store.batch(batch_id)
        except KeyError as exc:
            raise UsageError(str(exc.args[0])) from None
        if args.resume:
            await store.resume(batch_id)
            info(f"resumed batch {batch_id}: halt lifted, infra-errored items retried")
        if args.no_run:
            status = await store.status(batch_id)
        else:
            sv = await archive.load_candidate(batch["version_id"])
            settings = load_settings()
            decisions = make_decision_service(sv, settings)
            backends, cli_version = await _replay_backends(sv, seed, args)
            worker = ReplayWorker(
                store=store, archive=archive, judge_sv=seed, backends=backends,
                decisions=decisions, code=code_identity(cli_version),
                runs_dir=Path(args.runs_dir),
            )  # fmt: skip
            try:
                status = await worker.run_batch(batch_id)
            except ReplayRefused as exc:
                raise UsageError(str(exc)) from None
    text = ", ".join(f"{k}={status[k]}" for k in ("done", "errored", "running", "pending"))
    if status["infra_errored"]:
        text += f", infra_errored={status['infra_errored']} (retryable={status['retryable']})"
    if status["halted"]:
        text += f" halted: {status['halted']} (lift with --resume)"
    emit(args, status, f"batch {batch_id}: {text}")
    return EXIT_OK if status["complete"] or args.no_run else EXIT_FAILED


async def cmd_evolve_replay(args: argparse.Namespace) -> int:
    return await _replay(args, None)


async def cmd_evolve_worker(args: argparse.Namespace) -> int:
    return await _replay(args, args.batch_id)


async def _proposer_backend(name: str, backends: dict, args: argparse.Namespace, model: str):
    """The Improvement Planner's backend: the base's own instance when it uses the same one."""
    from womm.llm.base import get_backend

    if name in backends:
        return backends[name]
    if name == "fake":
        raise UsageError("the fake backend is for tests; the base version does not use it")
    backend = get_backend(name, load_settings())
    if name == "claude_code" and not args.skip_self_check:
        await backend.self_check(model)
    return backend


async def cmd_evolve_cycle(args: argparse.Namespace) -> int:
    """One self-evolution cycle on train/val (U5): GEPA prompt stage, then the topology stage,
    then one candidate named for the promotion gate. Never touches the holdout."""
    from womm.evolve.archive import Archive
    from womm.evolve.cycle import ReplayEvaluator, run_cycle
    from womm.evolve.planner_view import PlannerView
    from womm.evolve.proposers import Proposer, load_evolution_config
    from womm.evolve.replay import ReplayIncomplete, ReplayRefused, ReplayStore, ReplayWorker

    config = load_evolution_config(Path(args.config))
    if args.max_metric_calls:
        budget = config.budget.model_copy(update={"max_metric_calls": args.max_metric_calls})
        config = config.model_copy(update={"budget": budget})
    seed = load_system_version(Path(args.seed), REPO_ROOT)
    settings = load_settings()
    cycle_id = args.cycle_id or f"cycle_{_stamp()}"
    async with _evolve_db("womm evolve cycle") as db:
        archive, store = Archive(db), ReplayStore(db)
        try:
            base = await archive.load_candidate(args.base)
        except KeyError as exc:
            raise UsageError(f"{exc.args[0]} (womm evolve seed?)") from None
        backends, cli_version = await _replay_backends(base, seed, args)
        # One backend per Improvement Planner role (each may name its own backend and model).
        role_backends: dict[str, object] = {}
        for role in (config.roles.reflect, config.roles.propose_expert):
            if role.backend not in role_backends:
                role_backends[role.backend] = await _proposer_backend(
                    role.backend, backends, args, role.model
                )
            elif role.backend == "claude_code" and not (role.backend in backends
                                                        or args.skip_self_check):  # fmt: skip
                await role_backends[role.backend].self_check(role.model)  # its own model
        proposer = Proposer(
            role_backends[config.roles.reflect.backend], config,
            expert_backend=role_backends[config.roles.propose_expert.backend],
        )  # fmt: skip
        worker = ReplayWorker(
            store=store, archive=archive, judge_sv=seed, backends=backends,
            decisions=make_decision_service(base, settings), code=code_identity(cli_version),
            runs_dir=Path(args.runs_dir),
        )  # fmt: skip
        evaluator = ReplayEvaluator(archive_store=archive, store=store, worker=worker)
        async with PlannerView(settings.database_url, runs_dir=Path(args.runs_dir)) as view:
            try:
                result = await run_cycle(
                    base=base, view=view, evaluator=evaluator,
                    proposer=proposer, budget=config.budget,
                    stage=args.stage, cycle_id=cycle_id, run_dir=args.gepa_dir,
                )  # fmt: skip
            except ReplayRefused as exc:
                raise UsageError(str(exc)) from None
            except ReplayIncomplete as exc:
                info(f"error: {exc}")
                return EXIT_FAILED
            except ValueError as exc:
                raise UsageError(str(exc)) from None
    prompt, topo = result.prompt, result.topology
    data = {
        "cycle_id": cycle_id, "base": base.version_id, "chosen": result.chosen.version_id,
        "all_proposals_failed": result.all_proposals_failed,
        "prompt_stage": None if prompt is None else {
            "best": prompt.best.version_id, "front": prompt.front, "archived": prompt.archived,
            "rejections": prompt.rejections, "metric_calls": prompt.metric_calls,
            "spent_usd": prompt.spent_usd, "proposal_calls": prompt.proposal_calls,
            "proposal_errors": prompt.proposal_errors,
        },
        "topology_stage": None if topo is None else {
            "candidate": topo.candidate.version_id if topo.candidate else None,
            "reason": topo.reason, "target": topo.target, "rejections": topo.rejections,
            "spent_usd": topo.spent_usd,
        },
        "notes": result.notes,
    }  # fmt: skip
    lines = [f"cycle {cycle_id} on {base.version_id}"]
    if prompt is not None:
        n, calls = len(prompt.archived), prompt.metric_calls
        lines.append(f"prompt stage: {n} candidate(s) archived, {calls} metric calls, "
                     f"best {prompt.best.version_id}")  # fmt: skip
    if topo is not None:
        made = topo.candidate.version_id if topo.candidate else "none"
        lines.append(f"topology stage: {topo.reason} ({made})")
    lines += result.notes
    lines.append(f"candidate for the gate: {result.chosen.version_id}")
    if result.all_proposals_failed:
        lines.append("error: every proposer call failed (see the rejections); nothing was "
                     "proposed")  # fmt: skip
    emit(args, data, "\n".join(lines))
    return EXIT_FAILED if result.all_proposals_failed else EXIT_OK


def _holdout_store():
    """The sealed holdout store; only the promotion-side commands reach it."""
    from womm.eval.holdout import HoldoutError, HoldoutStore, holdout_database_url

    try:
        return HoldoutStore(holdout_database_url())
    except HoldoutError as exc:
        raise UsageError(str(exc)) from None


async def cmd_evolve_promote(args: argparse.Namespace) -> int:
    """The promotion gate (U7): one resumable holdout comparison of an archived candidate
    against the incumbent, decided under the pre-registered policy. Holdout side only."""
    from womm.evolve import promotion as pm
    from womm.evolve.archive import Archive
    from womm.evolve.diff_regression import r37_record

    try:
        policy, policy_sha = pm.load_policy(Path(args.policy))
        records = pm.load_records(Path(args.records))
    except pm.GateRefused as exc:
        raise UsageError(str(exc)) from None
    store = _holdout_store()
    settings = load_settings()
    async with _evolve_db("womm evolve promote") as db:
        archive = Archive(db)
        try:
            candidate = await archive.load_candidate(args.candidate)
            incumbent = await archive.load_candidate(args.incumbent)
        except KeyError as exc:
            raise UsageError(f"{exc.args[0]} (only archived versions go to the gate)") from None
        try:  # the candidate's archived cycle: a fresh --cycle-id cannot reset the budget
            cycle_id = await pm.gate_cycle_id(archive, candidate.version_id, args.cycle_id)
        except pm.GateRefused as exc:
            raise UsageError(f"promotion gate refused: {exc}") from None
        # Monitoring only: an unusable reference file is recorded as an error, never a block.
        r37 = await r37_record(archive, candidate.version_id, incumbent.version_id)
        await store.migrate()
        committed = pm.files_committed([Path(args.policy), Path(args.records)])
        try:  # refuse before preparing any backend; run_gate checks again
            await pm.preflight(policy, records, candidate, incumbent, store=store,
                               cycle_id=cycle_id, policy_committed=committed)  # fmt: skip
        except pm.GateRefused as exc:
            raise UsageError(f"promotion gate refused: {exc}") from None
        backends, cli_version, _ = await prepare_backends(
            candidate, skip_self_check=args.skip_self_check
        )
        extra, other_cli, _ = await prepare_backends(
            incumbent, skip_self_check=args.skip_self_check
        )
        backends = {**extra, **backends}
        try:
            decision = await pm.run_gate(
                candidate=candidate, incumbent=incumbent, policy=policy,
                policy_sha256=policy_sha, records=records, store=store, backends=backends,
                decisions=lambda sv: make_decision_service(sv, settings),
                code=code_identity(cli_version or other_cli), cycle_id=cycle_id,
                policy_committed=committed, r37=r37, summary_db=db, resume=args.resume,
            )  # fmt: skip
        except pm.GateRefused as exc:
            raise UsageError(f"promotion gate refused: {exc}") from None
        except pm.SummaryNotRecorded as exc:
            emit(args, exc.decision.model_dump(mode="json"), pm.format_decision(exc.decision))
            info(f"error: {exc}; insert the summary by hand from `womm evolve show "
                 f"{candidate.version_id}`")  # fmt: skip
            return EXIT_FAILED
    emit(args, decision.model_dump(mode="json"), pm.format_decision(decision))
    return EXIT_OK


async def cmd_evolve_show(args: argparse.Namespace) -> int:
    """The gate decisions recorded in the sealed holdout audit for one candidate (local only;
    aggregates, never case ids)."""
    from womm.evolve.promotion import format_decision

    store = _holdout_store()
    await store.migrate()
    rows = await store.decisions_for(args.candidate)
    text = "\n\n".join(f"{r['recorded_at']}\n{format_decision(r)}" for r in rows)
    emit(args, rows, text or f"no gate decision recorded for {args.candidate}")
    return EXIT_OK


async def cmd_evolve_diffcheck(args: argparse.Namespace) -> int:
    """The R37 diff regression check (U8): score an archived version on the R2 demo diff
    against hand-written reference answers. Monitoring only, never a gate."""
    from womm.evolve.archive import Archive
    from womm.evolve.diff_regression import DIFF_CHECK_SPLIT, diff_check_score, load_reference
    from womm.evolve.replay import ReplayRefused, ReplayStore, ReplayWorker

    reference = load_reference(Path(args.reference))
    if not reference.available:
        emit(args, {"version_id": args.version_id, "status": "not_available",
                    "reason": reference.reason},
             f"R37 diff check: not_available ({reference.reason})")  # fmt: skip
        return EXIT_OK
    seed = load_system_version(Path(args.seed), REPO_ROOT)
    async with _evolve_db("womm evolve diffcheck") as db:
        archive = Archive(db)
        store = ReplayStore(db, diff_check_cases=lambda: [reference.case])
        try:
            sv = await archive.load_candidate(args.version_id)
        except KeyError as exc:
            raise UsageError(f"{exc.args[0]} (womm evolve seed?)") from None
        backends, cli_version = await _replay_backends(sv, seed, args)
        code = code_identity(cli_version)
        try:
            batch_id = await store.submit(sv.version_id, DIFF_CHECK_SPLIT, args.repetitions,
                                          judge_sv=seed, code=code)  # fmt: skip
        except ValueError as exc:
            raise UsageError(str(exc)) from None
        worker = ReplayWorker(
            store=store, archive=archive, judge_sv=seed, backends=backends,
            decisions=make_decision_service(sv, load_settings()), code=code,
            runs_dir=Path(args.runs_dir),
        )  # fmt: skip
        try:
            status = await worker.run_batch(batch_id)
        except ReplayRefused as exc:
            raise UsageError(str(exc)) from None
        score = await diff_check_score(archive, sv.version_id)
    data = {"version_id": sv.version_id, "status": "available", "batch": status, "score": score}
    if not status["complete"]:
        emit(args, data, f"R37 diff check batch {batch_id} incomplete (rerun to resume)")
        return EXIT_FAILED
    mean = "n/a" if not score or score["mean"] is None else f"{score['mean']:.3f}"
    emit(args, data, f"R37 diff check of {sv.version_id}: coverage {mean} "
                     f"over {score['n'] if score else 0} run(s) (monitoring only)")  # fmt: skip
    return EXIT_OK


# ----------------------------------------------------------------------------- calibration


def _resolve_sv(ref: str) -> SystemVersion:
    """A SystemVersion file, or a version id found among ``system_versions/*.yaml``."""
    path = Path(ref)
    if path.suffix in (".yaml", ".yml") and path.is_file():
        return load_system_version(path, REPO_ROOT)
    for candidate in sorted((REPO_ROOT / "system_versions").glob("*.yaml")):
        try:
            sv = load_system_version(candidate, REPO_ROOT)
        except Exception:  # noqa: BLE001 - an unrelated broken file must not block the lookup
            continue
        if sv.version_id == ref:
            return sv
    raise UsageError(f"no system version {ref} under system_versions/ (pass its YAML file, or "
                     "write an archived one with `womm evolve materialize`)")  # fmt: skip


async def cmd_calibrate_sample(args: argparse.Namespace) -> int:
    """Draw (dossier, expected impact) pairs for a blind coverage-judge calibration."""
    from womm.eval import judge_calibration as jc
    from womm.eval.evaluators import judge_version

    sv = _resolve_sv(args.sv)
    cases = {c.case_id: c for c in load_all_golden(Path(args.golden_dir))}
    try:
        summary = jc.write_sample(
            Path(args.out), system_version=sv.version_id, judge_version=judge_version(sv),
            reports=[Path(r) for r in args.report], cases=cases,
            runs_dirs=[Path(args.runs_dir)], n=args.n, seed=args.seed,
            annotators=args.annotator or ["annotator1"],
        )  # fmt: skip
    except jc.CalibrationError as exc:
        raise UsageError(str(exc)) from None
    pop = summary["population"]
    text = (f"{summary['pairs']} pairs ({summary['judge_covered']} judge-covered, "
            f"{summary['judge_missed']} judge-missed) from {summary['cases']} case(s); "
            f"population {pop['pairs']} pairs, judge covered rate "
            f"{pop['natural_covered_rate']:.1%}\n"
            f"wrote {summary['out']}: send each annotator sheet_<name>.md and "
            f"answers_<name>.yaml; keep {jc.KEY_FILE} private")  # fmt: skip
    emit(args, summary, text)
    return EXIT_OK


async def cmd_calibrate_score(args: argparse.Namespace) -> int:
    """Agreement of the coverage judge with the annotators; --record appends the aggregate."""
    from womm.eval import judge_calibration as jc
    from womm.evolve import promotion as pm

    directory = Path(args.dir)
    try:
        key = json.loads((directory / jc.KEY_FILE).read_text(encoding="utf-8"))
        answers = jc.read_answers(directory, set(key["pairs"]))
    except FileNotFoundError:
        raise UsageError(f"no {jc.KEY_FILE} in {directory}: run `womm calibrate sample`") from None
    except jc.CalibrationError as exc:
        raise UsageError(str(exc)) from None
    result = jc.score(key, answers)
    text = jc.format_result(result)
    if args.record:
        if problems := jc.record_problems(result):
            raise UsageError("not recorded: " + "; ".join(problems))
        record = jc.calibration_record(result, dt.date.today().isoformat())
        try:
            pm.append_records(Path(args.records), "judge_calibrations", [record])
        except pm.GateRefused as exc:
            raise UsageError(str(exc)) from None
        result["recorded"] = record
        text += f"\nrecorded in {args.records}; commit it"
    emit(args, result, text)
    return EXIT_OK


async def cmd_noise_report(args: argparse.Namespace) -> int:
    """R34 run-to-run noise and the holdout MDD; --record appends both records."""
    from womm.eval import noise_report as nr
    from womm.eval.run_eval import read_report
    from womm.evolve import promotion as pm

    report = read_report(Path(args.report))
    try:
        policy, _ = pm.load_policy(Path(args.policy))
    except pm.GateRefused as exc:
        raise UsageError(str(exc)) from None
    repetitions = args.repetitions or policy.repetitions
    try:
        result = nr.noise_report(
            report, repetitions=repetitions, n_cases=args.holdout_cases,
            n_proposals=args.holdout_proposals, icc=args.icc, alpha=args.alpha, power=args.power,
        )  # fmt: skip
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    text = nr.format_report(result, expected_gain=policy.expected_gain)
    if repetitions != policy.repetitions:
        text += (f"\nnote: {repetitions} repetitions, the policy runs {policy.repetitions}; the "
                 "gate ignores an MDD report for other repetitions")  # fmt: skip
    if not policy.signed:
        text += "\nnote: the promotion policy is not signed yet"
    if args.record:
        if result["formal_problems"]:
            raise UsageError("not recorded: " + "; ".join(result["formal_problems"]))
        noise_run, mdds = nr.records_for(result, dt.date.today().isoformat())
        try:
            pm.append_records(Path(args.records), "formal_noise_runs", [noise_run])
            if mdds:
                pm.append_records(Path(args.records), "mdd_reports", mdds)
        except pm.GateRefused as exc:
            raise UsageError(str(exc)) from None
        result["recorded"] = {"formal_noise_runs": [noise_run], "mdd_reports": mdds}
        text += f"\nrecorded 1 noise run and {len(mdds)} MDD report(s) in {args.records}; commit it"
    emit(args, result, text)
    return EXIT_OK


def _sv_path(value: str) -> Path:
    """A SystemVersion YAML path, or a file name under system_versions/ ("v1.0-cost")."""
    path = Path(value)
    if path.is_file():
        return path
    named = REPO_ROOT / "system_versions" / f"{value.removesuffix('.yaml')}.yaml"
    if named.is_file():
        return named
    raise UsageError(f"no system version {value!r} (a YAML path or a name in system_versions/)")


async def cmd_cost_sweep(args: argparse.Namespace) -> int:
    """Cost records for every duty and prohibition of one corpus version (EU cost plan U5)."""
    from womm.cost.sweep import SweepError, SweepProgress, plan_sweep, run_sweep
    from womm.data.corpus import load_default_corpus

    sv = load_system_version(_sv_path(args.sv or args.system_version), REPO_ROOT)
    try:
        plan = plan_sweep(sv, load_default_corpus(), args.version)
    except SweepError as exc:
        raise UsageError(str(exc)) from None
    backends, cli_version, _ = await prepare_backends(sv, skip_self_check=args.skip_self_check)
    backend = backends[plan.role.backend]
    progress = SweepProgress()
    out = await run_sweep(
        plan, backend=backend, root=Path(args.runs_dir) / "cost_sweeps",
        code=code_identity(cli_version), repetitions=args.repetitions, max_usd=args.max_usd,
        progress=progress, log=info,
    )  # fmt: skip
    dev_only = plan.role.backend != "api"
    data = {
        "directory": str(out),
        "system_version": sv.version_id,
        "version": plan.version,
        "batches": len(plan.batches),
        "records": sum(len(b.items) for b in plan.batches),
        "repetitions": args.repetitions,
        "calls": progress.calls,
        "skipped": progress.skipped,
        "failed": progress.failed,
        "stopped_at_budget": progress.stopped_at_budget,
        "tokens": sum(u.input_tokens + u.output_tokens for u in progress.usage),
        "cost_usd": round(progress.cost_usd, 4),
        "dev_only": dev_only,
    }
    text = (
        f"cost sweep {sv.version_id} on {plan.version}: {data['records']} records in "
        f"{data['batches']} batches x {args.repetitions} repetitions"
        f"{' (dev-only: ' + plan.role.backend + ' backend)' if dev_only else ''}\n"
        f"calls={progress.calls} skipped={progress.skipped} failed={len(progress.failed)} "
        f"tokens={data['tokens']} cost≈${progress.cost_usd:.3f}"
        + ("\nstopped at --max-usd; rerun to resume" if progress.stopped_at_budget else "")
        + (f"\n{len(progress.failed)} batch(es) failed; rerun to retry" if progress.failed else "")
        + f"\nsaved {out}"
    )
    emit(args, data, text)
    return EXIT_FAILED if progress.failed or progress.stopped_at_budget else EXIT_OK


def _run_records(paths: list[str]) -> tuple[dict[int, list], set[str], str, str]:
    """Cost records of saved runs (``womm run`` output), one repetition per run, plus the union
    of their scenario keys, the system version and the cost backend."""
    from womm.data.fixtures import load_fixture as _fixture

    reps: dict[int, list] = {}
    keys: set[str] = set()
    versions: set[str] = set()
    backends: set[str] = set()
    for n, path in enumerate(paths, 1):
        try:
            run = RunResult.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise UsageError(f"{path} is not a saved run: {exc}") from None
        if run.dossier is None or run.dossier.costs is None:
            raise UsageError(f"{path}: the run has no cost section (not a cost-enabled version)")
        reps[n] = list(run.dossier.costs.records)
        keys |= {r.provision_key for r in run.dossier.costs.records}
        keys |= set(_fixture().scenario(run.scenario_id).provision_keys)
        versions.add(run.system_version)
        backends |= {u.backend for u in run.usage if u.role == "cost"}
    return reps, keys, ",".join(sorted(versions)), ",".join(sorted(backends)) or "unknown"


async def cmd_cost_check(args: argparse.Namespace) -> int:
    """R6: cost records against SWD(2021) 84 (scoring side; the reference is never an input)."""
    from womm.cost.sweep import SweepError, load_sweep
    from womm.data.corpus import load_default_corpus
    from womm.eval import cost_check as cc

    corpus = load_default_corpus()
    try:
        ref = cc.load_reference(Path(args.reference), corpus)
    except cc.CostReferenceError as exc:
        raise UsageError(str(exc)) from None
    restrict = None
    if args.sweep:
        try:
            sweep = load_sweep(Path(args.sweep))
        except SweepError as exc:
            raise UsageError(str(exc)) from None
        if sweep.version != ref.proposal_version:
            raise UsageError(
                f"the IA assessed {ref.proposal_version}; this sweep is of {sweep.version}"
            )
        if sweep.missing:
            raise UsageError(f"the sweep is incomplete ({sweep.missing}); rerun `womm cost sweep`")
        reps, sv_id, backend = sweep.repetitions, sweep.system_version, sweep.cost_backend
        source = str(sweep.directory)
    else:
        reps, restrict, sv_id, backend = _run_records(args.runs)
        source = ", ".join(args.runs)
    out = cc.score_repetitions(reps, ref, restrict_keys=restrict)
    header = {
        "reference_sha": cc.reference_sha(Path(args.reference)),
        "system_version": sv_id,
        "backend": backend,
        "source": source,
        "restricted_to": "the runs' scenarios" if restrict is not None else None,
    }
    text = cc.format_report(out, header)
    out_dir = Path(args.runs_dir) / "cost_check"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"cost_check_{_stamp()}_{sv_id.replace(',', '+')}"
    (out_dir / f"{stem}.json").write_text(
        json.dumps({"header": header, **out}, indent=1, ensure_ascii=False, default=str) + "\n"
    )
    (out_dir / f"{stem}.md").write_text(text + "\n")
    emit(args, {"header": header, **out}, text + f"\n\nsaved {out_dir / stem}.json and .md")
    return EXIT_OK


async def cmd_cost_late_added(args: argparse.Namespace) -> int:
    """R7: cost records on obligations added after the proposal. Reported, never scored."""
    import statistics

    from womm.cost.late_added import format_late_added, late_added_report
    from womm.cost.sweep import SweepError, load_sweep

    try:
        sweep = load_sweep(Path(args.sweep))
    except SweepError as exc:
        raise UsageError(str(exc)) from None
    if sweep.version == "com2021_206":
        raise UsageError("R7 reads a sweep of the adopted act (reg2024_1689), not the proposal")
    if not sweep.repetitions:
        raise UsageError(f"{sweep.directory} has no repetitions yet")
    reports = {k: late_added_report(v) for k, v in sorted(sweep.repetitions.items())}
    first = next(iter(reports))
    shares = [r.costly_share for r in reports.values() if r.costly_share is not None]
    dev = "" if sweep.cost_backend == "api" else f" dev-only ({sweep.cost_backend} backend)"
    header = (
        f"Sweep {sweep.directory} · {sweep.system_version} on {sweep.version}{dev} · "
        f"{len(reports)} repetition(s); detail from rep{first}"
        + (f"; incomplete: {sweep.missing}" if sweep.missing else "")
    )
    text = format_late_added(reports[first], header)
    if shares:
        text += (
            f"\n\nShare of medium/high records in added text across repetitions: mean "
            f"{statistics.fmean(shares):.0%} (min {min(shares):.0%}, max {max(shares):.0%})"
        )
    data = {
        "directory": str(sweep.directory),
        "system_version": sweep.system_version,
        "version": sweep.version,
        "scored": False,
        "repetitions": {
            k: {
                "added_records": [r.obligation_id for r in rep.records],
                "by_payer": rep.by_payer,
                "by_effort": rep.by_effort,
                "by_band": rep.by_band,
                "costly_added": rep.costly_added,
                "costly_total": rep.costly_total,
                "changed_not_added": rep.changed_not_added,
                "by_payer_costly": rep.by_payer_costly,
            }
            for k, rep in reports.items()
        },
    }
    emit(args, data, text)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    settings = load_settings()
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    common.add_argument(
        "--system-version",
        default=str(settings.system_version_path),
        help="SystemVersion YAML (default: %(default)s)",
    )
    common.add_argument(
        "--runs-dir", default=str(REPO_ROOT / "runs"), help="where results are written"
    )

    parser = argparse.ArgumentParser(
        prog="womm",
        description="WOMM regulatory impact assessment pipeline",
        epilog=EXIT_CODES_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", parents=[common], help="run one scenario through the pipeline")
    p_run.add_argument("scenario", help="scenario_id (see `womm scenarios`)")
    p_run.add_argument("--skip-self-check", action="store_true", help="dev only")
    p_run.add_argument("--decider", choices=["jev", "stub"], help="override the router decider")
    p_eval = sub.add_parser("eval", parents=[common], help="run golden cases and score them")
    p_eval.add_argument("--case", action="append", help="golden case_id to run (repeatable)")
    p_eval.add_argument(
        "--split",
        choices=SELECTABLE_SPLITS,
        help="only cases of this split (holdout is never selectable here)",
    )
    p_eval.add_argument("--repetitions", type=int, default=1, help="runs per case (noise)")
    p_eval.add_argument("--baseline", action="store_true", help="tag as a baseline experiment")
    p_eval.add_argument(
        "--formal",
        action="store_true",
        help="the R34 noise run: one --split, >= 6 repetitions, api backend only, clean tree",
    )
    p_eval.add_argument("--local", action="store_true", help="do not record in LangSmith")
    p_eval.add_argument("--skip-self-check", action="store_true", help="dev only")
    p_eval.add_argument("--decider", choices=["jev", "stub"], help="override the router decider")
    sub.add_parser(
        "selfcheck", parents=[common], help="verify backend auth and claude_code isolation"
    )
    sub.add_parser("scenarios", parents=[common], help="list fixture scenarios")
    p_evolve = sub.add_parser("evolve", help="self-evolution cycle (train/val only)")
    evolve = p_evolve.add_subparsers(dest="evolve_cmd", required=True)
    p_fail = evolve.add_parser(
        "failures", parents=[common], help="Failure Memory patterns of a system version"
    )
    p_fail.add_argument("--sv", help="version id (default: the --system-version file's id)")
    p_fail.add_argument("--from-db", action="store_true", help="read Postgres, not runs/")
    evolve.add_parser(
        "seed", parents=[common], help="archive the self-evolution base and its api twin"
    )
    p_twin = evolve.add_parser(
        "twin", parents=[common],
        help="archive the api twin of an archived candidate (formal gate modes compare twins)",
    )  # fmt: skip
    p_twin.add_argument("version_id")
    p_mat = evolve.add_parser(
        "materialize", parents=[common], help="write an archived candidate as files"
    )
    p_mat.add_argument("version_id")
    p_mat.add_argument("--out", default=str(REPO_ROOT), help="repository root to write under")
    replay_common = argparse.ArgumentParser(add_help=False)
    replay_common.add_argument(
        "--seed",
        default=str(REPO_ROOT / "system_versions" / "v1.0-unscoped.yaml"),
        help="SystemVersion whose judge every replay is pinned to (default: %(default)s)",
    )
    replay_common.add_argument("--no-run", action="store_true", help="only enqueue / report")
    replay_common.add_argument(
        "--resume", action="store_true",
        help="lift a halt (e.g. after a rate limit) and retry the batch's infra-errored items",
    )  # fmt: skip
    replay_common.add_argument("--skip-self-check", action="store_true", help="dev only")
    p_replay = evolve.add_parser(
        "replay", parents=[common, replay_common],
        help="replay an archived version on train/val as resumable work items",
    )  # fmt: skip
    p_replay.add_argument("version_id")
    p_replay.add_argument("--split", choices=SELECTABLE_SPLITS, required=True)
    p_replay.add_argument("--repetitions", type=int, default=1)
    p_replay.add_argument("--case", action="append", help="only this case (repeatable)")
    p_worker = evolve.add_parser(
        "worker", parents=[common, replay_common], help="resume an existing replay batch"
    )
    p_worker.add_argument("batch_id")
    p_cycle = evolve.add_parser(
        "cycle", parents=[common, replay_common],
        help="one self-evolution cycle on train/val: prompt stage, topology stage, a candidate",
    )  # fmt: skip
    p_cycle.add_argument("--base", required=True, help="archived version id to evolve")
    p_cycle.add_argument("--stage", choices=("prompt", "topology", "both"), default="both")
    p_cycle.add_argument(
        "--config", default=str(REPO_ROOT / "evals" / "evolution.yaml"),
        help="Improvement Planner roles and budget (default: %(default)s)",
    )  # fmt: skip
    p_cycle.add_argument("--max-metric-calls", type=int, help="override the config budget")
    p_cycle.add_argument("--cycle-id", help="default: cycle_<UTC timestamp>")
    p_cycle.add_argument("--gepa-dir", help="GEPA state directory, to resume a stopped stage")
    p_diff = evolve.add_parser(
        "diffcheck", parents=[common, replay_common],
        help="R37: score an archived version on the demo diff (monitoring, never a gate)",
    )  # fmt: skip
    p_diff.add_argument("version_id")
    p_diff.add_argument("--repetitions", type=int, default=3)
    p_diff.add_argument(
        "--reference", default=str(REPO_ROOT / "evals" / "diff_regression" /
                                   "demo_penalties_amended.yaml"),
        help="hand-written reference answers (default: %(default)s)",
    )  # fmt: skip
    p_promote = evolve.add_parser(
        "promote", parents=[common],
        help="promotion gate: one holdout comparison under the pre-registered policy "
             "(needs HOLDOUT_DATABASE_URL)",
    )  # fmt: skip
    p_promote.add_argument("candidate", help="archived version id (an api twin for formal modes)")
    p_promote.add_argument("--incumbent", required=True, help="archived version id")
    p_promote.add_argument(
        "--cycle-id",
        help="the candidate's archived cycle id (default), or its api twin's; any other is refused",
    )
    p_promote.add_argument(
        "--resume", action="store_true",
        help="take over the budget reservation of a stopped run of the same pair in this cycle",
    )  # fmt: skip
    p_promote.add_argument(
        "--policy", default=str(REPO_ROOT / "evals" / "promotion_policy.yaml"),
        help="pre-registered promotion policy (default: %(default)s)",
    )  # fmt: skip
    p_promote.add_argument(
        "--records", default=str(REPO_ROOT / "evals" / "promotion_records.yaml"),
        help="judge calibration, R34 and MDD records (default: %(default)s)",
    )  # fmt: skip
    p_promote.add_argument("--skip-self-check", action="store_true", help="dev only")
    p_show = evolve.add_parser(
        "show", parents=[common],
        help="gate decisions from the sealed holdout audit (local; needs HOLDOUT_DATABASE_URL)",
    )  # fmt: skip
    p_show.add_argument("candidate")
    _add_calibration_parsers(sub, common)
    return parser


def _add_calibration_parsers(sub: Any, common: argparse.ArgumentParser) -> None:
    from womm.eval.cost_check import REFERENCE_PATH as COST_REFERENCE
    from womm.eval.golden import GOLDEN_DIR
    from womm.evolve.promotion import POLICY_PATH, RECORDS_PATH

    p_cal = sub.add_parser("calibrate", help="coverage-judge calibration (train/val only)")
    cal = p_cal.add_subparsers(dest="calibrate_cmd", required=True)
    p_sample = cal.add_parser(
        "sample", parents=[common],
        help="draw blind (dossier, expected impact) pairs and write labeling sheets",
    )  # fmt: skip
    p_sample.add_argument("--sv", required=True, help="version id or YAML file of the judge's SV")
    p_sample.add_argument("--report", action="append", required=True,
                          help="train/val eval report from `womm eval` (repeatable)")  # fmt: skip
    p_sample.add_argument("--n", type=int, default=30, help="pairs to draw (default 30)")
    p_sample.add_argument("--seed", type=int, required=True)
    p_sample.add_argument("--annotator", action="append",
                          help="annotator name; one sheet each (repeatable)")  # fmt: skip
    p_sample.add_argument("--out", required=True, help="e.g. .cache/calibration/<name>/")
    p_sample.add_argument("--golden-dir", default=str(GOLDEN_DIR), help=argparse.SUPPRESS)
    p_score = cal.add_parser("score", parents=[common], help="judge agreement with annotators")
    p_score.add_argument("--dir", required=True, help="the `calibrate sample --out` directory")
    p_score.add_argument("--record", action="store_true",
                         help="append a judge_calibrations record (aggregates only)")  # fmt: skip
    p_score.add_argument("--records", default=str(RECORDS_PATH), help=argparse.SUPPRESS)
    p_noise = sub.add_parser("noise", help="R34 noise and minimum detectable delta")
    noise = p_noise.add_subparsers(dest="noise_cmd", required=True)
    p_nrep = noise.add_parser("report", parents=[common],
                              help="noise SD and holdout MDD from a formal noise run")  # fmt: skip
    p_nrep.add_argument("--report", required=True, help="the R34 formal eval report")
    p_nrep.add_argument("--holdout-cases", type=int, required=True,
                        help="sealed holdout case count")  # fmt: skip
    p_nrep.add_argument("--holdout-proposals", type=int, required=True,
                        help="sealed holdout proposal count")  # fmt: skip
    p_nrep.add_argument("--icc", type=float, default=1.0,
                        help="intra-proposal correlation (default 1.0, conservative)")  # fmt: skip
    p_nrep.add_argument("--alpha", type=float, default=0.05)
    p_nrep.add_argument("--power", type=float, default=0.8)
    p_nrep.add_argument("--repetitions", type=int,
                        help="per holdout case and arm (default: the policy's)")  # fmt: skip
    p_nrep.add_argument("--record", action="store_true",
                        help="append formal_noise_runs and mdd_reports records")  # fmt: skip
    p_nrep.add_argument("--policy", default=str(POLICY_PATH), help=argparse.SUPPRESS)
    p_nrep.add_argument("--records", default=str(RECORDS_PATH), help=argparse.SUPPRESS)
    p_cost = sub.add_parser("cost", help="EU-level cost estimation (cost records, IA cost check)")
    cost = p_cost.add_subparsers(dest="cost_cmd", required=True)
    p_sweep = cost.add_parser("sweep", parents=[common],
                              help="cost records for every duty of one corpus version")  # fmt: skip
    p_sweep.add_argument("--sv", help="cost-enabled SystemVersion: YAML path or name (v1.0-cost)")
    p_sweep.add_argument("--version", required=True, help="corpus version (com2021_206, ...)")
    p_sweep.add_argument("--repetitions", type=int, default=3)
    p_sweep.add_argument("--max-usd", type=float, help="stop scheduling batches at this spend")
    p_sweep.add_argument("--skip-self-check", action="store_true", help="dev only")
    p_check = cost.add_parser("check", parents=[common],
                              help="R6: score cost records against SWD(2021) 84")  # fmt: skip
    source = p_check.add_mutually_exclusive_group(required=True)
    source.add_argument("--sweep", help="a `womm cost sweep` directory of the proposal")
    source.add_argument("--runs", nargs="+", help="saved runs of a cost-enabled version")
    p_check.add_argument("--reference", default=str(COST_REFERENCE), help=argparse.SUPPRESS)
    p_late = cost.add_parser(
        "late-added", parents=[common], help="R7: costs the ex-ante IA could not see (not scored)"
    )
    p_late.add_argument("--sweep", required=True, help="a `womm cost sweep` of reg2024_1689")


HANDLERS = {
    "run": cmd_run,
    "eval": cmd_eval,
    "selfcheck": cmd_selfcheck,
    "scenarios": cmd_scenarios,
}


EVOLVE_HANDLERS = {
    "failures": cmd_evolve_failures,
    "seed": cmd_evolve_seed,
    "materialize": cmd_evolve_materialize,
    "twin": cmd_evolve_twin,
    "replay": cmd_evolve_replay,
    "worker": cmd_evolve_worker,
    "cycle": cmd_evolve_cycle,
    "diffcheck": cmd_evolve_diffcheck,
    "promote": cmd_evolve_promote,
    "show": cmd_evolve_show,
}


CALIBRATE_HANDLERS = {"sample": cmd_calibrate_sample, "score": cmd_calibrate_score}
NOISE_HANDLERS = {"report": cmd_noise_report}
COST_HANDLERS = {
    "sweep": cmd_cost_sweep,
    "check": cmd_cost_check,
    "late-added": cmd_cost_late_added,
}
GROUPS = {
    "evolve": ("evolve_cmd", EVOLVE_HANDLERS),
    "calibrate": ("calibrate_cmd", CALIBRATE_HANDLERS),
    "noise": ("noise_cmd", NOISE_HANDLERS),
    "cost": ("cost_cmd", COST_HANDLERS),
}


# `womm evolve` subcommands allowed to run with the holdout URL set: only the promotion gate
# (U7) and the local reader of its audit. Every other evolve command is Improvement-Planner side.
HOLDOUT_SIDE_EVOLVE = frozenset({"promote", "show"})


def _refuse_holdout_for_planner_side(args: argparse.Namespace) -> None:
    from womm.evolve.planner_view import HoldoutEnvRefused, refuse_holdout_env

    if args.cmd == "evolve" and args.evolve_cmd not in HOLDOUT_SIDE_EVOLVE:
        try:
            refuse_holdout_env()
        except HoldoutEnvRefused as exc:
            raise UsageError(str(exc)) from None


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    if args.cmd in GROUPS:
        dest, table = GROUPS[args.cmd]
        handler = table[getattr(args, dest)]
    else:
        handler = HANDLERS[args.cmd]
    try:
        _refuse_holdout_for_planner_side(args)
        return asyncio.run(handler(args))
    except (UsageError, FixtureError, GoldenError, BaselineRefused, ConfigError) as exc:
        info(f"error: {exc}")
        return EXIT_USAGE
    except (FileNotFoundError, ValidationError, yaml.YAMLError) as exc:
        info(f"error: invalid configuration: {exc}")
        return EXIT_USAGE
    except LLMError as exc:
        info(f"error: backend unavailable: {exc}")
        return EXIT_BACKEND


if __name__ == "__main__":
    sys.exit(main())
