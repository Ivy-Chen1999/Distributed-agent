"""Draft a golden case from a proposal's impact assessment with an LLM, for human review (U4).

    uv run python scripts/draft_golden_case.py --fixture data_act --case case_03_x \\
        --scenario eval_x --split train --ia-section "6.2.3." --ia-section "Annex 3"

The IA is fetched (or read from cache) into .cache/ia/<fixture>/ using the identifiers in the
gitignored evals/private/ia_index.yaml; ``--ia-section`` selects headings of its impact-relevant
cut (default: the whole cut). Roles, models and prompts come from evals/drafting.yaml.

Train/val drafts are written to evals/golden/drafts/<case>.yaml for PR review. Holdout drafts
go only under .cache/ (gitignored; default .cache/drafts/); any other destination is refused,
and a holdout run executes entirely with LangSmith tracing disabled. Holdout scenarios are not
in public fixtures, so a holdout case passes ``--articles`` instead of ``--scenario``. A train or
val draft for a proposal registered as holdout in the gitignored local registry
(evals/private/holdout_scenarios.yaml) is refused.

The audit sample (20%, pinned) is drawn with a seed derived from the case id; it cannot be set
here. ``--drafted-by`` (default: ``git config github.user``, else ``git config user.name``) is
recorded in the draft; the review gate refuses the drafter as a reviewer.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path

from langsmith import tracing_context

from womm.data.fixtures import FixtureError, fixture_dir, load_fixture
from womm.data.ia_index import IA_INDEX_PATH, load_ia_index
from womm.eval import ia_sources
from womm.eval.drafting import (
    DEFAULT_CONFIG,
    DraftingConfig,
    DraftingError,
    DraftInputs,
    draft_case,
    draft_path,
    load_drafting_config,
    write_draft,
)
from womm.eval.golden_review import HOLDOUT_REGISTRY, ReviewError, refuse_holdout_fixture
from womm.llm.base import LLMBackend, LLMError, get_backend
from womm.llm.claude_code import ClaudeCodeBackend
from womm.models.regulation import Scenario


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


async def prepare_backends(
    config: DraftingConfig, *, skip_self_check: bool = False
) -> dict[str, LLMBackend]:
    """One backend per name; claude_code must pass its isolation self-check (fail closed)."""
    roles = config.roles.as_dict().values()
    backends: dict[str, LLMBackend] = {}
    for name in sorted({r.backend for r in roles}):
        if name == "claude_code":
            cc = ClaudeCodeBackend(max_concurrency=config.max_parallel_llm_calls)
            if not skip_self_check:
                model = next(r.model for r in roles if r.backend == "claude_code")
                with tracing_context(enabled=False):
                    report = await cc.self_check(model)
                info(f"claude_code isolation self-check passed ({report.cli_version})")
            backends[name] = cc
        else:
            backends[name] = get_backend(name)
    return backends


def scenario_for(args: argparse.Namespace, fixture) -> Scenario:
    if args.scenario:
        return fixture.scenario(args.scenario)
    version = fixture.regulation.versions[-1]
    by_article = {p.article: p.provision_key for p in version.provisions}
    missing = [a for a in args.articles if a not in by_article]
    if missing:
        raise DraftingError(f"articles {missing} are not in fixture {args.fixture}")
    return Scenario(
        scenario_id=f"eval_{args.case.split('_', 2)[-1]}",
        kind="evaluation",
        description=f"Articles {', '.join(args.articles)} (local holdout scenario)",
        before_version=None,
        after_version=version.version_id,
        provision_keys=[by_article[a] for a in args.articles],
    )


def git_identity() -> str | None:
    """The drafter's handle: ``git config github.user``, else ``git config user.name``."""
    for key in ("github.user", "user.name"):
        try:
            out = subprocess.run(
                ["git", "config", "--get", key], capture_output=True, text=True, check=False
            ).stdout.strip()
        except OSError:
            return None
        if out:
            return out
    return None


async def run(args: argparse.Namespace, backends: dict[str, LLMBackend] | None = None) -> Path:
    draft_path(args.case, args.split, args.out_dir)  # refuse a bad destination before any work
    if args.split != "holdout":
        refuse_holdout_fixture(args.fixture, f"{args.split} drafting", args.holdout_registry)
    record = load_ia_index(args.ia_index).get(args.fixture)
    if record is None or not record.ia_celex:
        raise DraftingError(
            f"no IA for fixture {args.fixture!r} in {args.ia_index}; import it with "
            "scripts/import_proposal.py --ia-celex ..."
        )
    cached = None if args.refresh else ia_sources.load_cached_ia(args.fixture, args.ia_root)
    if cached is None:
        info(f"fetching the IA for {args.fixture} into .cache/ia/{args.fixture}/")
        ia_sources.cache_ia(
            args.fixture, record.ia_celex, record.rsb_ref, root=args.ia_root, refresh=args.refresh
        )
        cached = ia_sources.load_cached_ia(args.fixture, args.ia_root)
    extract = ia_sources.reparse_cached(args.fixture, args.ia_root)
    if args.ia_section:
        sections = ia_sources.select_sections(extract, args.ia_section)
    else:
        sections = [s for s in extract.sections if s.kind != "procedural"]
    fixture = load_fixture(fixture_dir(args.fixture))
    scenario = scenario_for(args, fixture)
    config = load_drafting_config(args.config)
    titles = [s.title for s in sections]
    inputs = DraftInputs(
        case_id=args.case,
        split=args.split,
        fixture=fixture,
        scenario=scenario,
        ia_full_text=cached.full_text,
        ia_sections_text="\n\n".join(f"### {s.title}\n\n{s.text}" for s in sections),
        ia_section_titles=titles,
        rsb_text=cached.rsb_text,
        rsb_status=cached.rsb_status,
        ia_reference=(
            f"Impact assessment accompanying the {fixture.regulation.title} proposal "
            f"(identifiers in the local IA index); sections: {'; '.join(titles)}"
        ),
        notes=args.notes or "",
        ia_record=record,
        drafted_by=args.drafted_by or git_identity(),
    )
    if backends is None:
        backends = await prepare_backends(config, skip_self_check=args.skip_self_check)
    draft = await draft_case(inputs, config, backends)
    path = write_draft(draft, args.out_dir)
    s = draft.stats
    print(f"{args.case} ({args.split}) -> {path}")
    print(
        f"items: {s.items} (impacts {s.expected_impacts}, omissions {s.important_omissions}, "
        f"possibly missing {s.possibly_missing}); anchors verified {s.anchors_verified}/{s.items}"
    )
    print(
        f"judge: agree {s.judge_agree}, disagree {s.judge_disagree}, uncertain "
        f"{s.judge_uncertain}; auto-accepted {s.auto_accepted}, pending {s.pending}, "
        f"audit sample {s.audited} (seed {draft.provenance.audit.seed}); human decisions "
        f"needed {s.human_decisions_needed}"
    )
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", required=True, help="fixture id under data/fixtures/")
    parser.add_argument("--case", required=True, help="case id, e.g. case_03_data_act_cloud")
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--scenario", help="evaluation scenario id in the fixture")
    where.add_argument("--articles", nargs="+", help="article numbers (holdout cases)")
    parser.add_argument("--split", required=True, choices=["train", "val", "holdout"])
    parser.add_argument(
        "--ia-section", action="append", help="IA heading prefix or title to draft from"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--drafted-by", help="your GitHub handle (default: git config github.user / user.name)"
    )
    parser.add_argument("--notes", help="free-text notes for reviewers")
    parser.add_argument("--out-dir", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--refresh", action="store_true", help="re-download the IA")
    parser.add_argument("--skip-self-check", action="store_true")
    parser.add_argument("--ia-index", type=Path, default=IA_INDEX_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--holdout-registry", type=Path, default=HOLDOUT_REGISTRY, help=argparse.SUPPRESS
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, backends: dict[str, LLMBackend] | None = None) -> int:
    """``backends`` replaces the configured ones (tests)."""
    args = parse_args(argv)
    try:
        if args.split == "holdout":
            # Holdout material never reaches LangSmith: not the IA, the draft or any call.
            with tracing_context(enabled=False):
                asyncio.run(run(args, backends))
        else:
            asyncio.run(run(args, backends))
    except (DraftingError, ReviewError, ia_sources.IaSourceError, FixtureError, LLMError) as exc:
        info(f"error: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
