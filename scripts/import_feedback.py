"""Analyst feedback through LangSmith annotation queues (U11, origin R31).

    # 1. Put the traced graph runs of a version's train/val eval reports in an annotation queue
    #    (created with its rubric on first use).
    uv run python scripts/import_feedback.py queue --sv <version_id> [--queue womm-analyst-review]
    # 2. Analysts mark findings in the LangSmith UI (rubric key womm_analyst).
    # 3. Import the marks into Postgres: the audit table, Failure Memory, the candidate queue.
    uv run python scripts/import_feedback.py import --sv <version_id> [--dry-run]
    #    [--apply-retractions]  (retract stored marks deleted in LangSmith; default: report)
    # 4. Stage queued missing-impact candidates into open golden drafts for the review gate.
    uv run python scripts/import_feedback.py stage

A mark is one categorical value of the ``womm_analyst`` key: accept, reject, edit,
missing_impact or weak_evidence. Its details go in the comment, one ``field: value`` line each:

    finding: <finding_id>          accept, reject, edit and weak_evidence
    edited: <the corrected text>   edit
    impact: <what is missing>      missing_impact (also actor, mechanism, provisions, category)

A value may continue on the indented lines below its field line; a field given twice refuses
the mark. Any other comment line is the analyst's note. See ``womm.evolve.feedback`` for the
flow.

Train/val only. The runs come from the version's eval reports in ``--runs-dir`` (holdout runs are
never traced and never in a report; a report naming a holdout split is refused), each mark is
resolved against the saved run, and the run must already be in Failure Memory (``womm eval``
with DATABASE_URL set records it). Like every ``womm evolve`` command, this script refuses to run
with HOLDOUT_DATABASE_URL in its environment. Missing-impact marks are never added to golden
cases: ``stage`` appends them as pending ``possibly_missing`` items of an open train draft in
``evals/golden/drafts/``, which the review gate blocks until a reviewer decides each one; a case
without an open train draft keeps its candidates queued until it is re-drafted. ``stage`` edits
the draft in place (only the ``stats`` counts and the appended items change, so comments
survive) and writes nothing to a draft that changed while it was staging.

Edited and deleted marks. A mark edited in LangSmith after its import (a newer ``modified_at``)
replaces the stored one: the previous version is kept in the row's ``history`` and its Failure
Memory event is replaced. If its candidate was already staged into a draft, the draft item is
not changed: the script warns, and the reviewer updates or rejects it by hand. A stored mark on
the queried runs that LangSmith no longer lists is reported; ``--apply-retractions`` retracts it
(the row stays, marked ``retracted_at``; its event is removed; a queued candidate is dropped).

Exit codes: 0 done, 2 at least one mark or candidate was refused (the others are still
imported or staged), a mark needs attention (edited after staging, gone from LangSmith without
--apply-retractions, or retracted after staging), or a usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import psycopg

from womm.api.db import Database
from womm.config import REPO_ROOT
from womm.eval.drafting import DRAFTS_DIR
from womm.eval.golden import GoldenError
from womm.eval.golden_review import HOLDOUT_REGISTRY
from womm.evolve.feedback import (
    FEEDBACK_KEY,
    MARKS,
    FeedbackError,
    FeedbackRecord,
    load_run,
    parse_feedback,
    report_runs,
    resolve_feedback,
    stage_candidates,
)
from womm.evolve.planner_view import HoldoutEnvRefused, refuse_holdout_env

DEFAULT_QUEUE = "womm-analyst-review"
CHUNK = 50  # run ids per LangSmith call
MARK_HELP = {
    "accept": "The finding is right and useful as written.",
    "reject": "The finding is wrong or irrelevant.",
    "edit": "Right idea, wrong wording: put the corrected text in an 'edited:' comment line.",
    "missing_impact": "The dossier misses an impact: describe it in an 'impact:' line, with "
    "'provisions:', 'actor:', 'mechanism:' and 'category:' lines when you know them.",
    "weak_evidence": "The finding's evidence does not support it.",
}
INSTRUCTIONS = (
    "Mark one finding per feedback with the womm_analyst key. Name the finding in the comment "
    "as 'finding: <finding_id>' (the board in the run outputs lists the ids); a missing impact "
    "names none. Continue a long value on indented lines; give each field once. Other comment "
    "lines are your note."
)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def _client() -> Any:
    from langsmith import Client

    return Client()


def _chunks(ids: list[str]) -> list[list[str]]:
    return [ids[i : i + CHUNK] for i in range(0, len(ids), CHUNK)]


def _run_keys(client: Any, trace_ids: list[str]) -> tuple[list[dict], list[str]]:
    """LangSmith run keys (run id, session id, start time) of ``trace_ids``, looked up in
    chunks, and the ids LangSmith does not know."""
    keys: dict[str, dict] = {}
    for chunk in _chunks(trace_ids):
        for run in client.list_runs(run_ids=chunk):
            keys[str(run.id)] = {"run_id": str(run.id), "session_id": str(run.session_id),
                                 "start_time": run.start_time}  # fmt: skip
    return [keys[t] for t in trace_ids if t in keys], [t for t in trace_ids if t not in keys]


def _queue(client: Any, args: argparse.Namespace) -> int:
    from langsmith.utils import LangSmithError
    from requests import HTTPError

    index = report_runs(Path(args.runs_dir), args.sv)
    if not index:
        info(f"no traced, scored train/val runs of {args.sv} in {args.runs_dir}")
        return 2
    try:
        # Returns the existing config when it is identical; a different one is an error.
        client.create_feedback_config(FEEDBACK_KEY, feedback_config={
            "type": "categorical",
            "categories": [{"value": i, "label": m} for i, m in enumerate(MARKS)],
        })  # fmt: skip
    except (LangSmithError, HTTPError) as exc:
        info(f"error: the LangSmith feedback key {FEEDBACK_KEY!r} exists with a different "
             f"configuration than the {len(MARKS)} categorical marks {MARKS} (or could not be "
             f"created): {exc}. Fix or delete that feedback config in LangSmith; nothing was "
             "queued.")  # fmt: skip
        return 2
    found = list(client.list_annotation_queues(name=args.queue))
    queue = found[0] if found else client.create_annotation_queue(
        name=args.queue,
        description="WOMM analyst review of train/val eval runs (R31). Never holdout.",
        rubric_instructions=INSTRUCTIONS,
        rubric_items=[{"feedback_key": FEEDBACK_KEY, "description": INSTRUCTIONS,
                       "value_descriptions": MARK_HELP, "is_required": True}],
    )  # fmt: skip
    # Runs are added by their full key (the SDK's preferred path; bare run ids are deprecated).
    keys, unknown = _run_keys(client, sorted(index))
    for chunk in range(0, len(keys), CHUNK):
        client.add_runs_to_annotation_queue(queue.id, runs=keys[chunk : chunk + CHUNK])
    for t in unknown:
        info(f"not queued: LangSmith has no run {t} (run {index[t].run_id} of {index[t].case_id})")
    print(f"queued {len(keys)} train/val run(s) of {args.sv} in {args.queue}")
    return 2 if unknown else 0


async def _import(client: Any, args: argparse.Namespace, url: str) -> int:
    runs_dir = Path(args.runs_dir)
    index = report_runs(runs_dir, args.sv)
    if not index:
        info(f"no traced, scored train/val runs of {args.sv} in {args.runs_dir}")
        return 0
    records: list[FeedbackRecord] = []
    seen: set[str] = set()
    refused = 0
    listed = (
        raw
        for chunk in _chunks(sorted(index))
        for raw in client.list_feedback(run_ids=chunk, feedback_key=[FEEDBACK_KEY])
    )
    for raw in listed:
        seen.add(str(raw.id))
        try:
            fb = parse_feedback(raw)
            case_run = index.get(fb.trace_run_id)
            if case_run is None:
                raise FeedbackError(f"feedback {fb.feedback_id}: run {fb.trace_run_id} is not a "
                                    f"scored train/val run of {args.sv}")  # fmt: skip
            run = load_run(runs_dir, case_run.run_id)
            if run is None:
                raise FeedbackError(f"feedback {fb.feedback_id}: saved run {case_run.run_id} "
                                    f"not found in {runs_dir}")  # fmt: skip
            records.append(resolve_feedback(fb, case_run, run))
        except (FeedbackError, GoldenError) as exc:
            info(f"refused: {exc}")
            refused += 1
    if args.dry_run:
        for r in records:
            print(f"would import {r.feedback.feedback_id}: {r.feedback.mark} on "
                  f"{r.run.case_id} run {r.run.run_id}")  # fmt: skip
        return 2 if refused else 0
    db = Database(url)
    await db.open()
    counts = {"new": 0, "updated": 0, "unchanged": 0}
    attention = 0
    try:
        await db.migrate()
        for r in records:
            fid = r.feedback.feedback_id
            try:
                status = await db.record_analyst_feedback(r)
            except psycopg.errors.ForeignKeyViolation:
                info(f"refused: feedback {fid}: run {r.run.run_id} of {r.run.case_id} is not "
                     "in Failure Memory (persist its eval report first)")  # fmt: skip
                refused += 1
                continue
            if status == "updated_staged":
                info(f"warning: feedback {fid} was edited in LangSmith after its candidate was "
                     "already staged into a golden draft; the stored mark and its event were "
                     "replaced, but the draft item is not: update or reject it by hand")  # fmt: skip
                attention += 1
                status = "updated"
            counts[status] += 1
        attention += await _gone(db, args, index, seen)
    finally:
        await db.close()
    print(f"{counts['new']} new, {counts['updated']} updated, {counts['unchanged']} unchanged "
          f"mark(s) of {len(records)} read; {refused} refused")  # fmt: skip
    return 2 if refused or attention else 0


async def _gone(db: Database, args: argparse.Namespace, index: dict, seen: set[str]) -> int:
    """Stored marks on the queried runs that LangSmith no longer lists (deleted feedback):
    reported, and retracted with --apply-retractions. Returns how many need attention."""
    rows = [r for r in await db.analyst_feedback(args.sv)
            if r["retracted_at"] is None and r["trace_run_id"] in index
            and r["feedback_id"] not in seen]  # fmt: skip
    attention = 0
    for row in rows:
        fid, what = row["feedback_id"], f"{row['mark']} on {row['case_id']} run {row['run_id']}"
        if not args.apply_retractions:
            info(f"gone from LangSmith: feedback {fid} ({what}); rerun with "
                 "--apply-retractions to retract it and its Failure Memory event")  # fmt: skip
            attention += 1
            continue
        status = await db.retract_analyst_feedback(fid)
        print(f"retracted {fid} ({what})")
        if status == "retracted_staged":
            info(f"warning: retracted feedback {fid} was already staged into "
                 f"{row['golden_draft']}; reject that draft item by hand")  # fmt: skip
            attention += 1
    return attention


async def _stage(args: argparse.Namespace, url: str) -> int:
    db = Database(url)
    await db.open()
    try:
        await db.migrate()
        rows = await db.queued_golden_candidates()
        staged, problems = stage_candidates(
            rows, Path(args.drafts_dir), registry=Path(args.holdout_registry)
        )
        for fid, path in staged.items():
            await db.mark_candidate_staged(fid, _display(path))
    finally:
        await db.close()
    for p in problems:
        info(f"refused: {p}")
    print(f"staged {len(staged)} of {len(rows)} queued candidate(s); the rest wait for a draft")
    return 2 if problems else 0


def _display(path: Path) -> str:
    path = path.resolve()
    return str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["queue", "import", "stage"])
    parser.add_argument("--sv", help="system version id (queue, import)")
    parser.add_argument("--queue", default=DEFAULT_QUEUE, help="annotation queue name")
    parser.add_argument("--runs-dir", default=str(REPO_ROOT / "runs"))
    parser.add_argument("--drafts-dir", default=str(DRAFTS_DIR))
    parser.add_argument("--holdout-registry", default=str(HOLDOUT_REGISTRY))
    parser.add_argument("--database-url", default=None, help="default: $DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true", help="import: resolve, write nothing")
    parser.add_argument(
        "--apply-retractions",
        action="store_true",
        help="import: retract stored marks gone from LangSmith (default: report them only)",
    )
    args = parser.parse_args(argv)
    try:
        refuse_holdout_env()
    except HoldoutEnvRefused as exc:
        info(f"error: {exc}")
        return 2
    if args.command in ("queue", "import") and not args.sv:
        info(f"error: {args.command} needs --sv")
        return 2
    try:
        if args.command == "queue":
            return _queue(_client(), args)
        url = args.database_url or os.environ.get("DATABASE_URL")
        if not url and not (args.command == "import" and args.dry_run):
            info("error: needs DATABASE_URL (or --database-url)")
            return 2
        if args.command == "import":
            return asyncio.run(_import(_client(), args, url or ""))
        return asyncio.run(_stage(args, url))
    except GoldenError as exc:
        info(f"error: {exc}")
        return 2


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env", override=False)
    sys.exit(main())
