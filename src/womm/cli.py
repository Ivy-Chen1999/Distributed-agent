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
    return parser


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
    "replay": cmd_evolve_replay,
    "worker": cmd_evolve_worker,
    "cycle": cmd_evolve_cycle,
    "diffcheck": cmd_evolve_diffcheck,
    "promote": cmd_evolve_promote,
    "show": cmd_evolve_show,
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
    handler = EVOLVE_HANDLERS[args.evolve_cmd] if args.cmd == "evolve" else HANDLERS[args.cmd]
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
