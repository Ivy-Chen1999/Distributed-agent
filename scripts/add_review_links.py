"""Add public EUR-Lex links (IA and proposal) to train/val golden-case drafts, for reviewers.

    uv run python scripts/add_review_links.py evals/golden/drafts/case_12_*.yaml

For the case owner, who has the gitignored IA index (evals/private/ia_index.yaml) and, ideally,
the IA cache (.cache/ia/<fixture>/): with the cache, each item's IA part is recorded too, so a
reviewer opens the right part of a multi-part IA. The ``review_links`` block is written in place
before ``provenance:``; nothing else in the file changes but the legacy header comment, and the
tool digests stay valid (the block is not item content). New drafts get it from
scripts/draft_golden_case.py. Holdout drafts, and drafts of a proposal registered as holdout in
the local registry, are refused: their identifiers never enter a tracked file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from womm.data.ia_index import IA_INDEX_PATH, IaIndexError, load_ia_index
from womm.eval import ia_sources
from womm.eval.draft_edits import DraftEditError, build_review_links, set_review_links_in_place
from womm.eval.golden_review import (
    HOLDOUT_REGISTRY,
    ReviewError,
    load_draft_for_review,
    refuse_holdout_fixture,
)


def ia_part_texts(fixture: str, root: Path | None = None) -> list[str] | None:
    """The cached IA's part texts, or None without a cache."""
    if ia_sources.load_cached_ia(fixture, root) is None:
        return None
    return ["\n".join(part.blocks) for part in ia_sources.reparse_cached(fixture, root).parts]


def add_links(path: Path, index_path: Path, registry: Path, ia_root: Path | None = None) -> str:
    draft = load_draft_for_review(path)
    if draft.split == "holdout":
        raise DraftEditError(f"{draft.case_id}: a holdout draft never gets review links")
    refuse_holdout_fixture(draft.fixture, "adding review links", registry)
    record = load_ia_index(index_path).get(draft.fixture)
    if record is None:
        raise DraftEditError(f"{draft.case_id}: fixture {draft.fixture!r} is not in the IA index")
    links = build_review_links(draft, record, ia_part_texts(draft.fixture, ia_root))
    set_review_links_in_place(path, links)
    located = f", {len(links.anchor_parts)} anchors located" if links.ia_parts > 1 else ""
    return f"{path.name}: IA {links.ia} ({links.ia_parts} part(s){located})"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("drafts", nargs="+", type=Path, help="train/val draft files")
    parser.add_argument("--ia-index", type=Path, default=IA_INDEX_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--holdout-registry", type=Path, default=HOLDOUT_REGISTRY, help=argparse.SUPPRESS
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    failed = False
    for path in args.drafts:
        try:
            print(add_links(path, args.ia_index, args.holdout_registry, args.ia_root))
        except (DraftEditError, ReviewError, IaIndexError, ia_sources.IaSourceError) as exc:
            print(f"error: {path.name}: {exc}", file=sys.stderr)
            failed = True
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
