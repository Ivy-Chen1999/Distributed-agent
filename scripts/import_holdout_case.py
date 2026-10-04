"""Seal verified holdout cases in the holdout database, then delete the plaintext (U6).

    HOLDOUT_DATABASE_URL=postgresql://... \\
        uv run python scripts/import_holdout_case.py .cache/holdout_import/case_20_x.yaml

Each handoff written by scripts/verify_golden_case.py is checked against its holdout scenario,
supplied locally in evals/private/holdout_scenarios.yaml (the article set never comes from a
public fixture), and its IA record in evals/private/ia_index.yaml. The case, its scenario and
its IA identifiers are stored in the holdout database (its own migrations are applied here,
never by the API). Then the handoff is deleted, together with the plaintext draft in
.cache/drafts/ (when its sha256 matches the one recorded at verification) and its decisions
file. Re-importing the same case is a no-op. Everything runs with LangSmith tracing disabled.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
from pathlib import Path

import yaml
from langsmith import tracing_context

from womm.config import REPO_ROOT
from womm.data.ia_index import IA_INDEX_PATH, IaIndexError, load_ia_index
from womm.eval.golden import GOLDEN_DIR
from womm.eval.holdout import (
    PRIVATE_DIR,
    SCENARIOS_PATH,
    HoldoutError,
    HoldoutStore,
    holdout_database_url,
    load_holdout_scenarios,
    prepare_import,
)

EVALS_DIR = REPO_ROOT / "evals"
DRAFTS_DIR = REPO_ROOT / ".cache" / "drafts"


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def read_handoff(path: Path) -> dict:
    if path.resolve().is_relative_to(EVALS_DIR.resolve()):
        raise HoldoutError(f"refusing a holdout handoff under evals/ ({path})")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise HoldoutError(f"cannot read handoff {path}: {exc}") from None
    if not isinstance(data, dict):
        raise HoldoutError(f"{path}: not a holdout handoff")
    return data


def delete_plaintext(handoff_path: Path, case_id: str, draft_sha: str | None, drafts: Path) -> None:
    """Remove the handoff, and the draft and decisions file it was verified from."""
    handoff_path.unlink()
    draft = drafts / f"{case_id}.yaml"
    if draft.exists():
        if draft_sha and hashlib.sha256(draft.read_bytes()).hexdigest() == draft_sha:
            draft.unlink()
        else:
            info(f"warning: {draft} differs from the verified draft; left in place")
    decisions = drafts / f"{case_id}.decisions.yaml"
    if decisions.exists():
        decisions.unlink()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("handoffs", nargs="+", type=Path, help=".cache/holdout_import/<case>.yaml")
    parser.add_argument("--scenarios", type=Path, default=SCENARIOS_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--private-dir", type=Path, default=PRIVATE_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--ia-index", type=Path, default=IA_INDEX_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--drafts-dir", type=Path, default=DRAFTS_DIR, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


async def run(args: argparse.Namespace, url: str) -> list[str]:
    scenarios = load_holdout_scenarios(args.scenarios, args.private_dir)
    try:
        ia_index = load_ia_index(args.ia_index)
    except IaIndexError as exc:
        raise HoldoutError(str(exc)) from None
    # Validate every handoff before touching the database or deleting anything.
    bundles = [
        (path, prepare_import(read_handoff(path), scenarios, ia_index, golden_dir=args.golden_dir))
        for path in args.handoffs
    ]
    store = HoldoutStore(url)
    await store.migrate()
    outcomes = []
    for path, bundle in bundles:
        outcome = await store.import_case(bundle)
        delete_plaintext(path, bundle.case.case_id, bundle.draft_sha256, args.drafts_dir)
        print(f"{bundle.case.case_id}: {outcome}; plaintext handoff deleted")
        outcomes.append(outcome)
    return outcomes


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    with tracing_context(enabled=False):
        try:
            asyncio.run(run(args, holdout_database_url()))
        except HoldoutError as exc:
            info(f"error: {exc}")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
