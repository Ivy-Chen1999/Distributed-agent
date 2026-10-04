"""Publish fully reviewed golden-case drafts as scored cases (U5).

    uv run python scripts/publish_golden_cases.py                 # every ready draft
    uv run python scripts/publish_golden_cases.py --case case_03_x --dry-run

Run after the review PR is merged. For each draft in evals/golden/drafts/ the script runs the
same strict check as CI (tests/eval/test_golden_drafts.py), then writes
evals/golden/<case_id>.yaml: rejected and unclear items are dropped, edited items are taken as
written, accepted possibly_missing candidates become expected impacts with provenance
``human_confirmed_candidate``, and the notes record the reviewers and dates. A case keeping
fewer than 3 expected impacts is refused. The published case must pass
``check_against_fixture``; then the draft is deleted.

Holdout drafts never come here: they are verified locally with scripts/verify_golden_case.py.

Exit codes: 0 every selected draft was published (or would be, with --dry-run), 2 at least one
was refused (the others are still published).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from womm.config import REPO_ROOT
from womm.eval.drafting import DRAFTS_DIR, GoldenDraft, load_draft
from womm.eval.golden import GOLDEN_DIR, GoldenError, check_against_fixture
from womm.eval.golden_review import (
    ReviewError,
    audit_result,
    build_case,
    case_yaml_header,
    check_draft,
    escalated_fixtures,
    scenario_keys_for,
)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def publish_one(
    draft: GoldenDraft,
    source: Path,
    *,
    all_drafts: list[GoldenDraft],
    golden_dir: Path,
    today: dt.date,
    dry_run: bool,
) -> Path:
    """Write one case; raises ReviewError (or GoldenError) with the reasons when refused."""
    if draft.split == "holdout":
        raise ReviewError(
            f"{draft.case_id}: holdout cases are never published to evals/; verify them with "
            "scripts/verify_golden_case.py and seal them in the holdout database"
        )
    escalated = draft.fixture in escalated_fixtures(all_drafts)
    problems = check_draft(draft, scenario_keys_for(draft), escalated=escalated)
    if problems:
        raise ReviewError("not fully decided:\n  " + "\n  ".join(problems))
    audit = audit_result(d for d in all_drafts if d.fixture == draft.fixture)
    case = build_case(draft, published_on=today, audit=audit)
    check_against_fixture(case)
    target = golden_dir / f"{case.case_id}.yaml"
    if target.exists():
        raise ReviewError(f"{case.case_id}: {_display(target)} already exists; not overwritten")
    if dry_run:
        return target
    data = case.model_dump(mode="json", exclude_none=True)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    golden_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(case_yaml_header(_display(source)) + body, encoding="utf-8")
    source.unlink()
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case", action="append", help="only this case id (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="check only, write nothing")
    parser.add_argument("--drafts-dir", type=Path, default=DRAFTS_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--today", type=dt.date.fromisoformat, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    today = args.today or dt.date.today()
    paths = sorted(args.drafts_dir.glob("case_*.yaml"))
    loaded: list[tuple[Path, GoldenDraft]] = []
    refused = 0
    for path in paths:
        try:
            loaded.append((path, load_draft(path)))
        except (ValidationError, yaml.YAMLError) as exc:
            if not args.case or path.stem in args.case:
                info(f"refused {_display(path)}: invalid draft: {exc}")
                refused += 1
    selected = [(p, d) for p, d in loaded if not args.case or d.case_id in args.case]
    missing = sorted(set(args.case or []) - {d.case_id for _, d in loaded})
    for case_id in missing:
        info(f"refused {case_id}: no draft in {_display(args.drafts_dir)}")
        refused += 1
    if not selected and not missing and not refused:
        info(f"no drafts in {_display(args.drafts_dir)}")
    all_drafts = [d for _, d in loaded]
    for path, draft in selected:
        try:
            target = publish_one(
                draft, path, all_drafts=all_drafts, golden_dir=args.golden_dir,
                today=today, dry_run=args.dry_run,
            )  # fmt: skip
        except (ReviewError, GoldenError) as exc:
            info(f"refused {draft.case_id}: {exc}")
            refused += 1
            continue
        verb = "would publish" if args.dry_run else "published"
        print(f"{verb} {draft.case_id} -> {_display(target)}")
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
