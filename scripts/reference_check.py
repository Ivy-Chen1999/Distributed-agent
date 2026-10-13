"""One-time blind check that the tool-settled reference answers are robust (womm.eval.reference_check).

    uv run python scripts/reference_check.py sample evals/golden/drafts/case_*.yaml \\
        --annotator <name1> --annotator <name2> --n 30 --out .cache/reference_check/round1
    uv run python scripts/reference_check.py upload --dir .cache/reference_check/round1 --url <app>
    uv run python scripts/reference_check.py fetch --dir .cache/reference_check/round1 --url <app>
    uv run python scripts/reference_check.py score --dir .cache/reference_check/round1
    uv run python scripts/reference_check.py extend --dir .cache/reference_check/round1 --url <app>

Run ``sample`` on the tie-broken train/val drafts before they are published
(scripts/publish_golden_cases.py deletes each draft it publishes). It draws ``--n`` items per
annotator (30 by default; each annotator gets different items), a third each kept by the three
judges, kept by the tie-break and dropped by it (20 a person by default, plus a reserve of 10
sent only if the first round has an error: ``extend``), and writes one blind labeling page per
annotator plus ``key.private.json`` (the tool's decisions; never send it). ``upload`` hosts the
pages on the deployed app and prints the one link to send; annotators sign in with their name and
every answer is saved there. ``fetch`` writes the answers back as files (both need
``WOMM_LABEL_ADMIN_TOKEN`` in the environment, the same value as on the app). ``score`` reads the
answer files and reports the tool's error rate against the people's labels; at or below
10% the reference answers count as robust.
Exit codes: 0 done (score: robust), 1 score: not robust, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

from womm.eval import hosted_labels, review_page
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
    sample.add_argument(
        "--annotator",
        action="append",
        required=True,
        dest="annotators",
        help="annotator's name, what they sign in with (repeatable)",
    )
    sample.add_argument("--out", type=Path, required=True, help="a new directory under .cache/")
    sample.add_argument("--n", type=int, default=20, help="first-round items per annotator")
    sample.add_argument("--extra", type=int, default=10,
                        help="reserve items per annotator, sent only if the first round has an "
                             "error")  # fmt: skip
    sample.add_argument("--seed", type=int, default=1)
    sample.add_argument("--overlap", type=int, default=0,
                        help="first-round items also given to every other annotator, to measure "
                             "how often people agree (outside the gate)")  # fmt: skip
    sample.add_argument(
        "--companions",
        action="store_true",
        help="label every item of the RSB companion drafts given (a census split between the "
        "annotators; give their case drafts too); without it companions are left out",
    )
    sample.add_argument(
        "--send-to",
        default=rc.SEND_TO,
        help='who the annotators send their file to, e.g. "Yuxin on Slack"',
    )
    sample.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    sample.add_argument(
        "--holdout-registry", type=Path, default=HOLDOUT_REGISTRY, help=argparse.SUPPRESS
    )
    for name, text in (("upload", "host the pages on the app"),
                       ("fetch", "write the hosted answers as answer files")):  # fmt: skip
        hosted = sub.add_parser(name, help=text)
        hosted.add_argument("--dir", type=Path, required=True, help="the sample directory")
        hosted.add_argument(
            "--url", required=True, help="the app, e.g. https://womm.up.railway.app"
        )
        hosted.add_argument(
            "--holdout-registry", type=Path, default=HOLDOUT_REGISTRY, help=argparse.SUPPRESS
        )
    ext = sub.add_parser("extend", help="send the reserve items (after a first round with errors)")
    ext.add_argument("--dir", type=Path, required=True, help="the sample directory")
    ext.add_argument("--url", help="the app, when the pages are hosted")
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
            companions = [d for d in drafts if d.companion is not None]
            cases = [d for d in drafts if d.companion is None]
            if args.companions:
                # A census: every companion item, split evenly between the annotators.
                items = len(rc.population(companions))
                n = max(3, -(-items // len(args.annotators)))
                out = rc.write_sample(args.out, cases, n=n, seed=args.seed,
                                      annotators=args.annotators, send_to=args.send_to,
                                      ia_root=args.ia_root, extra=0,
                                      companions=companions, overlap=args.overlap)  # fmt: skip
            else:
                if companions:
                    print(f"note: {len(companions)} companion draft(s) left out; label them "
                          "with --companions", file=sys.stderr)  # fmt: skip
                out = rc.write_sample(args.out, cases, n=args.n, seed=args.seed,
                                      annotators=args.annotators, send_to=args.send_to,
                                      ia_root=args.ia_root, extra=args.extra,
                                      overlap=args.overlap)  # fmt: skip
            print(json.dumps(out, indent=2))
            if out["missing_ia"]:
                print(f"note: no IA cache for {out['missing_ia']}; those sheets show the quote "
                      "only", file=sys.stderr)  # fmt: skip
            return 0
        if args.command in ("upload", "fetch"):
            return hosted(args)
        if args.command == "extend":
            return extend(args)
        key = json.loads((args.dir / rc.KEY_FILE).read_text(encoding="utf-8"))
        labels, notes = rc.read_answers(args.dir, key)
        result = rc.score(key, labels)
        print(json.dumps(result, indent=2) if args.json else rc.format_result(result, key, notes))
        page = review_page.reference_check(args.dir, key, result, labels, notes, limit=rc.MAX_ERROR)
        print(f"review page (private, keep it local): {page}", file=sys.stderr)
        return 0 if result["robust"] else 1
    except (FileNotFoundError, rc.ReferenceCheckError, ReviewError, httpx.HTTPError,
            hosted_labels.HostedLabelsError) as exc:  # fmt: skip
        print(f"error: {exc}", file=sys.stderr)
        return 2


def extend(args: argparse.Namespace, client: httpx.Client | None = None) -> int:
    pages = hosted_labels.read_pages(args.dir)
    if not pages.get("reserve"):
        raise rc.ReferenceCheckError("this sample has no reserve left to send")
    if args.url:  # the app first: a refusal there must leave the local files as they were
        hosted_labels.extend(args.url, pages["sample"], pages["reserve"], client=client)
    added = rc.extend(args.dir)
    counts = ", ".join(f"{name} +{len(cards)}" for name, cards in sorted(added.items()))
    where = (
        "on the app; ask them to open the same link again"
        if args.url
        else ("in their label_<name>.html; send each their new page")
    )
    print(f"extended: {counts}, {where}")
    return 0


def hosted(args: argparse.Namespace, client: httpx.Client | None = None) -> int:
    if args.command == "upload":
        link = hosted_labels.upload(args.dir, args.url, client=client,
                                    holdout_registry=args.holdout_registry)  # fmt: skip
        print(f"hosted. Send this link to the annotators:\n  {link}")
        return 0
    got = hosted_labels.fetch(args.dir, args.url, client=client)
    sample = hosted_labels.read_pages(args.dir)["sample"]
    rc.write_answer_files(args.dir, sample, got)
    lines, pending = hosted_labels.progress(got)
    print("\n".join(lines))
    if pending:
        print(f"waiting for {', '.join(pending)}; score once everyone has submitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
