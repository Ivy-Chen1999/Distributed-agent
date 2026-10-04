"""Human review of golden-case drafts and their publication (U5, plan Revision 2026-10-04).

Humans only where an LLM cannot be trusted. An item needs a human decision when:

- the judges did not all agree (``judge.overall`` is ``disagree`` or ``uncertain``);
- a deterministic check flagged it (``flags``: anchor not found, unknown provision key, ...);
- it is in the random audit sample (``review.audit: true``);
- it is a ``possibly_missing`` candidate (adding it to the case is a human call);
- its proposal is escalated: more than 10% of the decided audit items of that fixture were
  edited, rejected or marked unclear, so every item of the proposal goes to a human.

Auto-accepted, un-audited items of a non-escalated proposal need nothing. A human decision is
``verified``, ``edited`` (the fields were changed in place), ``rejected`` or ``unclear``; it
needs a reviewer. ``rejected`` and ``unclear`` items are dropped when publishing: an item a
human finds unclear is not an unambiguous task, so it is not kept as a coin flip.

``check_draft`` returns the problems that block a draft; ``build_case`` turns a fully decided
draft into a ``GoldenCase``. The CI test (``tests/eval/test_golden_drafts.py``), the publish
script and the local holdout verification script all use these functions.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from womm.eval.drafting import (
    DIMENSIONS,
    DraftCandidate,
    DraftImpact,
    DraftOmission,
    GoldenDraft,
    _DraftItem,
    overall,
)
from womm.eval.golden import ExpectedImpact, GoldenCase, Omission

HUMAN_DECISIONS = ("verified", "edited", "rejected", "unclear")
KEPT_DECISIONS = ("auto_accepted", "verified", "edited")
DROPPED_DECISIONS = ("rejected", "unclear")
AUDIT_ERRORS = ("edited", "rejected", "unclear")
NOTE_REQUIRED = ("rejected", "unclear")
ESCALATION_RATE = 0.10
MIN_KEPT_IMPACTS = 3
# GitHub usernames: alphanumerics and single inner hyphens, at most 39 characters.
GITHUB_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")


class ReviewError(ValueError):
    pass


def item_kind(item: _DraftItem) -> str:
    if isinstance(item, DraftImpact):
        return "impact"
    if isinstance(item, DraftOmission):
        return "omission"
    return "candidate"


def human_reasons(
    item: _DraftItem, *, escalated: bool = False, everything: bool = False
) -> list[str]:
    """Why ``item`` needs a human decision; empty when an auto-acceptance stands.

    ``everything`` is the holdout rule: every item is verified by a human."""
    reasons = []
    if item.judge.overall != "agree":
        reasons.append(f"judge {item.judge.overall}")
    if item.flags:
        reasons.append("flagged: " + "; ".join(item.flags))
    if item.review.audit:
        reasons.append("audit sample")
    if isinstance(item, DraftCandidate):
        reasons.append("possibly_missing candidate")
    if item.provenance.origin == "human_added":
        reasons.append("human added")
    if escalated:
        reasons.append("proposal escalated (audit error rate over 10%)")
    if everything:
        reasons.append("holdout: every item is verified by a human")
    if not reasons and item.review.decision == "pending":
        reasons.append("left pending by the drafting tool")
    return reasons


@dataclass(frozen=True)
class AuditResult:
    sampled: int
    decided: int
    errors: int

    @property
    def rate(self) -> float | None:
        return self.errors / self.decided if self.decided else None

    @property
    def escalated(self) -> bool:
        return self.rate is not None and self.rate > ESCALATION_RATE


def audit_result(drafts: Iterable[GoldenDraft]) -> AuditResult:
    """Audit outcome over drafts of one proposal (fixture)."""
    sampled = decided = errors = 0
    for draft in drafts:
        for item in draft.items():
            if not item.review.audit:
                continue
            sampled += 1
            if item.review.decision in HUMAN_DECISIONS:
                decided += 1
                errors += item.review.decision in AUDIT_ERRORS
    return AuditResult(sampled, decided, errors)


def escalated_fixtures(drafts: Iterable[GoldenDraft]) -> set[str]:
    by_fixture: dict[str, list[GoldenDraft]] = {}
    for d in drafts:
        by_fixture.setdefault(d.fixture, []).append(d)
    return {f for f, ds in by_fixture.items() if audit_result(ds).escalated}


def _valid_reviewer(reviewer: str | None) -> bool:
    return bool(reviewer) and bool(GITHUB_USERNAME.fullmatch(reviewer.lstrip("@")))


def check_draft(
    draft: GoldenDraft,
    scenario_keys: set[str] | None,
    *,
    allow_pending: bool = False,
    escalated: bool = False,
    everything: bool = False,
    reviewer_pattern: re.Pattern[str] | None = GITHUB_USERNAME,
) -> list[str]:
    """Problems that block ``draft``, each prefixed ``<case_id>/<item_id>``.

    ``scenario_keys`` are the provision keys an item may cite (None skips the key check).
    ``allow_pending`` is the draft-PR exemption: undecided items and missing reviewers are
    tolerated, malformed decisions are not. ``reviewer_pattern`` None accepts any non-empty
    reviewer name (local holdout verification)."""
    cid = draft.case_id
    problems: list[str] = []
    if draft.split == "holdout" and not everything:
        problems.append(f"{cid}: split 'holdout' never goes through a PR; use verify_golden_case")
    ids = [i.item_id for i in draft.items()]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{cid}/{dup}: duplicate item id")
    # The tool-written blocks must not be edited to dodge a review.
    sampled = set(draft.provenance.audit.sampled)
    for item in draft.items():
        dims = {d: getattr(item.judge, d) for d in DIMENSIONS}
        if item.judge.overall != overall(dims):
            problems.append(f"{cid}/{item.item_id}: judge.overall does not match the verdicts")
        if item.review.audit != (item.item_id in sampled):
            problems.append(f"{cid}/{item.item_id}: review.audit does not match the audit sample")
    for item in draft.items():
        where = f"{cid}/{item.item_id}"
        rev = item.review
        reasons = human_reasons(item, escalated=escalated, everything=everything)
        needs_human = bool(reasons)
        decided = rev.decision in HUMAN_DECISIONS
        if rev.reviewer is not None:
            ok = (
                _valid_reviewer(rev.reviewer)
                if reviewer_pattern is not None
                else bool(rev.reviewer.strip())
            )
            if not ok:
                problems.append(f"{where}: reviewer {rev.reviewer!r} is not a GitHub username")
        if needs_human and not decided:
            if rev.decision == "auto_accepted" and not allow_pending:
                problems.append(
                    f"{where}: needs a human decision ({', '.join(reasons)}), not auto_accepted"
                )
            elif rev.decision == "pending" and not allow_pending:
                problems.append(f"{where}: pending ({', '.join(reasons)})")
        if decided and not rev.reviewer and not allow_pending:
            problems.append(f"{where}: decision {rev.decision!r} has no reviewer")
        if rev.decision in NOTE_REQUIRED and not (rev.note or "").strip() and not allow_pending:
            problems.append(f"{where}: decision {rev.decision!r} needs a short note saying why")
        kept_or_open = rev.decision not in DROPPED_DECISIONS
        if kept_or_open and not (allow_pending and rev.decision == "pending"):
            if not item.provision_keys:
                problems.append(f"{where}: provision_keys is empty")
            elif scenario_keys is not None:
                unknown = [k for k in item.provision_keys if k not in scenario_keys]
                if unknown:
                    problems.append(
                        f"{where}: provision_keys {unknown} are not in scenario {draft.scenario_id}"
                    )
    if not allow_pending and not problems:
        kept = len(kept_impacts(draft))
        if kept < MIN_KEPT_IMPACTS:
            problems.append(
                f"{cid}: only {kept} expected impacts would be kept (minimum {MIN_KEPT_IMPACTS})"
            )
    return problems


def kept_impacts(draft: GoldenDraft) -> list[DraftImpact | DraftCandidate]:
    """Impacts and human-accepted candidates that a publish would keep."""
    impacts = [i for i in draft.expected_impacts if i.review.decision in KEPT_DECISIONS]
    candidates = [c for c in draft.possibly_missing if c.review.decision in ("verified", "edited")]
    return [*impacts, *candidates]


def _provenance(item: _DraftItem) -> str:
    if isinstance(item, DraftCandidate):
        return "human_confirmed_candidate"
    return {
        "auto_accepted": "llm_judged",
        "verified": "human_verified",
        "edited": "human_edited",
    }[item.review.decision]


def review_notes(
    draft: GoldenDraft, published_on: dt.date, audit: AuditResult, action: str = "published"
) -> str:
    """The review record appended to the case notes: who decided what, and when."""
    items = draft.items()
    reviewers = sorted({i.review.reviewer.lstrip("@") for i in items if i.review.reviewer})
    dropped = {d: [i.item_id for i in items if i.review.decision == d] for d in DROPPED_DECISIONS}
    declined = [c.candidate_id for c in draft.possibly_missing if c.review.decision == "rejected"]
    counts = {
        d: sum(i.review.decision == d for i in items) for d in (*KEPT_DECISIONS, *DROPPED_DECISIONS)
    }
    p = draft.provenance
    parts = [
        f"Drafted on {p.drafted_on.isoformat()} by {p.drafting_model} ({p.tool}) and checked by "
        "isolated LLM judges.",
        f"Human review by {', '.join(reviewers) or 'nobody (all items auto-accepted)'}; "
        f"{action} on {published_on.isoformat()}.",
        "Decisions: " + ", ".join(f"{k} {v}" for k, v in counts.items()) + ".",
        f"Audit: {audit.decided}/{audit.sampled} sampled items decided, {audit.errors} "
        "edited/rejected/unclear"
        + (" (escalated: every item reviewed by a human)." if audit.escalated else "."),
    ]
    if dropped["rejected"] or dropped["unclear"]:
        parts.append(
            f"Dropped: rejected {dropped['rejected'] or 'none'}, unclear (ambiguous, not kept) "
            f"{dropped['unclear'] or 'none'}."
        )
    if declined:
        parts.append(f"Declined candidates: {declined}.")
    return " ".join(parts)


def build_case(
    draft: GoldenDraft,
    *,
    published_on: dt.date,
    audit: AuditResult,
    action: str = "published",
) -> GoldenCase:
    """The published case: kept items only, edits as written, review record in ``notes``.

    The caller has already run ``check_draft`` strictly; this refuses too few kept impacts."""
    impacts = [
        ExpectedImpact(
            expected_id=i.item_id,
            affected_actor=i.affected_actor,
            mechanism=i.mechanism,
            impact=i.impact,
            provision_keys=list(i.provision_keys),
            ia_section=i.ia_section,
            category=i.category,
            provenance=_provenance(i),  # type: ignore[arg-type]
        )
        for i in kept_impacts(draft)
    ]
    if len(impacts) < MIN_KEPT_IMPACTS:
        raise ReviewError(
            f"{draft.case_id}: only {len(impacts)} expected impacts kept "
            f"(minimum {MIN_KEPT_IMPACTS}); not published"
        )
    omissions = [
        Omission(
            omission_id=o.omission_id,
            description=o.description,
            provision_keys=list(o.provision_keys),
            source=("RSB opinion" if o.source == "rsb" else "Impact assessment")
            + f", {o.ia_section}",
            category=o.category,
            provenance=_provenance(o),  # type: ignore[arg-type]
        )
        for o in draft.important_omissions
        if o.review.decision in KEPT_DECISIONS
    ]
    notes = " ".join(
        n for n in (draft.notes.strip(), review_notes(draft, published_on, audit, action)) if n
    )
    return GoldenCase(
        case_id=draft.case_id,
        scenario_id=draft.scenario_id,
        ia_reference=draft.ia_reference,
        fixture=draft.fixture,
        split=draft.split,
        notes=notes,
        expected_impacts=impacts,
        important_omissions=omissions,
    )


def case_yaml_header(source: Path | str) -> str:
    return (
        f"# Published by scripts/publish_golden_cases.py from {source} after human review.\n"
        "# Items record their provenance (llm_judged, human_verified, human_edited,\n"
        "# human_confirmed_candidate); notes record the reviewers and dates.\n"
    )


def scenario_keys_for(draft: GoldenDraft) -> set[str]:
    """Provision keys of the draft's scenario in its public fixture."""
    from womm.data.fixtures import FixtureError, fixture_dir, load_fixture

    try:
        scenario = load_fixture(fixture_dir(draft.fixture)).scenario(draft.scenario_id)
    except (FixtureError, KeyError, ValueError) as exc:
        raise ReviewError(f"{draft.case_id}: {exc}") from None
    return set(scenario.provision_keys)
