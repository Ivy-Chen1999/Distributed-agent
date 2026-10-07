"""Publish fully reviewed golden-case drafts as scored cases (U5).

    uv run python scripts/publish_golden_cases.py                 # every ready draft
    uv run python scripts/publish_golden_cases.py --case case_03_x --dry-run

Run after the review PR is merged. For each draft in evals/golden/drafts/ the script runs the
same strict check as CI (tests/eval/test_golden_drafts.py), then writes
evals/golden/<case_id>.yaml: rejected and unclear items are dropped, edited items are taken as
written, accepted possibly_missing candidates become expected impacts with provenance
``human_confirmed_candidate``, and the notes record the reviewers and dates. A case keeping
fewer than 3 expected impacts is refused. The published case must pass
``check_against_fixture``; then the draft is deleted and its audit counts are added to
evals/golden/audit_tally.yaml (counts per fixture only), so a proposal's escalation spans PRs.

A ``human_added`` item (an analyst's missing impact, staged by scripts/import_feedback.py) is
kept only as ``edited`` by a reviewer other than the analyst, with its IA anchor filled in; the
anchor is checked against the cached IA (``.cache/ia/<fixture>/``) when it is on this machine,
otherwise the script prints a note asking the reviewer to confirm it by hand. Published, it
carries ``origin: human`` (misses on it never drive the new-expert trigger).

A draft whose fixture is registered as a holdout proposal in the gitignored local registry
(evals/private/holdout_scenarios.yaml) is refused: all cases of one proposal share a split.

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
    AUDIT_TALLY_PATH,
    HOLDOUT_REGISTRY,
    ReviewError,
    Tally,
    TallyError,
    audit_result,
    build_case,
    case_yaml_header,
    check_draft,
    escalated_fixtures,
    fixture_audit,
    holdout_fixtures,
    load_audit_tally,
    refuse_holdout_fixture,
    scenario_keys_for,
    unchecked_human_anchors,
    write_audit_tally,
)
from womm.eval.ia_sources import IaSourceError, load_cached_ia

_ZERO = audit_result([])


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _cached_ia_text(fixture: str, root: Path | None) -> str | None:
    """The cached IA full text of ``fixture`` when it is on this machine, else None."""
    try:
        cached = load_cached_ia(fixture, root)
    except (IaSourceError, OSError, ValueError):
        return None
    return cached.full_text if cached is not None and cached.full_text else None


def publish_one(
    draft: GoldenDraft,
    source: Path,
    *,
    all_drafts: list[GoldenDraft],
    golden_dir: Path,
    today: dt.date,
    dry_run: bool,
    tally: Tally | None = None,
    registry: Path = HOLDOUT_REGISTRY,
    ia_root: Path | None = None,
) -> Path:
    """Write one case; raises ReviewError (or GoldenError) with the reasons when refused."""
    if draft.split == "holdout":
        raise ReviewError(
            f"{draft.case_id}: holdout cases are never published to evals/; verify them with "
            "scripts/verify_golden_case.py and seal them in the holdout database"
        )
    refuse_holdout_fixture(draft.fixture, "publishing a train/val case", registry)
    escalated = draft.fixture in escalated_fixtures(all_drafts, tally)
    ia_text = _cached_ia_text(draft.fixture, ia_root)
    problems = check_draft(draft, scenario_keys_for(draft), escalated=escalated, ia_text=ia_text)
    for note in unchecked_human_anchors(draft, ia_text):
        info(f"note: {note}")
    if problems:
        raise ReviewError("not fully decided:\n  " + "\n  ".join(problems))
    audit = fixture_audit(draft.fixture, all_drafts, tally)
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
    parser.add_argument("--tally", type=Path, default=AUDIT_TALLY_PATH, help=argparse.SUPPRESS)
    parser.add_argument(
        "--holdout-registry", type=Path, default=HOLDOUT_REGISTRY, help=argparse.SUPPRESS
    )
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
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
    try:
        tally = load_audit_tally(args.tally)
        holdout = holdout_fixtures(args.holdout_registry)
    except (TallyError, ReviewError) as exc:
        info(f"error: {exc}")
        return 2
    # Escalation is decided once, over the tally as it stood plus every open draft; each
    # published draft then moves its counts from the drafts into the tally.
    before = dict(tally)
    for path, draft in selected:
        try:
            target = publish_one(
                draft, path, all_drafts=all_drafts, golden_dir=args.golden_dir,
                today=today, dry_run=args.dry_run, tally=before,
                registry=args.holdout_registry, ia_root=args.ia_root,
            )  # fmt: skip
        except (ReviewError, GoldenError) as exc:
            info(f"refused {draft.case_id}: {exc}")
            refused += 1
            continue
        if not args.dry_run:
            tally[draft.fixture] = tally.get(draft.fixture, _ZERO) + audit_result([draft])
            try:
                write_audit_tally(tally, args.tally, holdout=holdout)
            except TallyError as exc:
                info(f"error: {exc}")
                refused += 1
        verb = "would publish" if args.dry_run else "published"
        print(f"{verb} {draft.case_id} -> {_display(target)}")
    return 2 if refused else 0


if __name__ == "__main__":
    sys.exit(main())
