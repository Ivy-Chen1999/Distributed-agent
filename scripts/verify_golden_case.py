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

**Spot check.** Once a reference check of the tool chain has passed (``scripts/reference_check.py
score`` says ROBUST), a holdout draft settled by the tool may be spot-checked instead:

    uv run python scripts/tiebreak_golden_case.py .cache/drafts/case_20_x.yaml
    uv run python scripts/verify_golden_case.py --draft .cache/drafts/case_20_x.yaml \
        --spot-check --reference-check <reference-check round dir>

The round's answers are re-scored here (a ROBUST verdict is required, not trusted), every item
must have been settled by the judges or the tie-break, and only a seeded sample of
``SPOT_SAMPLE`` tool-decided items, spread over judges-accepted, tie-break-kept and
tie-break-dropped like the reference check, is asked, interactively. Disagreeing with the tool
on more than ``SPOT_MAX_DISAGREEMENTS`` of them refuses the spot check: verify every item
instead. Otherwise the tool's decisions stand beside the owner's, and the handoff records the
spot check (sample, disagreements, reference-check sample) in the case notes and in
``spot_check``.

The verified case goes to a handoff file in .cache/holdout_import/ for the holdout importer
(U6). The draft must be read from, and the handoff written to, a path under .cache/
(gitignored); anything else is refused. Everything runs with LangSmith tracing disabled.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
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
from womm.eval import reference_check as rc
from womm.eval.drafting import (
    EVALS_DIR,
    DraftingError,
    GoldenDraft,
    _DraftItem,
    anchor_context,
    audit_seed_for,
    load_draft,
    require_under_cache,
)
from womm.eval.golden import GoldenCase
from womm.eval.golden_review import (
    AUDIT_ERRORS,
    EDITABLE,
    KEPT_DECISIONS,
    NOTE_REQUIRED,
    ReviewError,
    audit_result,
    build_case,
    check_draft,
    decide_item,
    human_reasons,
    item_kind,
)
from womm.eval.holdout import HANDOFF_FORMAT
from womm.eval.tiebreak import needs_tiebreak

HANDOFF_DIR = REPO_ROOT / ".cache" / "holdout_import"
CLAIM_FIELDS = ("affected_actor", "mechanism", "impact", "description")
SHORTCUTS = {"v": "verified", "e": "edited", "r": "rejected", "u": "unclear"}
SPOT_SAMPLE = 10
SPOT_MAX_DISAGREEMENTS = 1
TOOL_DECISIONS = ("auto_accepted", "llm_kept", "llm_dropped")

Ask = Callable[[str], str]
Say = Callable[[str], None]


class VerifyError(RuntimeError):
    pass


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def _refuse_under_evals(path: Path, what: str) -> None:
    """Holdout material only under .cache/ (gitignored), never under evals/ or elsewhere."""
    if path.resolve().is_relative_to(EVALS_DIR.resolve()):
        raise VerifyError(f"refusing {what} under evals/ ({path}); holdout material stays local")
    try:
        require_under_cache(path, what)
    except DraftingError as exc:
        raise VerifyError(str(exc)) from None


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
    try:
        return decide_item(item, entry, reviewer)
    except ReviewError as exc:
        raise VerifyError(str(exc)) from None


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
    return _decide(draft, decisions, reviewer)


def _decide(draft: GoldenDraft, decisions: dict[str, dict[str, Any]], reviewer: str) -> GoldenDraft:
    """The items in ``decisions`` decided by ``reviewer``; the others as they are."""

    def decide(items: list) -> list:
        return [
            _decided_item(i, decisions[i.item_id], reviewer) if i.item_id in decisions else i
            for i in items
        ]

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


# ----------------------------------------------------------------------------- spot check


def robust_reference_check(directory: Path) -> str:
    """The sample id of the reference check in ``directory``, re-scored here from its answers;
    refused unless the verdict is ROBUST."""
    try:
        key = json.loads((directory / rc.KEY_FILE).read_text(encoding="utf-8"))
        labels, _ = rc.read_answers(directory, key)
        result = rc.score(key, labels)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ZeroDivisionError) as exc:
        raise VerifyError(f"cannot score the reference check in {directory}: {exc}") from None
    if not result["robust"]:
        why = (
            f"{len(result['blank'])} items unlabelled"
            if result["blank"]
            else f"tool error {result['error']:.1%} over the {rc.MAX_ERROR:.0%} limit"
        )
        raise VerifyError(
            f"the reference check {key['sample']} is not ROBUST ({why}); no spot check: verify "
            "every item (run without --spot-check)"
        )
    return str(key["sample"])


def unsettled(draft: GoldenDraft) -> list[str]:
    """Items the tool has not settled: still open for the tie-break, needing a person under the
    train/val rule, or decided other than by the judges or the tie-break."""
    return [
        i.item_id
        for i in draft.items()
        if needs_tiebreak(i) or human_reasons(i) or i.review.decision not in TOOL_DECISIONS
    ]


def require_settled(draft: GoldenDraft) -> None:
    if draft.provenance.tiebreak_audit is None:
        raise VerifyError(
            f"{draft.case_id}: not tie-broken; run scripts/tiebreak_golden_case.py on the draft "
            "first, or verify every item (run without --spot-check)"
        )
    if open_ := unsettled(draft):
        raise VerifyError(
            f"{draft.case_id}: items not settled by the tool: {open_}; no spot check, verify "
            "every item (run without --spot-check)"
        )


def spot_seed_for(case_id: str) -> int:
    return audit_seed_for(f"{case_id}#spot")


def spot_sample(draft: GoldenDraft) -> list[_DraftItem]:
    """Up to ``SPOT_SAMPLE`` tool-decided items, stratified like the reference check (items
    dropped by a deterministic check carry no judgement and are left out), in draft order."""
    pool = [
        rc.Item(draft.case_id, draft.fixture, i.item_id, s, i.review.decision in KEPT_DECISIONS)
        for i in draft.items()
        if (s := rc.stratum(i)) is not None
    ]
    picked = {
        p.item_id for p in rc.stratified_sample(pool, SPOT_SAMPLE, spot_seed_for(draft.case_id))
    }
    return [i for i in draft.items() if i.item_id in picked]


def disagrees(item: _DraftItem, entry: dict[str, Any]) -> bool:
    """The owner's decision against the tool's: a kept item edited, rejected or unclear, or a
    dropped item kept (verified or edited)."""
    if item.review.decision in KEPT_DECISIONS:
        return entry["decision"] in AUDIT_ERRORS
    return entry["decision"] in ("verified", "edited")


def spot_verify(
    draft: GoldenDraft,
    decisions: dict[str, dict[str, Any]],
    reviewer: str,
    today: dt.date,
    reference_sample: str,
) -> tuple[GoldenCase, dict[str, Any]]:
    """The case with the owner's sample decisions over the tool's, and the spot-check record;
    refused when the owner disagrees with the tool on more than ``SPOT_MAX_DISAGREEMENTS``."""
    if not reviewer.strip():
        raise VerifyError("a reviewer name is required")
    require_settled(draft)
    sample = {i.item_id: i for i in spot_sample(draft)}
    if not sample:
        raise VerifyError(f"{draft.case_id}: no tool-decided item to spot-check")
    if sorted(decisions) != sorted(sample):
        raise VerifyError(
            f"{draft.case_id}: decisions must cover exactly the sample {sorted(sample)}"
        )
    disagreed = sorted(iid for iid, entry in decisions.items() if disagrees(sample[iid], entry))
    if len(disagreed) > SPOT_MAX_DISAGREEMENTS:
        raise VerifyError(
            f"{draft.case_id}: you disagreed with the tool on {len(disagreed)} of {len(sample)} "
            f"sampled items {disagreed} (limit {SPOT_MAX_DISAGREEMENTS}); spot check refused, "
            "nothing written. Verify every item: run without --spot-check"
        )
    record = {
        "sample_size": len(sample),
        "seed": spot_seed_for(draft.case_id),
        "items": sorted(sample),
        "disagreements": disagreed,
        "reference_check": reference_sample,
    }
    note = (
        f"Holdout spot check by {reviewer.strip()}: {len(sample)} tool-decided items sampled "
        f"(seed {record['seed']}), disagreements with the tool: {disagreed or 'none'}; the "
        f"other items stand as the tool settled them. Reference check {reference_sample} was "
        "ROBUST."
    )
    decided = _decide(draft, decisions, reviewer.strip())
    decided = decided.model_copy(
        update={"notes": " ".join(n for n in (decided.notes.strip(), note) if n)}
    )
    problems = check_draft(
        decided, fixture_keys(decided), holdout_spot_check=True, reviewer_pattern=None
    )
    if problems:
        raise VerifyError("spot check incomplete:\n  " + "\n  ".join(problems))
    try:
        case = build_case(
            decided, published_on=today, audit=audit_result([decided]), action="spot-checked"
        )
    except ReviewError as exc:
        raise VerifyError(str(exc)) from None
    return case, record


def write_handoff(
    case: GoldenCase,
    draft_path: Path,
    reviewer: str,
    today: dt.date,
    out_dir: Path,
    spot_check: dict[str, Any] | None = None,
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
        **({"spot_check": spot_check} if spot_check else {}),
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
    draft: GoldenDraft, ask: Ask, say: Say, ia: Any = None, items: list[_DraftItem] | None = None
) -> dict[str, dict[str, Any]]:
    """Ask for a decision on every item (or on ``items`` only); ``q`` aborts without writing."""
    decisions: dict[str, dict[str, Any]] = {}
    items = draft.items() if items is None else items
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
    parser.add_argument(
        "--spot-check",
        action="store_true",
        help="ask only a sample of a tie-broken draft (needs --reference-check; interactive)",
    )
    parser.add_argument(
        "--reference-check", type=Path, help="reference-check round dir that scores ROBUST"
    )
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
    if args.spot_check:
        return run_spot_check(args, interactive=interactive, ask=ask, say=say)
    if args.reference_check:
        raise VerifyError("--reference-check goes with --spot-check")
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


def run_spot_check(args: argparse.Namespace, *, interactive: bool, ask: Ask, say: Say) -> Path:
    if not args.reference_check:
        raise VerifyError("--spot-check needs --reference-check <reference-check round dir>")
    if args.decisions or not interactive:
        raise VerifyError("a spot check is interactive only (no --decisions, needs a terminal)")
    draft = load_holdout_draft(args.draft)
    reference_sample = robust_reference_check(args.reference_check)
    require_settled(draft)
    today = args.today or dt.date.today()
    reviewer = args.reviewer or ask("reviewer name: ").strip()
    sample = spot_sample(draft)
    say(f"spot check: {len(sample)} of {len(draft.items())} items, reference check "
        f"{reference_sample} ROBUST")  # fmt: skip
    ia = ia_sources.load_cached_ia(draft.fixture, args.ia_root)
    decisions = interactive_decisions(draft, ask, say, ia, items=sample)
    case, record = spot_verify(draft, decisions, reviewer, today, reference_sample)
    path = write_handoff(case, args.draft, reviewer, today, args.out_dir, spot_check=record)
    print(f"{case.case_id}: {len(case.expected_impacts)} impacts, "
          f"{len(case.important_omissions)} omissions; spot check of {record['sample_size']} "
          f"items, {len(record['disagreements'])} disagreement(s) -> {path}")  # fmt: skip
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
