"""WOMM command line.

Data goes to stdout (text, or JSON with --json); diagnostics and errors go to stderr.
Exit codes are listed in EXIT_CODES_HELP.
"""

from __future__ import annotations

import argparse
import asyncio
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
from womm.eval.golden import GoldenError, load_all_golden
from womm.eval.run_eval import (
    BaselineRefused,
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
  womm eval --baseline
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
    settings = load_settings()
    decisions = make_decision_service(sv, settings)  # config errors before LLM spend
    backends, cli_version, _ = await prepare_backends(sv, skip_self_check=args.skip_self_check)
    report = await evaluate_cases(
        cases,
        sv=sv,
        fixture=load_fixture(),
        backends=backends,
        decisions=decisions,
        code=code_identity(cli_version),
        judge_prompt=sv.prompt_text(sv.spec.judge),
        repetitions=args.repetitions,
        baseline=args.baseline,
        runs_dir=Path(args.runs_dir),
    )
    # The report is written first, so a failing side effect below can never lose the results.
    path = write_report(report, Path(args.runs_dir))
    if not args.local and settings.langsmith_api_key:
        try:
            from langsmith import Client

            name = await record_langsmith_experiment(
                report, cases, Client(), prefix=f"womm-{sv.spec.name}"
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
        f"{s.scenario_id:34} {s.kind:10} {len(s.provision_keys):2} provisions" for s in scenarios
    )
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
    p_eval.add_argument("--repetitions", type=int, default=1, help="runs per case (noise)")
    p_eval.add_argument("--baseline", action="store_true", help="tag as a baseline experiment")
    p_eval.add_argument("--local", action="store_true", help="do not record in LangSmith")
    p_eval.add_argument("--skip-self-check", action="store_true", help="dev only")
    p_eval.add_argument("--decider", choices=["jev", "stub"], help="override the router decider")
    sub.add_parser(
        "selfcheck", parents=[common], help="verify backend auth and claude_code isolation"
    )
    sub.add_parser("scenarios", parents=[common], help="list fixture scenarios")
    return parser


HANDLERS = {
    "run": cmd_run,
    "eval": cmd_eval,
    "selfcheck": cmd_selfcheck,
    "scenarios": cmd_scenarios,
}


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(HANDLERS[args.cmd](args))
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
