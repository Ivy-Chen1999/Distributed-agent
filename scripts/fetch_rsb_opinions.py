"""Fetch the Regulatory Scrutiny Board opinion of every cached impact assessment.

    uv run python scripts/fetch_rsb_opinions.py [--fixture <id> ...] [--refresh]

For each fixture in the gitignored IA index (evals/private/ia_index.yaml) whose IA is cached
under .cache/ia/, the RSB opinion is looked up in Cellar (womm.eval.ia_sources.refresh_rsb: the
comnat resource, then the pi_com work found by SPARQL, served as PDF) and written next to the
IA's procedural annex, which is kept. Nothing tracked is written. The table says, per fixture,
whether the opinion was found and how many words it has; a fixture without one is listed, never
skipped silently. Exit code 1 when any fixture has no opinion text.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from womm.data.ia_index import IA_INDEX_PATH, load_ia_index
from womm.eval import ia_sources


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--fixture", action="append", help="only these fixtures (repeatable)")
    parser.add_argument("--refresh", action="store_true", help="ignore cached downloads")
    parser.add_argument("--index", type=Path, default=IA_INDEX_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, client=None) -> list[tuple[str, str, int]]:
    """(fixture, status, opinion words) per fixture, in fixture order."""
    index = load_ia_index(args.index)
    wanted = args.fixture or sorted(index)
    rows = []
    for fixture in wanted:
        record = index.get(fixture)
        if record is None:
            rows.append((fixture, "not in the IA index", 0))
            continue
        if ia_sources.load_cached_ia(fixture, args.ia_root) is None:
            rows.append((fixture, "IA not cached; fetch it first", 0))
            continue
        cached = ia_sources.refresh_rsb(
            fixture, record.rsb_ref, root=args.ia_root, refresh=args.refresh, client=client
        )
        rows.append((fixture, cached.rsb_status, len(cached.rsb_opinion.split())))
    return rows


def main(argv: list[str] | None = None, client=None) -> int:
    args = parse_args(argv)
    try:
        rows = run(args, client)
    except (ia_sources.IaSourceError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    width = max((len(f) for f, _, _ in rows), default=7)
    for fixture, status, words in rows:
        print(f"{fixture:<{width}}  {words:>6} words  {status}")
    missing = [f for f, _, w in rows if not w]
    print(f"\n{len(rows) - len(missing)} of {len(rows)} with the RSB opinion"
          + (f"; without: {', '.join(missing)}" if missing else ""))  # fmt: skip
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
