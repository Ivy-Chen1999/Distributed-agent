"""Verify a holdout golden-case draft locally, item by item (U5).

    uv run python scripts/verify_golden_case.py --draft .cache/drafts/case_20_x.yaml
    uv run python scripts/verify_golden_case.py --draft .cache/drafts/case_20_x.yaml \\
        --reviewer jdoe --decisions .cache/drafts/case_20_x.decisions.yaml

Holdout cases are never reviewed in a PR. Every item is verified by a human (the holdout rule),
with the same decisions as the PR review: verified, edited, rejected or unclear (see
docs/eval/golden-review-guide.md). Interactively, each item is shown with its IA anchor in
context from the local IA cache. Without a terminal the script refuses to run unless both
``--reviewer`` and ``--decisions`` are given. A decisions file maps item ids to decisions:

    c20_e01: {decision: verified}
    c20_e02: {decision: edited, note: "number fixed", edit: {impact: "..."}}
    c20_m01: {decision: rejected, note: "already covered by c20_e03"}

The verified case goes to a handoff file in .cache/holdout_import/ for the holdout importer
(U6), never to evals/. Everything runs with LangSmith tracing disabled.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from langsmith import tracing_context
from pydantic import ValidationError

from womm.config import REPO_ROOT
from womm.data.fixtures import FixtureError, fixture_dir, load_fixture
from womm.eval import ia_sources
from womm.eval.drafting import (
    EVALS_DIR,
    DraftCandidate,
    GoldenDraft,
    Review,
    _DraftItem,
    anchor_context,
    load_draft,
)
from womm.eval.golden import GoldenCase
from womm.eval.golden_review import (
    HUMAN_DECISIONS,
    NOTE_REQUIRED,
    ReviewError,
    audit_result,
    build_case,
    check_draft,
    item_kind,
)
from womm.eval.holdout import HANDOFF_FORMAT

HANDOFF_DIR = REPO_ROOT / ".cache" / "holdout_import"
CLAIM_FIELDS = ("affected_actor", "mechanism", "impact", "description")
EDITABLE = {
    "impact": ("affected_actor", "mechanism", "impact", "provision_keys", "ia_section", "category"),
    "candidate": (
        "affected_actor",
        "mechanism",
        "impact",
        "provision_keys",
        "ia_section",
        "category",
    ),
    "omission": ("description", "provision_keys", "ia_section", "category"),
}
SHORTCUTS = {"v": "verified", "e": "edited", "r": "rejected", "u": "unclear"}
STATUS = {"verified": "human_verified", "edited": "human_edited"}

Ask = Callable[[str], str]
Say = Callable[[str], None]


class VerifyError(RuntimeError):
    pass


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def _refuse_under_evals(path: Path, what: str) -> None:
    if path.resolve().is_relative_to(EVALS_DIR.resolve()):
        raise VerifyError(f"refusing {what} under evals/ ({path}); holdout material stays local")


def load_holdout_draft(path: Path) -> GoldenDraft:
    _refuse_under_evals(path, "a holdout draft")
    try:
        draft = load_draft(path)
    except (OSError, ValidationError, yaml.YAMLError) as exc:
        raise VerifyError(f"cannot read draft {path}: {exc}") from None
    if draft.split != "holdout":
        raise VerifyError(
            f"{draft.case_id} is a {draft.split} draft; train/val drafts are reviewed in a PR "
            "and published with scripts/publish_golden_cases.py"
        )
    return draft


def load_decisions(path: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise VerifyError(f"cannot read decisions {path}: {exc}") from None
    if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
        raise VerifyError(f"{path}: expected a mapping of item id -> {{decision, note, edit}}")
    return {str(k): v for k, v in raw.items()}


def _decided_item(item: _DraftItem, entry: dict[str, Any], reviewer: str) -> _DraftItem:
    where = item.item_id
    unknown = set(entry) - {"decision", "note", "edit"}
    if unknown:
        raise VerifyError(f"{where}: unknown keys {sorted(unknown)}")
    decision = entry.get("decision")
    if decision not in HUMAN_DECISIONS:
        raise VerifyError(f"{where}: decision must be one of {HUMAN_DECISIONS}, got {decision!r}")
    note = entry.get("note")
    if decision in NOTE_REQUIRED and not (note or "").strip():
        raise VerifyError(f"{where}: decision {decision!r} needs a note saying why")
    edit = entry.get("edit") or {}
    if decision == "edited" and not edit:
        raise VerifyError(f"{where}: decision 'edited' needs an 'edit' mapping of new values")
    if edit and decision != "edited":
        raise VerifyError(f"{where}: an edit needs decision 'edited', not {decision!r}")
    allowed = EDITABLE[item_kind(item)]
    bad = sorted(set(edit) - set(allowed))
    if bad:
        raise VerifyError(f"{where}: fields {bad} cannot be edited; editable: {list(allowed)}")
    data = item.model_dump(mode="json") | edit
    if isinstance(item, DraftCandidate) and decision in STATUS:
        status = "human_confirmed_candidate"
    else:
        status = STATUS.get(decision, item.provenance.status)
    data["provenance"] = {**data["provenance"], "status": status}
    data["review"] = Review(
        decision=decision, audit=item.review.audit, reviewer=reviewer, note=note
    ).model_dump()
    try:
        return type(item).model_validate(data)
    except ValidationError as exc:
        raise VerifyError(f"{where}: the edit is not schema-valid: {exc}") from None


def apply_decisions(
    draft: GoldenDraft, decisions: dict[str, dict[str, Any]], reviewer: str
) -> GoldenDraft:
    """Every item decided, by ``reviewer``; unknown or missing ids are refused."""
    ids = [i.item_id for i in draft.items()]
    unknown = sorted(set(decisions) - set(ids))
    if unknown:
        raise VerifyError(f"{draft.case_id}: decisions for unknown items {unknown}")
    missing = [i for i in ids if i not in decisions]
    if missing:
        raise VerifyError(
            f"{draft.case_id}: every holdout item needs a decision; missing {missing}"
        )

    def decide(items: list) -> list:
        return [_decided_item(i, decisions[i.item_id], reviewer) for i in items]

    return draft.model_copy(
        update={
            "expected_impacts": decide(draft.expected_impacts),
            "important_omissions": decide(draft.important_omissions),
            "possibly_missing": decide(draft.possibly_missing),
        }
    )


def fixture_keys(draft: GoldenDraft) -> set[str]:
    """Holdout scenarios are not in the public fixture: any provision key of the fixture."""
    try:
        fixture = load_fixture(fixture_dir(draft.fixture))
    except FixtureError as exc:
        raise VerifyError(f"{draft.case_id}: {exc}") from None
    return {p.provision_key for v in fixture.regulation.versions for p in v.provisions}


def verify(
    draft: GoldenDraft, decisions: dict[str, dict[str, Any]], reviewer: str, today: dt.date
) -> GoldenCase:
    if not reviewer.strip():
        raise VerifyError("a reviewer name is required")
    decided = apply_decisions(draft, decisions, reviewer.strip())
    problems = check_draft(decided, fixture_keys(decided), everything=True, reviewer_pattern=None)
    if problems:
        raise VerifyError("verification incomplete:\n  " + "\n  ".join(problems))
    try:
        return build_case(
            decided, published_on=today, audit=audit_result([decided]), action="verified"
        )
    except ReviewError as exc:
        raise VerifyError(str(exc)) from None


def write_handoff(
    case: GoldenCase, draft_path: Path, reviewer: str, today: dt.date, out_dir: Path
) -> Path:
    """The verified case for U6's holdout importer; private file mode, never under evals/."""
    path = out_dir / f"{case.case_id}.yaml"
    _refuse_under_evals(path, "the holdout handoff")
    data = {
        "format": HANDOFF_FORMAT,
        "consumer": "scripts/import_holdout_case.py",
        "verified_by": reviewer,
        "verified_on": today.isoformat(),
        "draft_sha256": hashlib.sha256(draft_path.read_bytes()).hexdigest(),
        "case": case.model_dump(mode="json", exclude_none=True),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("# Holdout handoff: sealed by the holdout importer, then deleted. Never commit.\n")
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True, width=100)
    return path


# ----------------------------------------------------------------------------- interactive


def _wrap(label: str, text: str) -> str:
    return textwrap.fill(f"{label}: {text}", width=100, subsequent_indent="    ")


def show_item(item: _DraftItem, n: int, total: int, ia: Any, say: Say, chars: int = 600) -> None:
    data = item.model_dump(mode="json")
    say("")
    say(f"=== [{n}/{total}] {item.item_id} ({item_kind(item)}) ===")
    for f in (*CLAIM_FIELDS, "why_missing"):
        if f in data:
            say(_wrap(f, data[f]))
    say(_wrap("provision_keys", ", ".join(item.provision_keys)))
    say(f"category: {item.category}   derivability: {item.derivability.verdict}")
    j = item.judge
    say(
        f"judges: anchor {j.anchor_faithfulness.verdict}, derivability {j.derivability.verdict}, "
        f"category {j.category.verdict} -> {j.overall}"
    )
    if item.flags:
        say(f"flags: {'; '.join(item.flags)}")
    say(_wrap("ia_section", item.ia_section))
    say(_wrap("anchor", item.ia_anchor))
    if ia is None:
        say("(IA not cached locally; anchor shown without context)")
        return
    text = ia.rsb_text if item.anchor.against == "rsb" else ia.full_text
    ctx = anchor_context(item.ia_anchor, text, chars)
    say(_wrap("in context", ctx or "ANCHOR NOT FOUND in the cached text"))


def _ask_edit(item: _DraftItem, ask: Ask) -> dict[str, Any]:
    data = item.model_dump(mode="json")
    edit: dict[str, Any] = {}
    for f in EDITABLE[item_kind(item)]:
        current = ", ".join(data[f]) if f == "provision_keys" else data[f]
        new = ask(f"  {f} [Enter keeps]: ").strip()
        if new and new != current:
            edit[f] = (
                [k.strip() for k in new.split(",") if k.strip()] if f == "provision_keys" else new
            )
    return edit


def interactive_decisions(
    draft: GoldenDraft, ask: Ask, say: Say, ia: Any = None
) -> dict[str, dict[str, Any]]:
    """Ask for a decision on every item; ``q`` aborts without writing anything."""
    decisions: dict[str, dict[str, Any]] = {}
    items = draft.items()
    for n, item in enumerate(items, 1):
        show_item(item, n, len(items), ia, say)
        while True:
            key = ask("[v]erified [e]dited [r]ejected [u]nclear [q]uit: ").strip().lower()
            if key == "q":
                raise VerifyError("aborted; nothing written")
            if key not in SHORTCUTS:
                continue
            decision = SHORTCUTS[key]
            entry: dict[str, Any] = {"decision": decision}
            if decision == "edited":
                entry["edit"] = _ask_edit(item, ask)
                if not entry["edit"]:
                    say("no field changed; choose again")
                    continue
            note = ask("note" + (" (required)" if decision in NOTE_REQUIRED else "") + ": ")
            if decision in NOTE_REQUIRED and not note.strip():
                say("a note is required for this decision")
                continue
            if note.strip():
                entry["note"] = note.strip()
            decisions[item.item_id] = entry
            break
    return decisions


# ----------------------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--draft", type=Path, required=True, help="holdout draft in .cache/drafts/")
    parser.add_argument("--reviewer", help="your name (required without a terminal)")
    parser.add_argument("--decisions", type=Path, help="YAML decisions file (non-interactive)")
    parser.add_argument("--out-dir", type=Path, default=HANDOFF_DIR, help=argparse.SUPPRESS)
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--today", type=dt.date.fromisoformat, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def run(
    args: argparse.Namespace,
    *,
    interactive: bool,
    ask: Ask = input,
    say: Say = info,
) -> Path:
    if not interactive and not (args.reviewer and args.decisions):
        raise VerifyError(
            "refusing to run non-interactively without both --reviewer and --decisions"
        )
    draft = load_holdout_draft(args.draft)
    today = args.today or dt.date.today()
    if args.decisions:
        decisions = load_decisions(args.decisions)
        reviewer = args.reviewer or ask("reviewer name: ").strip()
    else:
        reviewer = args.reviewer or ask("reviewer name: ").strip()
        ia = ia_sources.load_cached_ia(draft.fixture, args.ia_root)
        decisions = interactive_decisions(draft, ask, say, ia)
    case = verify(draft, decisions, reviewer, today)
    path = write_handoff(case, args.draft, reviewer, today, args.out_dir)
    print(f"{case.case_id}: {len(case.expected_impacts)} impacts, "
          f"{len(case.important_omissions)} omissions verified -> {path}")  # fmt: skip
    return path


def main(
    argv: list[str] | None = None,
    *,
    interactive: bool | None = None,
    ask: Ask = input,
    say: Say = info,
) -> int:
    """``interactive`` defaults to whether stdin is a terminal; ``ask``/``say`` are for tests."""
    args = parse_args(argv)
    if interactive is None:
        interactive = sys.stdin.isatty()
    # Holdout material never reaches LangSmith.
    with tracing_context(enabled=False):
        try:
            run(args, interactive=interactive, ask=ask, say=say)
        except (VerifyError, EOFError) as exc:
            info(f"error: {exc}" if str(exc) else "error: input ended; nothing written")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
