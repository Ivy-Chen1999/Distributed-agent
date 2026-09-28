"""WOMM command line: `womm run <scenario>`, `womm selfcheck`, `womm scenarios`."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from womm.config import REPO_ROOT, load_settings
from womm.data.fixtures import load_fixture
from womm.decisions.stub import StubDecisionService
from womm.eval.golden import load_all_golden
from womm.eval.run_eval import (
    BaselineRefused,
    evaluate_cases,
    record_langsmith_experiment,
    write_report,
)
from womm.graph.build import run_scenario
from womm.identity import code_identity
from womm.llm.base import LLMBackend, get_backend
from womm.llm.claude_code import ClaudeCodeBackend
from womm.models.run import RunResult
from womm.models.system_version import SystemVersion, load_system_version

RUNS_DIR = REPO_ROOT / "runs"


async def prepare_backends(
    sv: SystemVersion, *, skip_self_check: bool = False
) -> tuple[dict[str, LLMBackend], str | None]:
    """Instantiate each backend the version uses. claude_code must pass its isolation
    self-check first (fail closed)."""
    settings = load_settings()
    roles = sv.spec.roles().values()
    backends: dict[str, LLMBackend] = {}
    cli_version = None
    for name in sorted({r.backend for r in roles}):
        if name == "claude_code":
            cc = ClaudeCodeBackend(max_concurrency=sv.spec.max_parallel_llm_calls)
            cli_version = await cc.cli_version()
            if not skip_self_check:
                model = next(r.model for r in roles if r.backend == "claude_code")
                report = await cc.self_check(model)
                print(f"claude_code isolation self-check passed ({report.cli_version})")
            backends[name] = cc
        else:
            backends[name] = get_backend(name, settings)
    return backends, cli_version


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
            lines.append(
                f"  {i.impact_id}: {i.summary}  [{', '.join(f.agent for f in i.findings)}]"
            )
        lines.extend(f"  note: {n}" for n in d.notes)
    tokens = sum(u.input_tokens + u.output_tokens for u in result.usage)
    cost = sum(u.cost_usd or 0 for u in result.usage)
    lines.append(f"llm calls={len(result.usage)}  tokens={tokens}  cost≈${cost:.3f}")
    return "\n".join(lines)


async def cmd_run(args: argparse.Namespace) -> int:
    sv = load_system_version(Path(args.system_version), REPO_ROOT)
    fixture = load_fixture()
    backends, cli_version = await prepare_backends(sv, skip_self_check=args.skip_self_check)
    result = await run_scenario(
        args.scenario,
        sv=sv,
        fixture=fixture,
        backends=backends,
        decisions=StubDecisionService(),
        code_identity=code_identity(cli_version),
    )
    RUNS_DIR.mkdir(exist_ok=True)
    out = RUNS_DIR / f"{result.run_id}.json"
    out.write_text(result.model_dump_json(indent=2))
    print(summarize(result))
    print(f"saved {out.relative_to(REPO_ROOT)}")
    if os.environ.get("LANGSMITH_TRACING", "").lower() == "true":
        print(
            f"LangSmith: project {load_settings().langsmith_project}, run_name womm:{args.scenario}"
        )
    return 0 if result.status.value in ("succeeded", "degraded", "no_changes") else 1


async def cmd_selfcheck(args: argparse.Namespace) -> int:
    sv = load_system_version(Path(args.system_version), REPO_ROOT)
    await prepare_backends(sv)
    return 0


async def cmd_eval(args: argparse.Namespace) -> int:
    sv = load_system_version(Path(args.system_version), REPO_ROOT)
    cases = load_all_golden()
    if args.case:
        cases = [c for c in cases if c.case_id in args.case]
        if not cases:
            print(f"no golden case matches {args.case}")
            return 2
    backends, cli_version = await prepare_backends(sv, skip_self_check=args.skip_self_check)
    try:
        report = await evaluate_cases(
            cases,
            sv=sv,
            fixture=load_fixture(),
            backends=backends,
            decisions=StubDecisionService(),
            code=code_identity(cli_version),
            judge_prompt=sv.prompt_text(sv.spec.judge),
            repetitions=args.repetitions,
            baseline=args.baseline,
            runs_dir=RUNS_DIR,
        )
    except BaselineRefused as exc:
        print(exc)
        return 2
    path = write_report(report, RUNS_DIR)
    print(json.dumps(report.summary, indent=2) if report.summary else f"ABORTED: {report.aborted}")
    for s in report.scores:
        print(
            f"  {s.case_id}: {s.outcome} coverage={s.coverage} "
            f"omissions={s.omissions_addressed} grounding={s.grounding}"
            + (f" error={s.error or s.judge_error}" if (s.error or s.judge_error) else "")
        )
    print(f"report {path.relative_to(REPO_ROOT)}")
    if not args.local and load_settings().langsmith_api_key:
        from langsmith import Client

        name = await record_langsmith_experiment(
            report, load_all_golden(), Client(), prefix=f"womm-{sv.spec.name}"
        )
        print(f"LangSmith experiment: {name}")
    return 0 if report.summary else 1


def cmd_scenarios(_: argparse.Namespace) -> int:
    for s in load_fixture().scenarios.values():
        print(f"{s.scenario_id:34} {s.kind:10} {len(s.provision_keys):2} provisions")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(REPO_ROOT / ".env", override=False)
    settings = load_settings()
    parser = argparse.ArgumentParser(prog="womm")
    parser.add_argument("--system-version", default=str(settings.system_version_path))
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="run one scenario through the RIA pipeline")
    p_run.add_argument("scenario")
    p_run.add_argument("--skip-self-check", action="store_true", help="dev only")
    p_eval = sub.add_parser("eval", help="run golden cases and score them")
    p_eval.add_argument("--case", action="append", help="case_id to run (repeatable)")
    p_eval.add_argument("--repetitions", type=int, default=1)
    p_eval.add_argument("--baseline", action="store_true", help="tag as a baseline experiment")
    p_eval.add_argument("--local", action="store_true", help="do not record in LangSmith")
    p_eval.add_argument("--skip-self-check", action="store_true", help="dev only")
    sub.add_parser("selfcheck", help="verify backend auth and claude_code isolation")
    sub.add_parser("scenarios", help="list fixture scenarios")
    args = parser.parse_args(argv)

    if args.cmd == "scenarios":
        return cmd_scenarios(args)
    handler = {"run": cmd_run, "eval": cmd_eval, "selfcheck": cmd_selfcheck}[args.cmd]
    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())
