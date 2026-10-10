"""Score each golden case's own reference answer with the coverage judge (womm.eval.oracle_check).

    uv run python scripts/oracle_check.py                                  # published train/val
    uv run python scripts/oracle_check.py evals/golden/drafts/case_12_*.yaml   # drafts only

A sanity check of the cases and the judge, not of the system: the dossier scored is built from
the case itself (one impact per expected impact, one per important omission), so every expected
impact should be judged covered and every omission addressed. Each miss is printed with the
judge's justification; it points at a broken item or a miscalibrated judge. One judge call per
case, with the judge role, backend and prompt of the system version (default: the one in
WOMM_SYSTEM_VERSION or the repository default), as ``womm eval`` uses them.

Without paths, every published train/val case in evals/golden/ is checked. With paths, only those
draft files are checked, as the case they would become (dropped items left out, pending items
kept, accepted candidates added), so a draft can be checked before it is published. Holdout
cases are refused: a holdout draft, and any case whose fixture is registered as holdout in the
local registry.

Exit codes: 0 every case passed (coverage 1.0 and omissions 1.0), 1 at least one case failed or
could not be judged, 2 bad input (unreadable or invalid case, draft or system version, holdout).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from langsmith import tracing_context
from pydantic import ValidationError

from womm.config import REPO_ROOT, ConfigError, load_settings
from womm.eval.golden import GOLDEN_DIR, GoldenCase, GoldenError, load_all_golden
from womm.eval.golden_review import (
    HOLDOUT_REGISTRY,
    ReviewError,
    load_draft_for_review,
    refuse_holdout_fixture,
)
from womm.eval.oracle_check import OracleResult, case_from_draft, check_case
from womm.llm.base import LLMBackend, LLMError, get_backend
from womm.llm.claude_code import ClaudeCodeBackend
from womm.models.system_version import RoleConfig, load_system_version

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def load_cases(args: argparse.Namespace) -> list[GoldenCase]:
    if args.drafts:
        cases = [case_from_draft(load_draft_for_review(p)) for p in args.drafts]
    else:
        cases = load_all_golden(args.golden_dir)
    for case in cases:
        refuse_holdout_fixture(case.fixture, "an oracle check", args.holdout_registry)
    if not cases:
        raise GoldenError("no golden cases to check")
    return cases


async def judge_backend(role: RoleConfig, skip_self_check: bool) -> LLMBackend:
    """Only the judge's backend; claude_code passes its isolation self-check first."""
    if role.backend != "claude_code":
        return get_backend(role.backend, load_settings())
    cc = ClaudeCodeBackend(max_concurrency=1)
    if not skip_self_check:
        with tracing_context(enabled=False):
            report = await cc.self_check(role.model)
        print(f"claude_code isolation self-check passed ({report.cli_version})", file=sys.stderr)
    return cc


def describe(r: OracleResult) -> list[str]:
    def fmt(x: float | None) -> str:
        return "-" if x is None else f"{x:.2f}"

    head = f"{'PASS' if r.passed else 'FAIL'} {r.case_id}: coverage={fmt(r.coverage)} "
    head += f"omissions={fmt(r.omissions)}" + (f" error={r.error}" if r.error else "")
    lines = [head]
    lines += [f"  not covered {i}: {why}" for i, why in r.uncovered.items()]
    lines += [f"  not addressed {i}: {why}" for i, why in r.unaddressed.items()]
    return lines


async def run(args: argparse.Namespace) -> int:
    cases = load_cases(args)
    sv = load_system_version(args.system_version, REPO_ROOT)
    role = sv.spec.judge
    prompt = sv.prompt_text(role)
    backend = await judge_backend(role, args.skip_self_check)
    print(f"judge {role.backend}:{role.model} ({sv.version_id}), {len(cases)} case(s)")
    failed = 0
    for case in cases:
        result = await check_case(case, backend, role, prompt)
        failed += not result.passed
        print("\n".join(describe(result)), flush=True)
    print(f"{len(cases) - failed}/{len(cases)} case(s) passed")
    return EXIT_FAILED if failed else EXIT_OK


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("drafts", nargs="*", type=Path, help="train/val draft files (optional)")
    parser.add_argument(
        "--system-version", type=Path, default=None, help="system version file (judge role)"
    )
    parser.add_argument("--skip-self-check", action="store_true")
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR, help=argparse.SUPPRESS)
    parser.add_argument(
        "--holdout-registry", type=Path, default=HOLDOUT_REGISTRY, help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    if args.system_version is None:
        args.system_version = load_settings().system_version_path
    return args


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    try:
        return asyncio.run(run(args))
    except (GoldenError, ReviewError, ValidationError, ConfigError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except LLMError as exc:
        print(f"error: judge backend: {exc}", file=sys.stderr)
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())
