"""Tighten the answer keys after the failed reference check (plan 2026-10-16-001).

    uv run python scripts/key_fidelity_sweep.py sweep evals/golden/drafts/case_*.yaml \\
        .cache/rsb_companions_v2/*.rsb.yaml --judge-lines .cache/reference_check/judge_lines.json \\
        --out .cache/key_sweep/train_val
    uv run python scripts/key_fidelity_sweep.py trim --out .cache/key_sweep/train_val \\
        --judge-lines .cache/reference_check/judge_lines.json
    uv run python scripts/key_fidelity_sweep.py keeps --out .cache/key_sweep/train_val
    uv run python scripts/key_fidelity_sweep.py apply --out .cache/key_sweep/train_val \\
        --rulings rulings.json --wrong-keeps 0 --ruled-by "<name>" [--reject <case/item> ...]

``sweep`` and ``trim`` run model calls (the tie-break and drafter roles of evals/drafting.yaml,
on the Claude subscription) and write only under ``--out``; ``edits.html`` there is the
before/after page to review. ``keeps`` lists the seeded sample of the sweep's keeps to check.
``apply`` writes the accepted edits into the drafts as sweep overlays and the record
``key_tightened.json``. Holdout drafts need ``--holdout`` (no tracing, ``--out`` under .cache/,
counts only). Exit codes: 0 done, 2 bad input.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

from womm.eval import key_sweep as ks
from womm.eval.drafting import DraftingError, load_drafting_config


def _backends(config, skip_self_check: bool):
    spec = importlib.util.spec_from_file_location(
        "draft_golden_case", Path(__file__).with_name("draft_golden_case.py")
    )
    script = sys.modules.get("draft_golden_case")
    if script is None:
        script = importlib.util.module_from_spec(spec)
        sys.modules["draft_golden_case"] = script
        spec.loader.exec_module(script)
    return asyncio.run(script.prepare_backends(config, skip_self_check=skip_self_check))


def _lines(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("sweep", "trim", "keeps", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--out", type=Path, required=True, help="the sweep's working directory")
        p.add_argument("--holdout", action="store_true", help="holdout drafts (owner only)")
        if name in ("sweep", "trim"):
            p.add_argument("--judge-lines", type=Path, help="lines to judge, by <case>/<item>")
            p.add_argument("--skip-self-check", action="store_true")
        if name == "sweep":
            p.add_argument("drafts", nargs="+", type=Path)
        if name == "apply":
            p.add_argument("--rulings", type=Path, required=True,
                           help="the counts copied from the review page")  # fmt: skip
            p.add_argument("--wrong-keeps", type=int, required=True,
                           help="wrong keeps found in the `keeps` sample")  # fmt: skip
            p.add_argument("--ruled-by", required=True, help="who ruled and reviewed the edits")
            p.add_argument("--reject", action="append", default=[], help="<case>/<item>")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command in ("sweep", "trim"):
            config = load_drafting_config()
            backends = _backends(config, args.skip_self_check)
            lines = _lines(args.judge_lines)
            if args.command == "sweep":
                res = asyncio.run(ks.sweep(args.drafts, args.out, config, backends,
                                           judge_lines=lines, holdout=args.holdout))  # fmt: skip
                print(f"swept {res['swept']}: keep {res['keep']}, drop {res['drop']}")
            else:
                res = asyncio.run(ks.trim(args.out, config, backends, judge_lines=lines,
                                          holdout=args.holdout))  # fmt: skip
                print(f"flagged {res['flagged']}: proposed trim {res['trim']}, drop {res['drop']}"
                      + (f"; under the minimum if all drops pass: {', '.join(res['under_minimum'])}"
                         if res["under_minimum"] else ""))  # fmt: skip
                if not args.holdout:
                    print(f"review: {args.out / 'edits.html'}")
        elif args.command == "keeps":
            picked = ks.keep_sample(args.out)
            print(f"{len(picked)} keeps to check" + ("" if args.holdout else ":"))
            if not args.holdout:
                print("\n".join(picked))
        else:
            rulings = json.loads(args.rulings.read_text(encoding="utf-8"))
            rec = ks.apply(args.out, rejected=set(args.reject), rulings=rulings,
                           wrong_keeps=args.wrong_keeps, ruled_by=args.ruled_by,
                           holdout=args.holdout)  # fmt: skip
            print(f"applied: trimmed {rec['trimmed']}, dropped {rec['dropped']} in "
                  f"{len(rec['drafts'])} drafts; record {args.out / ks.RECORD_FILE}")  # fmt: skip
    except (ks.SweepError, DraftingError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
