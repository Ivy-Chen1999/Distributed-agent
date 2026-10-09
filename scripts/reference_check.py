"""One-time blind check that the tool-settled reference answers are robust (womm.eval.reference_check).

    uv run python scripts/reference_check.py sample evals/golden/drafts/case_*.yaml \\
        --annotator alice --annotator bob --out .cache/reference_check/2026-10
    uv run python scripts/reference_check.py score --dir .cache/reference_check/2026-10

Run ``sample`` on the tie-broken train/val drafts before they are published
(scripts/publish_golden_cases.py deletes each draft it publishes). It draws about 20 items, a
third each kept by the three judges, kept by the tie-break and dropped by it, and writes one
blind labeling sheet and answer file per annotator plus ``key.private.json`` (the tool's
decisions; never send it). ``score`` reads the answer files back and reports the tool's error
rate against the human majority; at or below 10% the reference answers count as robust.
Exit codes: 0 done (score: robust), 1 score: not robust, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from womm.eval import reference_check as rc
from womm.eval.golden_review import (
    HOLDOUT_REGISTRY,
    ReviewError,
    load_draft_for_review,
    refuse_holdout_fixture,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sample = sub.add_parser("sample", help="draw the items and write the labeling material")
    sample.add_argument("drafts", nargs="+", type=Path, help="tie-broken train/val draft files")
    sample.add_argument("--annotator", action="append", required=True, dest="annotators")
    sample.add_argument("--out", type=Path, required=True, help="a new directory under .cache/")
    sample.add_argument("--n", type=int, default=20)
    sample.add_argument("--seed", type=int, default=1)
    sample.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    sample.add_argument(
        "--holdout-registry", type=Path, default=HOLDOUT_REGISTRY, help=argparse.SUPPRESS
    )
    score = sub.add_parser("score", help="compare the labels with the tool")
    score.add_argument("--dir", type=Path, required=True)
    score.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "sample":
            drafts = []
            for path in args.drafts:
                draft = load_draft_for_review(path)
                refuse_holdout_fixture(draft.fixture, "a reference check", args.holdout_registry)
                drafts.append(draft)
            out = rc.write_sample(args.out, drafts, n=args.n, seed=args.seed,
                                  annotators=args.annotators, ia_root=args.ia_root)  # fmt: skip
            print(json.dumps(out, indent=2))
            if out["missing_ia"]:
                print(f"note: no IA cache for {out['missing_ia']}; those sheets show the quote "
                      "only", file=sys.stderr)  # fmt: skip
            return 0
        key = json.loads((args.dir / rc.KEY_FILE).read_text(encoding="utf-8"))
        result = rc.score(key, rc.read_answers(args.dir, set(key["items"])))
        print(json.dumps(result, indent=2) if args.json else rc.format_result(result, key))
        return 0 if result["robust"] else 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (rc.ReferenceCheckError, ReviewError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
