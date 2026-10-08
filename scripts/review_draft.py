"""Review a golden-case draft: see what to decide, decide it, check it as CI will.

    uv run python scripts/review_draft.py                    # list the items you must decide
    uv run python scripts/review_draft.py --interactive      # decide them one by one
    uv run python scripts/review_draft.py --template         # or: write a decisions file ...
    uv run python scripts/review_draft.py --apply FILE       # ... fill it in, and apply it
    uv run python scripts/review_draft.py --check            # what CI will say

The draft defaults to the only file in evals/golden/drafts/ (a review branch holds one); pass a
path or a case id prefix such as ``case_12`` otherwise. Each item is shown with its claim, its IA
section and anchor quote (what to search for in the IA, linked from ``review_links``), and the
three LLM judges' verdicts and reasons.

Decisions are written into the draft in place: only the decided items' ``review`` block and
``provenance.status`` change (an ``edited`` item is rewritten with its new values), every other
line and comment is kept, the tool digests stay valid, and nothing is written if the file changed
meanwhile. The reviewer is ``--reviewer``, else the decisions file's ``reviewer``, else
``git config github.user``. Rules: docs/eval/reviewer-quickstart.md and
docs/eval/golden-review-guide.md. Holdout drafts are verified with scripts/verify_golden_case.py.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from womm.config import REPO_ROOT
from womm.eval.draft_edits import DraftEditError, apply_decisions_in_place
from womm.eval.drafting import DRAFTS_DIR, GoldenDraft, _DraftItem
from womm.eval.golden_review import (
    EDITABLE,
    HUMAN_DECISIONS,
    NOTE_REQUIRED,
    USERNAME_HINT,
    ReviewError,
    _person,
    _valid_reviewer,
    editable_fields,
    escalated_fixtures,
    human_reasons,
    item_kind,
    load_audit_tally,
    load_draft_for_review,
    refuse_holdout_fixture,
    review_gate,
)

Ask = Callable[[str], str]
Say = Callable[[str], None]
CLAIM_FIELDS = ("affected_actor", "mechanism", "impact", "description", "why_missing")
SHORTCUTS = {"v": "verified", "e": "edited", "r": "rejected", "u": "unclear"}
PLACEHOLDER = "<your-github-username>"
TEMPLATE_DIR = REPO_ROOT / ".cache" / "review"


class HelperError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- loading


def resolve_draft(arg: str | None, drafts_dir: Path = DRAFTS_DIR) -> Path:
    """A path, a case id (prefix) in the drafts folder, or the folder's only draft."""
    if arg and Path(arg).is_file():
        return Path(arg)
    files = sorted(drafts_dir.glob("*.yaml"))
    if arg:
        files = [f for f in files if f.stem.startswith(Path(arg).stem)]
    if len(files) == 1:
        return files[0]
    names = ", ".join(f.stem for f in files) or "none"
    what = f"matching {arg!r}" if arg else f"in {drafts_dir}"
    raise HelperError(f"expected one draft {what}, found: {names}; pass the draft's path")


def load(path: Path) -> GoldenDraft:
    try:
        draft = load_draft_for_review(path)
    except ReviewError as exc:
        raise HelperError(str(exc)) from None
    if draft.split == "holdout":
        raise HelperError("holdout drafts are verified with scripts/verify_golden_case.py")
    try:
        refuse_holdout_fixture(draft.fixture, "review")
    except ReviewError as exc:
        raise HelperError(str(exc)) from None
    return draft


def needs(draft: GoldenDraft, drafts_dir: Path) -> dict[str, list[str]]:
    """item id -> why it needs a human, for the items that do (the CI rule, with escalation)."""
    others = []
    for p in sorted(drafts_dir.glob("*.yaml")):
        try:
            others.append(load_draft_for_review(p))
        except ReviewError:
            continue
    others = [d for d in others if d.case_id != draft.case_id]
    escalated = draft.fixture in escalated_fixtures([draft, *others], load_audit_tally())
    out = {}
    for item in draft.items():
        reasons = human_reasons(item, escalated=escalated)
        if reasons:
            out[item.item_id] = reasons
    return out


def is_done(item: _DraftItem) -> bool:
    return item.review.decision in HUMAN_DECISIONS and bool(item.review.reviewer)


def git_user() -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "--get", "github.user"], capture_output=True, text=True, check=False
        ).stdout.strip()
    except OSError:
        return None
    return out or None


# ----------------------------------------------------------------------------- showing


def _wrap(label: str, text: str, indent: str = "  ") -> str:
    return textwrap.fill(
        f"{label}: {text}", width=100, initial_indent=indent, subsequent_indent=indent + "    "
    )


def search_phrase(anchor: str, words: int = 10) -> str:
    """The first words of the anchor's first verbatim segment: what to paste into Ctrl+F."""
    first = re.split(r"\[?\s*(?:\.{3,}|…)\s*\]?", anchor.strip())[0].strip(" .,;:\"'")
    return " ".join(first.split()[:words])


def _display(path: Path) -> str:
    """The path as typed from the current directory when it is below it."""
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)


def header(draft: GoldenDraft, path: Path, todo: dict[str, list[str]]) -> list[str]:
    done = sum(is_done(i) for i in draft.items() if i.item_id in todo)
    lines = [
        f"{draft.case_id}  ({draft.split} split, fixture {draft.fixture}, scenario "
        f"{draft.scenario_id})",
        f"file: {_display(path)}",
    ]
    links = draft.review_links
    if links:
        parts = f"  ({links.ia_parts} parts on EUR-Lex)" if links.ia_parts > 1 else ""
        lines += [f"IA:       {links.ia}{parts}", f"proposal: {links.proposal}"]
    else:
        lines.append("IA: no review_links in this draft; ask the case owner for the IA link")
    lines.append(f"decisions needed: {len(todo)} ({done} made, {len(todo) - done} to go)")
    return lines


def describe(item: _DraftItem, reasons: list[str], draft: GoldenDraft) -> list[str]:
    data = item.model_dump(mode="json")
    rev = item.review
    status = rev.decision + (f" by {rev.reviewer}" if rev.reviewer else "")
    out = [f"{item.item_id}  [{item_kind(item)}]  needs you because: {', '.join(reasons)}"]
    out.append(f"  decision now: {status}" + (f" (note: {rev.note})" if rev.note else ""))
    if is_human_added(item):
        out.append(
            f"  raised by: {item.provenance.raised_by or '(nobody recorded)'} (analyst feedback "
            f"{item.provenance.feedback_id or '?'}); keep it only as 'edited', with every field "
            "and the IA anchor filled in, and only if you did not raise it"
        )
    for f in CLAIM_FIELDS:
        if f in data:
            out.append(_wrap(f, data[f]))
    out.append(_wrap("provision_keys", ", ".join(item.provision_keys)))
    out.append(f"  category: {item.category}   derivability: {item.derivability.verdict}")
    part = draft.review_links.anchor_parts.get(item.item_id) if draft.review_links else None
    where = f" (IA part {part})" if part else ""
    source = " in the RSB opinion" if item.anchor.against == "rsb" else ""
    out.append(_wrap(f"IA section{where}", item.ia_section))
    if item.ia_anchor.strip():
        out.append(_wrap(f"search the IA{source} for", f'"{search_phrase(item.ia_anchor)}"'))
        out.append(_wrap("anchor (verbatim)", item.ia_anchor))
    else:
        out.append("  anchor: no anchor yet; find the passage in the IA and fill in ia_anchor")
    if item.flags:
        out.append(_wrap("flags", "; ".join(item.flags)))
    j = item.judge
    out.append(f"  judges -> {j.overall}:")
    for dim in ("anchor_faithfulness", "derivability", "category"):
        v = getattr(j, dim)
        out.append(_wrap(f"{dim} {v.verdict}", v.reason, indent="    "))
    return out


def is_human_added(item: _DraftItem) -> bool:
    return item.provenance.origin == "human_added"


def list_items(draft: GoldenDraft, path: Path, drafts_dir: Path, show_all: bool, say: Say) -> None:
    todo = needs(draft, drafts_dir)
    for line in header(draft, path, todo):
        say(line)
    items = [i for i in draft.items() if show_all or i.item_id in todo]
    for n, item in enumerate(items, 1):
        say("")
        say(f"[{n}/{len(items)}] " + "\n".join(describe(item, todo.get(item.item_id, []), draft)))
    say("")
    say("next: decide with --interactive, or --template then --apply FILE; then --check")


# ----------------------------------------------------------------------------- deciding


def template(draft: GoldenDraft, path: Path, drafts_dir: Path, reviewer: str | None) -> str:
    """A decisions file with one entry per undecided item that needs a human."""
    todo = needs(draft, drafts_dir)
    items = [i for i in draft.items() if i.item_id in todo and not is_done(i)]
    lines = [
        f"# Decisions for {draft.case_id}. Fill in, then run:",
        f"#   uv run python scripts/review_draft.py {_display(path)} --apply <this file>",
        "# decision: verified | edited | rejected | unclear (leave empty to skip for now)",
        "# note: required for rejected and unclear (e.g. 'covered by c12_e03')",
        "# edit: only with decision: edited, the new values, e.g.",
        "#   edit: {category: administrative_burden}",
        "#   edit: {impact: 'Recurring costs of EUR 6 000-7 000 per year'}",
        f"# editable: impacts and candidates {', '.join(EDITABLE['impact'])};",
        f"#   omissions {', '.join(EDITABLE['omission'])}",
        "# human_added items (an analyst's, see 'raised by'): only edited or rejected/unclear;",
        "#   also editable: ia_anchor (the verbatim IA quote you found); never your own item",
        "# Details of each item: uv run python scripts/review_draft.py",
        f"reviewer: {yaml.safe_dump(reviewer or PLACEHOLDER).splitlines()[0]}",
        "decisions:",
    ]
    for item in items:
        summary = " | ".join(
            str(getattr(item, f)) for f in ("affected_actor", "description") if hasattr(item, f)
        )
        lines.append(f"  # {item_kind(item)}, {', '.join(todo[item.item_id])}: {summary[:70]}")
        if is_human_added(item):
            lines.append(f"  # raised by {item.provenance.raised_by}: decide edited (or reject)")
        lines += [f"  {item.item_id}:", "    decision:", "    note:", "    edit: {}"]
    if not items:
        lines.append("  {}  # nothing left to decide")
    return "\n".join(lines) + "\n"


def read_decisions(path: Path) -> tuple[str | None, dict[str, dict[str, Any]]]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise HelperError(f"cannot read decisions {path}: {exc}") from None
    if not isinstance(raw, dict) or not isinstance(raw.get("decisions") or {}, dict):
        raise HelperError(f"{path}: expected 'reviewer:' and a 'decisions:' mapping of item ids")
    decisions = {}
    for item_id, entry in (raw.get("decisions") or {}).items():
        if not isinstance(entry, dict):
            raise HelperError(f"{path}: {item_id}: expected decision, note, edit")
        if entry.get("decision") in (None, ""):
            continue  # skipped for now
        decisions[str(item_id)] = {k: v for k, v in entry.items() if v not in (None, {}, "")}
    reviewer = raw.get("reviewer")
    return (str(reviewer) if reviewer else None), decisions


def ask_edit(item: _DraftItem, ask: Ask) -> dict[str, Any]:
    data = item.model_dump(mode="json")
    edit: dict[str, Any] = {}
    for f in editable_fields(item):
        current = ", ".join(data[f]) if f == "provision_keys" else data[f]
        new = ask(f"  {f} [Enter keeps: {str(current)[:60]}]: ").strip()
        if new and new != current:
            edit[f] = (
                [k.strip() for k in new.split(",") if k.strip()] if f == "provision_keys" else new
            )
    return edit


def interactive(
    draft: GoldenDraft, drafts_dir: Path, ask: Ask, say: Say, reviewer: str | None = None
) -> dict[str, dict[str, Any]]:
    """Decisions for the undecided items that need a human; 's' skips, 'q' stops (keeping the
    decisions made so far). A ``human_added`` item raised by ``reviewer`` is skipped."""
    todo = needs(draft, drafts_dir)
    items = [i for i in draft.items() if i.item_id in todo and not is_done(i)]
    decisions: dict[str, dict[str, Any]] = {}
    for n, item in enumerate(items, 1):
        say("")
        say(f"[{n}/{len(items)}] " + "\n".join(describe(item, todo[item.item_id], draft)))
        human = is_human_added(item)
        if human and reviewer and _person(item.provenance.raised_by) == _person(reviewer):
            say("  you raised this item; someone else reviews it (skipped)")
            continue
        while True:
            try:
                key = ask("[v]erified [e]dited [r]ejected [u]nclear [s]kip [q]uit: ")
            except EOFError:
                return decisions
            key = key.strip().lower()[:1]
            if key == "q":
                return decisions
            if key == "s":
                break
            if key not in SHORTCUTS:
                continue
            if human and key == "v":
                say("  a human_added item is kept only as 'edited': choose e and fill it in")
                continue
            entry: dict[str, Any] = {"decision": SHORTCUTS[key]}
            if key == "e":
                entry["edit"] = ask_edit(item, ask)
                if not entry["edit"] and not human:
                    say("  no field changed; choose verified if the item is right as it is")
                    continue
            required = SHORTCUTS[key] in NOTE_REQUIRED
            note = ask("  note" + (" (required)" if required else " (optional)") + ": ").strip()
            if required and not note:
                say("  a note is required for this decision")
                continue
            if note:
                entry["note"] = note
            decisions[item.item_id] = entry
            break
    return decisions


def check_reviewer(draft: GoldenDraft, reviewer: str | None) -> None:
    """Refuse a bad reviewer before any work, not after the last decision."""
    name = (reviewer or "").strip().lstrip("@")
    if not _valid_reviewer(name):
        raise HelperError(f"reviewer {reviewer!r} is not a GitHub username; {USERNAME_HINT}")
    if _person(name) == _person(draft.provenance.drafted_by):
        raise HelperError(f"{name} drafted this case; the drafter cannot be the reviewer")


def write_decisions(
    path: Path, decisions: dict[str, dict[str, Any]], reviewer: str | None, say: Say
) -> None:
    if not decisions:
        say("no decisions to write")
        return
    if not reviewer or reviewer == PLACEHOLDER:
        raise HelperError(
            "who is reviewing? pass --reviewer <your-github-username> (or set 'reviewer:' in "
            "the decisions file, or git config github.user)"
        )
    try:
        apply_decisions_in_place(path, decisions, reviewer)
    except DraftEditError as exc:
        raise HelperError(str(exc)) from None
    say(f"wrote {len(decisions)} decision(s) by {reviewer} into {_display(path)}")


# ----------------------------------------------------------------------------- checking


def check(path: Path, drafts_dir: Path, say: Say) -> bool:
    """The CI gate, both ways: on a draft PR now, and once the PR is marked ready."""
    strict = review_gate(path, allow_pending=False, drafts_dir=drafts_dir)
    lenient = review_gate(path, allow_pending=True, drafts_dir=drafts_dir)
    say(f"CI on the draft PR: {'passes' if not lenient else 'FAILS'}")
    for p in lenient:
        say("  - " + _short(p))
    if not strict:
        say("Ready: every decision is made. Commit, push, and mark the PR ready for review.")
        return True
    todo = [p for p in strict if p not in lenient]
    say(f"Before you mark the PR ready ({len(todo)} to do):")
    for p in todo:
        say("  - " + _short(p))
    return False


def _short(problem: str) -> str:
    """'case_12_x/c12_e01: ...' -> 'c12_e01: ...' (the case is printed once already)."""
    head, sep, rest = problem.partition(": ")
    return head.split("/", 1)[-1] + sep + rest if "/" in head else problem


# ----------------------------------------------------------------------------- main


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.splitlines()[2:7]),
    )
    parser.add_argument("draft", nargs="?", help="draft path or case id (default: the only one)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--interactive", "-i", action="store_true", help="decide item by item")
    mode.add_argument(
        "--template",
        nargs="?",
        const="",
        metavar="OUT",
        help="write a decisions file to fill in (default .cache/review/<case>.decisions.yaml)",
    )
    mode.add_argument("--apply", type=Path, metavar="FILE", help="apply a filled decisions file")
    mode.add_argument("--check", action="store_true", help="run the CI check and explain it")
    parser.add_argument("--all", action="store_true", help="list every item, not only yours")
    parser.add_argument("--reviewer", help="your GitHub username (default: git config github.user)")
    parser.add_argument("--drafts-dir", type=Path, default=DRAFTS_DIR, help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def run(args: argparse.Namespace, ask: Ask, say: Say) -> int:
    path = resolve_draft(args.draft, args.drafts_dir)
    if args.check:
        return 0 if check(path, args.drafts_dir, say) else 1
    draft = load(path)
    if args.template is not None:
        out = (
            Path(args.template)
            if args.template
            else TEMPLATE_DIR / f"{draft.case_id}.decisions.yaml"
        )
        if out.resolve().parent == args.drafts_dir.resolve():
            raise HelperError("write the decisions file outside evals/golden/drafts/")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(template(draft, path, args.drafts_dir, args.reviewer or git_user()))
        say(f"decisions file: {_display(out)}\nfill it in, then: --apply {_display(out)}")
        return 0
    if args.apply:
        file_reviewer, decisions = read_decisions(args.apply)
        write_decisions(path, decisions, args.reviewer or file_reviewer or git_user(), say)
        check(path, args.drafts_dir, say)
        return 0
    if args.interactive:
        reviewer = args.reviewer or git_user() or ask("your GitHub username: ").strip()
        check_reviewer(draft, reviewer)
        decisions = interactive(draft, args.drafts_dir, ask, say, reviewer)
        write_decisions(path, decisions, reviewer, say)
        check(path, args.drafts_dir, say)
        return 0
    list_items(draft, path, args.drafts_dir, args.all, say)
    return 0


def main(argv: list[str] | None = None, *, ask: Ask = input, say: Say = print) -> int:
    args = parse_args(argv)
    try:
        return run(args, ask, say)
    except HelperError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
