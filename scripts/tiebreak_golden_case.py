"""Settle the open items of train/val golden drafts with the tie-break judge (womm.eval.tiebreak).

    uv run python scripts/tiebreak_golden_case.py evals/golden/drafts/case_12_*.yaml

For the case owner, who has the IA cache (.cache/ia/<fixture>/). One LLM call per draft (role
``tiebreak`` in evals/drafting.yaml) rules keep / drop / unsure on every item the three judges
left open and on every ``possibly_missing`` candidate; the draft is rewritten with the rulings and
a 20% audit sample of the kept items, which a person then checks. New drafts get this from
scripts/draft_golden_case.py. A draft already tie-broken, a holdout draft and a draft of a
proposal registered as holdout are refused.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from langsmith import tracing_context

from womm.data.fixtures import fixture_dir, load_fixture
from womm.eval import ia_sources
from womm.eval.drafting import (
    DEFAULT_CONFIG,
    DraftingError,
    load_drafting_config,
    write_draft,
)
from womm.eval.golden_review import (
    HOLDOUT_REGISTRY,
    ReviewError,
    human_reasons,
    load_draft_for_review,
    refuse_holdout_fixture,
)
from womm.eval.tiebreak import needs_tiebreak, tiebreak_draft
from womm.llm.base import LLMBackend, LLMError, get_backend
from womm.llm.claude_code import ClaudeCodeBackend


async def backends_for(config, skip_self_check: bool) -> dict[str, LLMBackend]:
    role = config.roles.tiebreak
    if role is None:
        raise DraftingError(f"no tiebreak role in {DEFAULT_CONFIG.name}")
    if role.backend != "claude_code":
        return {role.backend: get_backend(role.backend)}
    cc = ClaudeCodeBackend(max_concurrency=1)
    if not skip_self_check:
        with tracing_context(enabled=False):
            report = await cc.self_check(role.model)
        print(f"claude_code isolation self-check passed ({report.cli_version})", file=sys.stderr)
    return {"claude_code": cc}


async def run(args: argparse.Namespace) -> int:
    config = load_drafting_config(args.config)
    backends = None
    for path in args.drafts:
        draft = load_draft_for_review(path)
        refuse_holdout_fixture(draft.fixture, "a tie-break", args.holdout_registry)
        open_items = [i.item_id for i in draft.items() if needs_tiebreak(i)]
        cached = ia_sources.load_cached_ia(draft.fixture, args.ia_root)
        if cached is None:
            raise DraftingError(f"{path.name}: no IA cache for {draft.fixture}; draft it first")
        if args.dry_run:
            print(f"{path.name}: {len(open_items)} open items: {', '.join(open_items)}")
            continue
        backends = backends or await backends_for(config, args.skip_self_check)
        fixture = load_fixture(fixture_dir(draft.fixture))
        ruled = await tiebreak_draft(draft, fixture, cached.full_text, config, backends)
        write_draft(ruled, path.parent)
        s = ruled.stats
        people = [i.item_id for i in ruled.items() if human_reasons(i)]
        print(
            f"{path.name}: {len(open_items)} ruled (kept {s.llm_kept}, dropped {s.llm_dropped}); "
            f"for people: {len(people)} audit items {people}"
        )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("drafts", nargs="+", type=Path, help="train/val draft files")
    parser.add_argument("--dry-run", action="store_true", help="list the open items only")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--skip-self-check", action="store_true")
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--holdout-registry", type=Path, default=HOLDOUT_REGISTRY, help=argparse.SUPPRESS
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except (DraftingError, ReviewError, LLMError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
