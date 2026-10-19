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
    sync_dataset,
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
  womm eval --split val --repetitions 3 --formal   # R34 noise run (api only; may extend by 3)
  womm calibrate sample --sv sv_c8a57a8215d7 --report runs/eval_X.json --n 30 --seed 1 \
      --annotator alice --annotator bob --out .cache/calibration/2026-10
  womm calibrate upload --dir .cache/calibration/2026-10 --url https://<app>.up.railway.app
  womm calibrate fetch --dir .cache/calibration/2026-10 --url https://<app>.up.railway.app
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


def langsmith_client() -> Any:
    from langsmith import Client

    return Client()


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
    if args.formal and (args.local or not settings.langsmith_api_key):
        # An R34 extension refuses a run without its own experiment; refuse before the spend.
        raise UsageError("a formal run records a LangSmith experiment (an R34 extension needs one "
                         "per run): set LANGSMITH_API_KEY and drop --local")  # fmt: skip
    if args.formal:
        # A set but rejected key would only fail after the spend: do the recording's own
        # dataset sync now (idempotent; the run's cases, never holdout).
        try:
            sync_dataset(langsmith_client(), cases)
        except Exception as exc:  # noqa: BLE001
            reason = f"{type(exc).__name__}: {exc}"
            raise UsageError(f"LangSmith is not usable ({reason}); a formal run must record "
                             "its experiment, so nothing was spent") from exc  # fmt: skip
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
            # LangSmith prints the experiment link to stdout; stdout is reserved for data.
            with contextlib.redirect_stdout(sys.stderr):
                name = await record_langsmith_experiment(
                    report, cases, langsmith_client(), prefix=_experiment_prefix(sv, report)
                )
            report.metadata["langsmith_experiment"] = name
            info(f"LangSmith experiment: {name}")
        except Exception as exc:  # noqa: BLE001
            info(f"warning: LangSmith recording failed: {type(exc).__name__}: {exc}")
    unrecorded = (
        args.formal and not report.aborted and "langsmith_experiment" not in report.metadata
    )
    if settings.database_url and not report.aborted:
        try:
            n = await persist_failures(report, settings.database_url)
            info(f"recorded {n} failure record(s) in Postgres")
        except Exception as exc:  # noqa: BLE001
            info(f"warning: failure records not stored: {type(exc).__name__}: {exc}")
    path.write_text(report.to_json())  # now including the experiment name, if recorded
    if args.formal and not report.aborted:
        from womm.eval import noise_report as nr
        from womm.evolve import promotion as pm

        records = Path(args.records) if args.records else pm.RECORDS_PATH
        try:
            nr.log_formal_run(records, json.loads(report.to_json()), path,
                              dt.datetime.now(dt.UTC).isoformat(timespec="seconds"))  # fmt: skip
        except pm.GateRefused as exc:
            info(f"error: the formal run was not logged in {records}: {exc}")
            return EXIT_FAILED
        info(f"logged the formal run in {records}; commit it before the next formal run "
             "(a formal run refuses a dirty tree)")  # fmt: skip

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
    if unrecorded:  # logged above (it was paid for), but an R34 extension would refuse it
        info("error: this formal run has no LangSmith experiment, so an R34 extension cannot "
             "use it (each run needs its own); fix LangSmith before `womm noise report "
             "--record`, or plan the 6-repetition fallback run")  # fmt: skip
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
    # The 2026-11-20 route (runbook): new expert only when the topology stage made the chosen
    # candidate; otherwise the prompt-only fallback, with the reason.
    if (
        topo is not None
        and topo.candidate is not None
        and result.chosen.version_id == topo.candidate.version_id
    ):
        route, why = "new_expert", f"the new expert's candidate was chosen ({topo.reason})"
    elif topo is not None and topo.candidate is not None:
        route, why = "prompt_only", "the new expert's candidate did not beat its parent on val"
    elif topo is not None:
        route, why = "prompt_only", topo.reason
    else:
        route, why = "prompt_only", "the topology stage was not run (--stage prompt)"
    data["route"] = {"route": route, "why": why}
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
    lines.append(f"route: {route.replace('_', '-')} ({why})")
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
        # Report-only: the new expert's category, read here and passed to the holdout side.
        target = await pm.target_category(archive, candidate.version_id)
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
                target_category=target,
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
    from womm.evolve.rescore import format_rescore

    store = _holdout_store()
    await store.migrate()
    rows = await store.decisions_for(args.candidate)
    blocks = []
    for r in rows:
        rescore = await store.current_rescore(r["gate_id"]) if r.get("gate_id") else None
        r["rescore"] = rescore
        check = format_rescore(rescore) if rescore else "cross-judge check: not run"
        blocks.append(f"{r['recorded_at']}\n{format_decision(r)}\n{check}")
    emit(args, rows, "\n\n".join(blocks) or f"no gate decision recorded for {args.candidate}")
    return EXIT_OK


async def cmd_evolve_rescore(args: argparse.Namespace) -> int:
    """Re-score a gate comparison's stored runs with a cross-family judge (report-only; local,
    aggregates only). Judge calls only, on the judge version's backend (the Claude subscription
    for the default). With DATABASE_URL set, the label is copied onto the gate's published
    promotion_decisions row (page 4), when it has one."""
    from womm.evolve import promotion as pm
    from womm.evolve import rescore as rs

    judge_sv = load_system_version(_sv_path(args.judge_sv), REPO_ROOT)
    store = _holdout_store()
    await store.migrate()
    backends, _, _ = await prepare_backends(judge_sv, skip_self_check=args.skip_self_check)
    try:
        result = await rs.rescore(args.gate_id, store=store, judge_sv=judge_sv,
                                  backends=backends, replace=args.replace)  # fmt: skip
    except rs.RescoreRefused as exc:
        raise UsageError(str(exc)) from None
    except rs.RescoreStopped as exc:
        info(str(exc))
        return EXIT_FAILED
    data, text = result.model_dump(mode="json"), rs.format_rescore(result)
    # Page 4: the label goes onto the gate's published summary row, when there is one. The
    # re-score is already saved in the holdout audit, so a failure here loses nothing.
    if not load_settings().database_url:
        data["page4"] = None
        emit(args, data, f"{text}\npage 4: not updated (no DATABASE_URL)")
        return EXIT_OK
    summary = rs.cross_judge_summary(result, dt.date.today().isoformat())
    try:
        async with _evolve_db("womm evolve rescore") as db:
            published = await pm.record_cross_judge(db, args.gate_id, summary)
    except Exception as exc:
        data["page4"] = None
        emit(args, data, text)
        info(f"error: the re-score is recorded in the holdout audit, but page 4's "
             f"promotion_decisions.cross_judge for gate {args.gate_id} was not written: {exc}; "
             f"set it by hand from `womm evolve show <candidate>`")  # fmt: skip
        return EXIT_FAILED
    data["page4"] = published
    text += (f"\npage 4: the cross-judge label is on gate {args.gate_id}'s published summary"
             if published else f"\npage 4: no published summary for gate {args.gate_id} "
             "(publish_summary off, or its summary insert failed: see `womm evolve show`): nothing "
             "updated")  # fmt: skip
    emit(args, data, text)
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
            f"wrote {summary['out']}: host it with `womm calibrate upload` (or send each "
            f"annotator sheet_<name>.md and answers_<name>.yaml); "
            f"keep {jc.KEY_FILE} private")  # fmt: skip
    emit(args, summary, text)
    return EXIT_OK


async def cmd_calibrate_hosted(args: argparse.Namespace) -> int:
    """Host the sample's pages on the app (upload) or write its answers back as files (fetch)."""
    import httpx

    from womm.eval import hosted_labels as hl
    from womm.eval import judge_calibration as jc

    directory = Path(args.dir)
    try:
        if args.calibrate_cmd == "upload":
            link = hl.upload(directory, args.url)
            emit(args, {"link": link}, f"hosted. Send this link to the annotators:\n  {link}")
            return EXIT_OK
        key = json.loads((directory / jc.KEY_FILE).read_text(encoding="utf-8"))
        got = hl.fetch(directory, args.url)
        jc.write_answer_files(directory, list(key["pairs"]), got)
    except FileNotFoundError:
        raise UsageError(f"no {jc.KEY_FILE} in {directory}: run `womm calibrate sample`") from None
    except (hl.HostedLabelsError, jc.CalibrationError, httpx.HTTPError) as exc:
        raise UsageError(str(exc)) from None
    lines, pending = hl.progress(got)
    if pending:
        lines.append(f"waiting for {', '.join(pending)}; score once everyone has submitted")
    emit(args, {"annotators": got, "pending": pending}, "\n".join(lines))
    return EXIT_OK


async def cmd_calibrate_score(args: argparse.Namespace) -> int:
    """Agreement of the coverage judge with the annotators; --record appends the aggregate."""
    from womm.eval import judge_calibration as jc
    from womm.eval import review_page
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
    if result["agreement"] is not None and not jc.record_problems(result):
        record = pm.JudgeCalibration(**jc.calibration_record(result, "check"))
        if problems := pm.calibration_problems(record):
            text += "\ndoes not count for promotion: " + "; ".join(problems)
    page = review_page.calibration(directory, key, result, answers, jc.read_notes(directory),
                                   minimum=pm.CALIBRATION_MIN)  # fmt: skip
    text += f"\nreview page (private, keep it local): {page}"
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


async def cmd_policy_sign(args: argparse.Namespace) -> int:
    """Sign the promotion policy (pre-registration): set signed_by and signed_on, keeping the
    file's comments. The owner then commits it; the gate reads only a committed file."""
    import re

    from womm.evolve import promotion as pm

    path = Path(args.policy)
    try:
        policy, _ = pm.load_policy(path)
    except pm.GateRefused as exc:
        raise UsageError(str(exc)) from None
    if policy.signed:
        raise UsageError(f"already signed by {policy.signed_by} on {policy.signed_on}; a signed "
                         "policy is never changed")  # fmt: skip
    name = args.by.strip()
    if not name:
        raise UsageError("--by needs your name")
    on = dt.date.today().isoformat()
    if policy.changelog[-1].date != on:
        raise UsageError(f"the last changelog entry is dated {policy.changelog[-1].date}: add an "
                         f"entry dated {on} saying what you sign and why, then sign")  # fmt: skip
    text = path.read_text(encoding="utf-8")
    text, n_by = re.subn(r"^signed_by:.*$", f"signed_by: {json.dumps(name)}", text, count=1,
                         flags=re.M)  # fmt: skip
    text, n_on = re.subn(r"^signed_on:.*$", f'signed_on: "{on}"', text, count=1, flags=re.M)
    if n_by != 1 or n_on != 1:
        raise UsageError(f"{path} has no signed_by / signed_on lines to fill in")
    path.write_text(text, encoding="utf-8")
    pm.load_policy(path)  # still valid
    guards = ", ".join(f"{k} {v.model_dump(exclude_none=True)}" for k, v in policy.guards.items())
    shown = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    summary = (f"signed {path} as {name} on {on}. What you signed:\n"
               f"  primary metric: {policy.primary_metric}; guards: {guards}\n"
               f"  formal mode: {policy.formal_mode}; expected gain: {policy.expected_gain}; "
               f"weak bar: gain above {policy.weak_min_delta_se:g} SE; impact count may drop at "
               f"most {policy.impact_count_guard.max_drop_ratio:.0%}; "
               f"repetitions: {policy.repetitions.min} to {policy.repetitions.max} "
               "(extended on the pooled noise only)\n"
               f"  changelog: {len(policy.changelog)} entries, last "
               f"{policy.changelog[-1].date}\n"
               f"  holdout budget: {policy.holdout_budget.per_cycle} per cycle, "
               f"{policy.holdout_budget.total} in total; "
               f"publish summary: {policy.publish_summary}\n"
               f"commit it now: git commit {shown}")  # fmt: skip
    emit(args, {"signed_by": name, "signed_on": on}, summary)
    return EXIT_OK


async def cmd_noise_report(args: argparse.Namespace) -> int:
    """R34 run-to-run noise and the holdout MDD. One report is a first run (the pre-registered
    stop rule says stop or extend) or a fallback run of the maximum; two reports are a first
    run and its extension, pooled. --record appends every formal attempt, and the noise run and
    MDD records once the chain is final."""
    from womm.eval import noise_report as nr
    from womm.eval.run_eval import read_report
    from womm.evolve import promotion as pm

    if len(args.report) > 2:
        raise UsageError("at most two reports: a first R34 run and its extension")
    paths = [Path(p) for p in args.report]
    reports = [read_report(p) for p in paths]
    keys = [nr.run_key(rep, path) for rep, path in zip(reports, paths, strict=True)]
    try:
        policy, policy_sha = pm.load_policy(Path(args.policy))
    except pm.GateRefused as exc:
        raise UsageError(str(exc)) from None
    repetitions = args.repetitions or policy.repetitions.min
    shift = None
    try:
        report = nr.pool_reports(reports) if len(reports) == 2 else reports[0]
        if len(reports) == 2:
            shift = nr.batch_shift(reports[0], reports[1])
        result = nr.noise_report(
            report, repetitions=repetitions, n_cases=args.holdout_cases,
            n_proposals=args.holdout_proposals, icc=args.icc, alpha=args.alpha, power=args.power,
        )  # fmt: skip
    except ValueError as exc:
        raise UsageError(str(exc)) from None
    run = result["noise_run"]
    inputs = {"policy_sha256": policy_sha, "expected_gain": policy.expected_gain,
              "repetitions_min": policy.repetitions.min, "holdout_cases": args.holdout_cases,
              "holdout_proposals": args.holdout_proposals, "icc": args.icc, "alpha": args.alpha,
              "power": args.power}  # fmt: skip
    records = pm.load_records(Path(args.records)) if Path(args.records).exists() else None
    chain = records.noise_chain(run["judge_version"]) if records and run["judge_version"] else []
    first = next((a for a in chain if a.kind == "first"), None)
    if len(reports) == 2:
        kind = "extension"
    elif (run["repetitions"] or 0) >= nr.R34_MAX_REPETITIONS and first is not None:
        kind = "fallback"
    else:
        kind = "first"
    coverage = result["metrics"]["coverage"]
    if kind == "first" and (run["repetitions"] or 0) < nr.R34_MAX_REPETITIONS:
        if coverage["noise_sd"] is None:
            raise UsageError("no coverage noise: the stop rule needs two scored runs of a case")
        star = nr.sd_star(policy.expected_gain, repetitions=policy.repetitions.min,
                          n_cases=args.holdout_cases, n_proposals=args.holdout_proposals,
                          icc=args.icc, alpha=args.alpha, power=args.power)  # fmt: skip
        rule = nr.r34_next_step(coverage["noise_sd"], coverage["dof"], star)
    else:
        rule = {"next_step": "final"}
    if args.record or rule["next_step"] == "final":
        result["stop_rule"] = rule  # never previewed: reading it is an attempt
    result["attempt"] = {"kind": kind, "next_step": rule["next_step"]}
    result["batch_shift"] = shift
    result["null_pass_rate"] = nr.null_pass_rate(report["scores"],
                                                 bar_se=policy.weak_min_delta_se)  # fmt: skip
    text = nr.format_report(result, expected_gain=policy.expected_gain)
    aa = result["null_pass_rate"]
    if aa["rate"] is not None:
        text += (f"\nA/A null pass rate of the weak bar: {aa['rate']:.0%} "
                 f"({aa['label']})")  # fmt: skip
    if not args.record:
        result.pop("attempt")
        if run["repetitions"] and run["repetitions"] < nr.R34_MAX_REPETITIONS:
            text += ("\nnote: the stop rule is applied only with --record (every attempt that "
                     "reads it is recorded, so it cannot be dropped and rerun)")  # fmt: skip
    else:
        text += f"\nR34 attempt: {kind}, next step: {rule['next_step']}"
    if repetitions != policy.repetitions.min:
        text += (f"\nnote: {repetitions} repetitions, the policy starts at "
                 f"{policy.repetitions.min}; the gate ignores an MDD report for other "
                 "repetitions")  # fmt: skip
    if not policy.signed:
        text += "\nnote: the promotion policy is not signed yet"
    if args.record:
        if result["formal_problems"]:
            raise UsageError("not recorded: " + "; ".join(result["formal_problems"]))
        if repetitions != policy.repetitions.min:
            raise UsageError(f"not recorded: --repetitions {repetitions} is not the policy's "
                             f"repetitions.min ({policy.repetitions.min})")  # fmt: skip
        _check_r34_chain(kind, chain, first, reports, inputs, keys, policy)
        if records and (
            outside := records.formal_runs_outside_chain(run["judge_version"], also=tuple(keys))
        ):
            raise UsageError(f"formal run(s) {', '.join(outside)} are not part of this R34 "
                             "chain: every logged formal run is counted")  # fmt: skip
        on = dt.date.today().isoformat()
        attempt = {"judge_version": run["judge_version"], "system_version": run["system_version"],
                   "split": run["split"], "experiment": run["experiment"],
                   "git_sha": report["metadata"].get("git_sha"),
                   "repetitions": run["repetitions"], "kind": kind,
                   "next_step": rule["next_step"], "stop_inputs": inputs, "run_keys": keys,
                   "recorded_on": on, "batch_shift": bool(shift and shift["flag"])}  # fmt: skip
        try:
            pm.append_records(Path(args.records), "noise_attempts", [attempt])
            recorded = {"noise_attempts": [attempt]}
            if rule["next_step"] != "extend":
                noise_run, mdds = nr.records_for(result, on)
                noise_run["batch_shift"] = attempt["batch_shift"]
                pm.append_records(Path(args.records), "formal_noise_runs", [noise_run])
                if mdds:
                    pm.append_records(Path(args.records), "mdd_reports", mdds)
                recorded |= {"formal_noise_runs": [noise_run], "mdd_reports": mdds}
        except pm.GateRefused as exc:
            raise UsageError(str(exc)) from None
        result["recorded"] = recorded
        if rule["next_step"] == "extend":
            sha = attempt["git_sha"]
            text += (f"\nrecorded the attempt in {args.records}; commit it, then run 3 more "
                     f"repetitions at git sha {sha} and pass both reports")  # fmt: skip
        else:
            text += (f"\nrecorded 1 noise run and {len(recorded['mdd_reports'])} MDD report(s) "
                     f"in {args.records}; commit it")  # fmt: skip
    emit(args, result, text)
    return EXIT_OK


def _check_r34_chain(kind: str, chain: list, first: Any, reports: list[dict], inputs: dict,
                     keys: list[str], policy: Any) -> None:  # fmt: skip
    """Refuse an attempt that would break the one-chain-per-judge rule (see NoiseAttempt)."""
    from womm.eval import noise_report as nr

    done_extra = any(a.kind in ("extension", "fallback") for a in chain)
    used = {k for a in chain for k in a.run_keys}
    if kind == "first" and first is not None:
        raise UsageError(f"a first R34 attempt already exists for this judge "
                         f"({first.experiment}, {first.recorded_on}): extend it or record a "
                         "fallback run; it is never restarted")  # fmt: skip
    if kind == "first":
        return
    if first is None or first.next_step != "extend":
        raise UsageError(f"an {kind} needs a recorded first attempt that asked to extend")
    if done_extra:
        raise UsageError("this chain already has its extension or fallback")
    new = reports[-1]
    meta = new["metadata"]
    for field in ("system_version", "split"):
        if new.get(field, meta.get(field)) != getattr(first, field):
            raise UsageError(f"the {kind} has another {field} than the first attempt")
    if inputs != first.stop_inputs:
        raise UsageError(f"the stop-rule inputs differ from the first attempt's "
                         f"({first.stop_inputs}); fixed once run 1 is seen")  # fmt: skip
    if problems := nr.formal_problems(new):
        raise UsageError(f"the {kind} is not a clean formal run: " + "; ".join(problems))
    if kind == "extension" and (not meta.get("langsmith_experiment") or keys[1] == keys[0]):
        raise UsageError("the extension needs its own experiment: the two runs must differ")
    if keys[-1] in used:
        raise UsageError(f"the {kind} run {keys[-1]} is already part of the chain")
    if kind == "fallback":
        return
    head = reports[0]["metadata"]
    if keys[0] != first.run_keys[0] or head.get("git_sha") != first.git_sha:
        raise UsageError("the first report is not the recorded first attempt (experiment "
                         "and git sha must match)")  # fmt: skip
    want = policy.repetitions.max - policy.repetitions.min
    if meta.get("repetitions") != want:
        raise UsageError(f"the extension must be {want} repetitions, got "
                         f"{meta.get('repetitions')}")  # fmt: skip


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
        "prior_usd": round(progress.prior_usd, 4),
        "unpriced": progress.unpriced,
        "dev_only": dev_only,
    }
    text = (
        f"cost sweep {sv.version_id} on {plan.version}: {data['records']} records in "
        f"{data['batches']} batches x {args.repetitions} repetitions"
        f"{' (dev-only: ' + plan.role.backend + ' backend)' if dev_only else ''}\n"
        f"calls={progress.calls} skipped={progress.skipped} failed={len(progress.failed)} "
        f"tokens={data['tokens']} cost≈${progress.cost_usd:.3f}"
        + (f" (+${progress.prior_usd:.3f} in earlier runs)" if progress.prior_usd else "")
        + (
            "\nstopped: the backend reports no USD cost, so --max-usd cannot be enforced; "
            "rerun without --max-usd"
            if progress.unpriced and progress.stopped_at_budget
            else "\nstopped at --max-usd; rerun with a higher cap to resume"
            if progress.stopped_at_budget
            else ""
        )
        + (f"\n{len(progress.failed)} batch(es) failed; rerun to retry" if progress.failed else "")
        + f"\nsaved {out}"
    )
    emit(args, data, text)
    return EXIT_FAILED if progress.failed or progress.stopped_at_budget else EXIT_OK


def _run_records(
    paths: list[str], proposal_version: str
) -> tuple[dict[int, list], set[str], str, str]:
    """Cost records of saved runs (the JSON ``womm run`` writes under runs/), one repetition per
    run, plus the union of their scenario keys, the system version and the cost backend. Every
    run must carry cost records of ``proposal_version`` (the text the IA assessed)."""
    from womm.data.fixtures import FixtureError
    from womm.data.fixtures import load_fixture as _fixture

    reps: dict[int, list] = {}
    keys: set[str] = set()
    versions: set[str] = set()
    backends: set[str] = set()
    for n, path in enumerate(paths, 1):
        try:
            run = RunResult.model_validate_json(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise UsageError(f"{path} is not a saved run JSON: {exc}") from None
        if run.dossier is None or run.dossier.costs is None:
            raise UsageError(f"{path}: the run has no cost section (not a cost-enabled version)")
        records = list(run.dossier.costs.records)
        if not records:
            raise UsageError(f"{path}: the run has no cost records to score")
        other = sorted({r.records_version for r in records} - {proposal_version})
        if other:
            raise UsageError(
                f"{path}: cost records of {', '.join(other)}; the IA assessed "
                f"{proposal_version}, so only runs on the proposal are scored"
            )
        try:
            scenario = _fixture().scenario(run.scenario_id)
        except FixtureError as exc:
            raise UsageError(f"{path}: {exc}") from None
        reps[n] = records
        keys |= {r.provision_key for r in records}
        keys |= set(scenario.provision_keys)
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
        reps, restrict, sv_id, backend = _run_records(args.runs, ref.proposal_version)
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
        help="the R34 noise run: one --split, >= 3 repetitions (the stop rule may ask for 3 more), "
        "api backend only, clean tree",
    )
    p_eval.add_argument("--local", action="store_true", help="do not record in LangSmith")
    p_eval.add_argument("--records", default=None, help=argparse.SUPPRESS)
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
    p_rescore = evolve.add_parser(
        "rescore", parents=[common],
        help="re-score a gate comparison with a cross-family judge (report-only; local; needs "
        "HOLDOUT_DATABASE_URL)",
    )  # fmt: skip
    p_rescore.add_argument("gate_id")
    p_rescore.add_argument("--judge-sv", default="system_versions/v1.0-unscoped.yaml",
                           help="the system version whose coverage judge re-scores")  # fmt: skip
    p_rescore.add_argument("--replace", action="store_true",
                           help="supersede the gate's current re-score")  # fmt: skip
    p_rescore.add_argument("--skip-self-check", action="store_true", help="dev only")
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
                          help="annotator's name, what they sign in with (case does not "
                               "matter); one sheet each (repeatable)")  # fmt: skip
    p_sample.add_argument("--out", required=True, help="e.g. .cache/calibration/<name>/")
    p_sample.add_argument("--golden-dir", default=str(GOLDEN_DIR), help=argparse.SUPPRESS)
    for name, text in (("upload", "host the labelling pages on the deployed app"),
                       ("fetch", "write the hosted answers back as answer files")):  # fmt: skip
        p_host = cal.add_parser(name, parents=[common], help=text)
        p_host.add_argument("--dir", required=True, help="the `calibrate sample --out` directory")
        p_host.add_argument(
            "--url", required=True, help="the app, e.g. https://<app>.up.railway.app"
        )
    p_score = cal.add_parser("score", parents=[common], help="judge agreement with annotators")
    p_score.add_argument("--dir", required=True, help="the `calibrate sample --out` directory")
    p_score.add_argument("--record", action="store_true",
                         help="append a judge_calibrations record (aggregates only)")  # fmt: skip
    p_score.add_argument("--records", default=str(RECORDS_PATH), help=argparse.SUPPRESS)
    p_pol = sub.add_parser("policy", help="the pre-registered promotion policy")
    pol = p_pol.add_subparsers(dest="policy_cmd", required=True)
    p_sign = pol.add_parser("sign", parents=[common],
                            help="sign the promotion policy with its current values")  # fmt: skip
    p_sign.add_argument("--by", required=True, help="your name")
    p_sign.add_argument("--policy", default=str(POLICY_PATH), help=argparse.SUPPRESS)
    p_noise = sub.add_parser("noise", help="R34 noise and minimum detectable delta")
    noise = p_noise.add_subparsers(dest="noise_cmd", required=True)
    p_nrep = noise.add_parser("report", parents=[common],
                              help="noise SD and holdout MDD from a formal noise run")  # fmt: skip
    p_nrep.add_argument("--report", required=True, action="append",
                        help="the R34 formal eval report; give it twice (first run, then its "
                        "extension) to pool them")  # fmt: skip
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
    p_sweep.add_argument(
        "--max-usd", type=float,
        help="hard cap on the sweep directory's USD spend (earlier runs included); a call starts "
        "only if it and the calls in flight fit; refused for backends that report no USD cost",
    )  # fmt: skip
    p_sweep.add_argument("--skip-self-check", action="store_true", help="dev only")
    p_check = cost.add_parser("check", parents=[common],
                              help="R6: score cost records against SWD(2021) 84")  # fmt: skip
    source = p_check.add_mutually_exclusive_group(required=True)
    source.add_argument("--sweep", help="a `womm cost sweep` directory of the proposal")
    source.add_argument(
        "--runs", nargs="+",
        help="saved run JSON files (womm run output) of a cost-enabled version on the proposal",
    )  # fmt: skip
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
    "rescore": cmd_evolve_rescore,
}


CALIBRATE_HANDLERS = {"sample": cmd_calibrate_sample, "score": cmd_calibrate_score,
                      "upload": cmd_calibrate_hosted, "fetch": cmd_calibrate_hosted}  # fmt: skip
NOISE_HANDLERS = {"report": cmd_noise_report}
COST_HANDLERS = {
    "sweep": cmd_cost_sweep,
    "check": cmd_cost_check,
    "late-added": cmd_cost_late_added,
}
POLICY_HANDLERS = {"sign": cmd_policy_sign}
GROUPS = {
    "policy": ("policy_cmd", POLICY_HANDLERS),
    "evolve": ("evolve_cmd", EVOLVE_HANDLERS),
    "calibrate": ("calibrate_cmd", CALIBRATE_HANDLERS),
    "noise": ("noise_cmd", NOISE_HANDLERS),
    "cost": ("cost_cmd", COST_HANDLERS),
}


# `womm evolve` subcommands allowed to run with the holdout URL set: only the promotion gate
# (U7), the cross-family judge re-score of its comparisons and the local reader of its audit.
# Every other evolve command is Improvement-Planner side.
HOLDOUT_SIDE_EVOLVE = frozenset({"promote", "show", "rescore"})


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
