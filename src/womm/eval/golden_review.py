"""Human review of golden-case drafts and their publication (U5, plan Revision 2026-10-04).

Humans only where an LLM cannot be trusted. An item needs a human decision when:

- the judges did not all agree (``judge.overall`` is ``disagree`` or ``uncertain``);
- a deterministic check flagged it (``flags``: anchor not found, unknown provision key, ...);
- it is in the random audit sample (``review.audit: true``);
- it is a ``possibly_missing`` candidate (adding it to the case is a human call);
- its proposal is escalated: more than 10% of the decided audit items of that fixture were
  edited, rejected or marked unclear, so every item of the proposal goes to a human. The count
  spans PRs: published drafts leave their audit counts in ``evals/golden/audit_tally.yaml``
  (counts per fixture only), which ``escalated_fixtures`` adds to the open drafts.

Auto-accepted, un-audited items of a non-escalated proposal need nothing. A human decision is
``verified``, ``edited`` (the fields were changed in place), ``rejected`` or ``unclear``; it
needs a reviewer. ``rejected`` and ``unclear`` items are dropped when publishing: an item a
human finds unclear is not an unambiguous task, so it is not kept as a coin flip.

The gate does not trust author-editable fields. ``check_draft`` recomputes each item's digest of
its tool-written fields and fails when one changed while the item is ``auto_accepted`` or
``verified`` (a changed item must be ``edited``, with a reviewer); recomputes the audit sample as
``draw_audit(eligible_ids, 0.2, audit_seed_for(case_id))``; refuses a draft whose rate is not the
pinned 0.2 or that was drafted with a test-only seed; and refuses the drafter as a reviewer.

``check_draft`` returns the problems that block a draft; ``build_case`` turns a fully decided
draft into a ``GoldenCase``. The CI test (``tests/eval/test_golden_drafts.py``), the publish
script and the local holdout verification script all use these functions. ``review_gate`` is
the CI check of one draft file (also run by ``scripts/review_draft.py --check``): a file that
does not parse is explained by item id and line rather than by a raw parser error.
``decide_item`` applies one reviewer decision; both review scripts use it. Every problem names
the item and says what to do, because the readers are often first-time reviewers.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from womm.config import REPO_ROOT
from womm.eval.drafting import (
    AUDIT_RATE,
    DIMENSIONS,
    DRAFTS_DIR,
    DraftCandidate,
    DraftImpact,
    DraftOmission,
    GoldenDraft,
    Review,
    _DraftItem,
    analyst_digest,
    audit_eligible,
    audit_seed_for,
    check_anchor,
    draw_audit,
    item_digest,
    load_draft,
    overall,
)
from womm.eval.golden import GOLDEN_DIR, ExpectedImpact, GoldenCase, Omission

HUMAN_DECISIONS = ("verified", "edited", "rejected", "unclear")
KEPT_DECISIONS = ("auto_accepted", "verified", "edited")
DROPPED_DECISIONS = ("rejected", "unclear")
AUDIT_ERRORS = ("edited", "rejected", "unclear")
NOTE_REQUIRED = ("rejected", "unclear")
ESCALATION_RATE = 0.10
MIN_KEPT_IMPACTS = 3
# GitHub usernames: alphanumerics and single inner hyphens, at most 39 characters.
GITHUB_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
# Audit counts of published drafts, per fixture (train/val only); see ``escalated_fixtures``.
AUDIT_TALLY_PATH = GOLDEN_DIR / "audit_tally.yaml"
# The gitignored local registry of holdout proposals (the same file womm.eval.holdout reads).
HOLDOUT_REGISTRY = REPO_ROOT / "evals" / "private" / "holdout_scenarios.yaml"
DECIDE = "set review.decision to verified, edited, rejected or unclear"
USERNAME_HINT = "use your GitHub username (as in github.com/<username>), not your display name"
# Fields a reviewer may change with an 'edited' decision, per item kind.
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
DECIDED_STATUS = {"verified": "human_verified", "edited": "human_edited"}


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

    def __add__(self, other: AuditResult) -> AuditResult:
        return AuditResult(
            self.sampled + other.sampled, self.decided + other.decided, self.errors + other.errors
        )

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


Tally = dict[str, AuditResult]


def fixture_audit(
    fixture: str, drafts: Iterable[GoldenDraft], tally: Mapping[str, AuditResult] | None = None
) -> AuditResult:
    """Audit outcome of one proposal: its open drafts plus its published (tallied) ones."""
    current = audit_result(d for d in drafts if d.fixture == fixture)
    return current + (tally or {}).get(fixture, AuditResult(0, 0, 0))


def escalated_fixtures(
    drafts: Iterable[GoldenDraft], tally: Mapping[str, AuditResult] | None = None
) -> set[str]:
    """Fixtures whose audit error rate, over open drafts and the tally, is over 10%."""
    drafts = list(drafts)
    fixtures = {d.fixture for d in drafts} | set(tally or {})
    return {f for f in fixtures if fixture_audit(f, drafts, tally).escalated}


class TallyError(ValueError):
    pass


TALLY_HEADER = (
    "# Audit outcomes of published golden-case drafts, per proposal fixture: counts only.\n"
    "# Written by scripts/publish_golden_cases.py; read by the review gate so that escalation\n"
    "# (audit error rate over 10%) spans PRs. Holdout proposals never appear here.\n"
)


def load_audit_tally(path: Path = AUDIT_TALLY_PATH) -> Tally:
    """fixture -> AuditResult; empty when no draft has been published yet."""
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise TallyError(f"cannot read {path}: {exc}") from None
    fixtures = raw.get("fixtures") if isinstance(raw, dict) else None
    if not isinstance(fixtures, dict):
        raise TallyError(f"{path}: expected a 'fixtures' mapping of fixture -> counts")
    out: Tally = {}
    for name, counts in fixtures.items():
        if not isinstance(counts, dict) or set(counts) != {"sampled", "decided", "errors"}:
            raise TallyError(f"{path}: {name}: expected exactly sampled, decided, errors")
        values = [counts[k] for k in ("sampled", "decided", "errors")]
        if not all(isinstance(v, int) and v >= 0 for v in values):
            raise TallyError(f"{path}: {name}: counts must be non-negative integers")
        sampled, decided, errors = values
        if not errors <= decided <= sampled:
            raise TallyError(f"{path}: {name}: need errors <= decided <= sampled")
        out[str(name)] = AuditResult(sampled, decided, errors)
    return out


def write_audit_tally(
    tally: Mapping[str, AuditResult],
    path: Path = AUDIT_TALLY_PATH,
    *,
    holdout: Iterable[str] = (),
) -> None:
    """Write the tally (sorted, counts only); refuses a holdout fixture."""
    sealed = sorted(set(tally) & set(holdout))
    if sealed:
        raise TallyError("refusing to write a holdout proposal to the public audit tally")
    data = {
        "fixtures": {
            f: {"sampled": r.sampled, "decided": r.decided, "errors": r.errors}
            for f, r in sorted(tally.items())
        }
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TALLY_HEADER + yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def holdout_fixtures(path: Path = HOLDOUT_REGISTRY) -> set[str]:
    """Fixture ids registered as holdout proposals in the gitignored local registry; empty when
    the registry is absent (a machine without holdout material)."""
    if not path.exists():
        return set()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ReviewError(f"cannot read the local holdout registry: {exc}") from None
    if not isinstance(raw, dict):
        raise ReviewError("the local holdout registry is not a mapping of fixture ids")
    return {str(k) for k in raw}


def refuse_holdout_fixture(fixture: str, what: str, registry: Path = HOLDOUT_REGISTRY) -> None:
    """Raise when ``fixture`` is sealed as holdout; names only that fixture."""
    if fixture in holdout_fixtures(registry):
        raise ReviewError(
            f"fixture {fixture!r} is registered as a holdout proposal in the local registry; "
            f"{what} refused (all cases of one proposal share a split)"
        )


def _valid_reviewer(reviewer: str | None) -> bool:
    return bool(reviewer) and bool(GITHUB_USERNAME.fullmatch(reviewer.lstrip("@")))


def _person(name: str | None) -> str:
    return (name or "").strip().lstrip("@").casefold()


def integrity_problems(draft: GoldenDraft) -> list[str]:
    """Tool-written fields, the audit sample and its parameters, recomputed (author-editable
    fields are not trusted)."""
    cid = draft.case_id
    p = draft.provenance
    audit = p.audit
    problems: list[str] = []
    if audit.non_publishable_test_seed:
        problems.append(f"{cid}: drafted with a test-only audit seed; not publishable, re-draft")
    if audit.rate != AUDIT_RATE:
        problems.append(f"{cid}: audit rate {audit.rate} is not the pinned {AUDIT_RATE}")
    seed = audit_seed_for(cid)
    if audit.seed != seed and not audit.non_publishable_test_seed:
        problems.append(f"{cid}: audit seed is not the one derived from the case id")
    if audit.eligible != len(audit.eligible_ids):
        problems.append(f"{cid}: audit.eligible does not count audit.eligible_ids")
    if sorted(audit.sampled) != draw_audit(audit.eligible_ids, AUDIT_RATE, seed):
        problems.append(
            f"{cid}: audit sample does not match draw_audit(eligible_ids, {AUDIT_RATE}, "
            "audit_seed_for(case_id))"
        )
    by_id = {i.item_id: i for i in draft.items()}
    eligible = set(audit.eligible_ids)
    for eid in sorted(eligible):
        if eid not in by_id or isinstance(by_id[eid], DraftCandidate):
            problems.append(f"{cid}/{eid}: audit-eligible id is not an impact or omission")
    for item in draft.items():
        where = f"{cid}/{item.item_id}"
        tool_item = item.provenance.origin != "human_added"
        checkable = tool_item and item.review.decision != "edited"
        if checkable and audit_eligible(item) != (item.item_id in eligible):
            problems.append(f"{where}: audit eligibility does not match its verdicts and flags")
        stored = p.item_digests.get(item.item_id)
        if stored is None:
            if tool_item:
                problems.append(f"{where}: no tool digest recorded for this item")
        elif stored != item_digest(item) and item.review.decision in ("auto_accepted", "verified"):
            problems.append(
                f"{where}: tool-written fields changed but the decision is "
                f"{item.review.decision!r}; a changed item must be 'edited' with a reviewer. "
                "If the change was a slip, undo it (git diff shows it); if you fixed the item on "
                "purpose, set review.decision: edited, review.reviewer and a note"
            )
        if item.provenance.origin == "human_added":
            problems.extend(_analyst_digest_problems(draft, item, where))
    for gone in sorted(set(p.item_digests) - set(by_id)):
        problems.append(
            f"{cid}/{gone}: item removed from the draft; put it back and decide it 'rejected' "
            "with a note instead"
        )
    drafter = _person(p.drafted_by)
    if drafter:
        for item in draft.items():
            if item.review.reviewer and _person(item.review.reviewer) == drafter:
                problems.append(
                    f"{cid}/{item.item_id}: reviewer {item.review.reviewer!r} drafted this case; "
                    "the drafter cannot be the reviewer"
                )
    return problems


def _analyst_digest_problems(draft: GoldenDraft, item: _DraftItem, where: str) -> list[str]:
    """A staged ``human_added`` item's ``raised_by`` and ``feedback_id`` are the analyst's, not
    the reviewer's: the analyst digest written at staging must still match them."""
    prov = item.provenance
    if prov.feedback_id is None and prov.analyst_digest is None:
        return []  # not staged from feedback (or before the link existed); see below
    expected = analyst_digest(draft.case_id, item.item_id, prov.feedback_id, prov.raised_by)
    if prov.analyst_digest != expected:
        return [
            f"{where}: provenance.raised_by/feedback_id do not match the analyst digest written "
            "at staging; who raised an item is not reviewer-editable (restore them, or reject "
            "the item)"
        ]
    return []


HUMAN_ADDED_FIELDS = ("affected_actor", "mechanism", "impact", "ia_section", "ia_anchor")


def _human_added(item: _DraftItem) -> bool:
    return item.provenance.origin == "human_added" and isinstance(
        item, DraftImpact | DraftCandidate
    )


def human_added_problems(item: _DraftItem, where: str, ia_text: str | None) -> list[str]:
    """A kept ``human_added`` item (an analyst's missing impact) is published only as ``edited``
    by a reviewer other than the person who raised it, with every field a tool-drafted impact
    has filled in. Its IA anchor is checked against ``ia_text`` (the cached IA) when given;
    without it, ``unchecked_human_anchors`` lists the anchor for the reviewer."""
    rev = item.review
    if not _human_added(item) or rev.decision in DROPPED_DECISIONS or rev.decision == "pending":
        return []
    problems = []
    if rev.decision != "edited":
        problems.append(
            f"{where}: a human_added item is kept only as 'edited' (the reviewer fills in and "
            f"checks its IA anchor), not {rev.decision!r}"
        )
    for name in HUMAN_ADDED_FIELDS:
        if not str(getattr(item, name, "") or "").strip():
            problems.append(f"{where}: human_added item: {name} is empty")
    raised_by = item.provenance.raised_by
    if not (raised_by or "").strip():
        problems.append(
            f"{where}: human_added item names nobody who raised it (provenance.raised_by); "
            "the reviewer must be someone else"
        )
    elif rev.reviewer and _person(rev.reviewer) == _person(raised_by):
        problems.append(
            f"{where}: reviewer {rev.reviewer!r} raised it; a human_added item is reviewed by "
            "someone else"
        )
    if not (item.provenance.feedback_id and item.provenance.analyst_digest):
        problems.append(
            f"{where}: human_added item has no provenance.feedback_id/analyst_digest linking it "
            "to the analyst feedback it was staged from; re-stage it with "
            "scripts/import_feedback.py --stage"
        )
    if ia_text is not None and item.ia_anchor.strip():
        status = check_anchor(item.ia_anchor, ia_text, "ia").status
        if status != "verified":
            problems.append(f"{where}: human_added item: IA anchor {status} in the cached IA")
    return problems


def editable_fields(item: _DraftItem) -> tuple[str, ...]:
    """The fields a reviewer may edit on ``item``: a ``human_added`` item also takes its IA
    anchor, which the reviewer finds and fills in (an analyst's item is staged without one)."""
    fields = EDITABLE[item_kind(item)]
    return (*fields, "ia_anchor") if _human_added(item) else fields


def _kept_human_added(draft: GoldenDraft) -> list[_DraftItem]:
    return [
        i for i in draft.items() if _human_added(i) and i.review.decision in ("verified", "edited")
    ]


def analyst_feedback_ids(drafts: Iterable[GoldenDraft]) -> list[str]:
    """Feedback ids of the kept ``human_added`` items of ``drafts`` (what a publish
    cross-checks against the ``analyst_feedback`` table)."""
    ids = {i.provenance.feedback_id for d in drafts for i in _kept_human_added(d)}
    return sorted(i for i in ids if i)


def analyst_feedback_problems(
    draft: GoldenDraft, rows: Mapping[str, Mapping[str, object]]
) -> list[str]:
    """Each kept ``human_added`` item checked against its stored mark (``rows``: feedback id ->
    ``analyst_feedback`` row): the row exists, is a ``missing_impact`` mark that is not
    retracted, on a train run of this case and fixture, made by the analyst named in
    ``raised_by``. The analyst digest only catches edits; this catches a forged one."""
    problems = []
    for item in _kept_human_added(draft):
        where = f"{draft.case_id}/{item.item_id}"
        fid = item.provenance.feedback_id
        row = rows.get(fid) if fid else None
        if row is None:
            problems.append(f"{where}: no analyst_feedback row for feedback {fid!r}")
            continue
        if row.get("mark") != "missing_impact":
            problems.append(f"{where}: feedback {fid} is a {row.get('mark')!r} mark, not "
                            "missing_impact")  # fmt: skip
        if row.get("retracted_at") is not None:
            problems.append(f"{where}: feedback {fid} was retracted; reject the item")
        if row.get("split") != "train":
            problems.append(f"{where}: feedback {fid} is on split {row.get('split')!r}, not train")
        for name, want in (("case_id", draft.case_id), ("fixture", draft.fixture)):
            if row.get(name) != want:
                problems.append(f"{where}: feedback {fid} is on {name} {row.get(name)!r}, not "
                                f"{want!r}")  # fmt: skip
        analyst = row.get("analyst")
        if not isinstance(analyst, str) or _person(analyst) != _person(item.provenance.raised_by):
            problems.append(
                f"{where}: feedback {fid} was raised by {analyst!r}, but the item names "
                f"{item.provenance.raised_by!r} (provenance.raised_by)"
            )
    return problems


def uncrosschecked_analysts(draft: GoldenDraft) -> list[str]:
    """Kept ``human_added`` items whose analyst could not be checked against the database
    (no DATABASE_URL): not blocking; CI has already checked the analyst digest."""
    return [
        f"{draft.case_id}/{i.item_id}: analyst not cross-checked against analyst_feedback (no "
        "DATABASE_URL); the analyst digest was checked"
        for i in _kept_human_added(draft)
    ]


def unchecked_human_anchors(draft: GoldenDraft, ia_text: str | None) -> list[str]:
    """Kept ``human_added`` items whose IA anchor could not be checked here (no cached IA):
    not blocking, but the reviewer confirms the anchor by hand."""
    if ia_text is not None:
        return []
    return [
        f"{draft.case_id}/{i.item_id}: IA anchor not checked (no cached IA here); the reviewer "
        "confirms it against the impact assessment"
        for i in draft.items()
        if _human_added(i) and i.review.decision in ("verified", "edited")
    ]


def check_draft(
    draft: GoldenDraft,
    scenario_keys: set[str] | None,
    *,
    allow_pending: bool = False,
    escalated: bool = False,
    everything: bool = False,
    reviewer_pattern: re.Pattern[str] | None = GITHUB_USERNAME,
    ia_text: str | None = None,
) -> list[str]:
    """Problems that block ``draft``, each prefixed ``<case_id>/<item_id>``.

    ``scenario_keys`` are the provision keys an item may cite (None skips the key check).
    ``allow_pending`` is the draft-PR exemption: undecided items and missing reviewers are
    tolerated, malformed decisions are not. ``reviewer_pattern`` None accepts any non-empty
    reviewer name (local holdout verification). ``ia_text`` is the cached IA, when available,
    against which ``human_added`` anchors are checked (``human_added_problems``)."""
    cid = draft.case_id
    problems: list[str] = []
    if draft.split == "holdout" and not everything:
        problems.append(f"{cid}: split 'holdout' never goes through a PR; use verify_golden_case")
    ids = [i.item_id for i in draft.items()]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"{cid}/{dup}: duplicate item id")
    # The tool-written blocks must not be edited to dodge a review.
    problems.extend(integrity_problems(draft))
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
                problems.append(
                    f"{where}: reviewer {rev.reviewer!r} is not a GitHub username; {USERNAME_HINT}"
                )
        if needs_human and not decided:
            if rev.decision == "auto_accepted" and not allow_pending:
                problems.append(
                    f"{where}: needs a human decision ({', '.join(reasons)}), not auto_accepted; "
                    f"{DECIDE}"
                )
            elif rev.decision == "pending" and not allow_pending:
                problems.append(f"{where}: pending ({', '.join(reasons)}); {DECIDE}")
        if decided and not rev.reviewer and not allow_pending:
            problems.append(
                f"{where}: decision {rev.decision!r} has no reviewer; set review.reviewer to "
                "your GitHub username"
            )
        if rev.decision in NOTE_REQUIRED and not (rev.note or "").strip() and not allow_pending:
            problems.append(
                f"{where}: decision {rev.decision!r} needs a short note saying why (review.note)"
            )
        problems.extend(human_added_problems(item, where, ia_text))
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
            origin="human" if i.provenance.origin == "human_added" else None,
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


# ----------------------------------------------------------------------------- one decision


def decide_item(item: _DraftItem, entry: Mapping[str, Any], reviewer: str) -> _DraftItem:
    """``item`` decided by ``reviewer`` from a decisions entry ``{decision, note, edit}``; an
    ``edit`` maps editable fields to new values and needs ``decision: edited``."""
    where = item.item_id
    unknown = set(entry) - {"decision", "note", "edit"}
    if unknown:
        raise ReviewError(f"{where}: unknown keys {sorted(unknown)}; use decision, note, edit")
    decision = entry.get("decision")
    if decision not in HUMAN_DECISIONS:
        raise ReviewError(
            f"{where}: decision must be one of {', '.join(HUMAN_DECISIONS)}, got {decision!r}"
        )
    raw_note = entry.get("note")
    note = str(raw_note).strip() if raw_note is not None else ""
    if decision in NOTE_REQUIRED and not note:
        raise ReviewError(f"{where}: decision {decision!r} needs a note saying why")
    edit = entry.get("edit") or {}
    if not isinstance(edit, Mapping):
        raise ReviewError(f"{where}: 'edit' must be a mapping of field -> new value")
    human = _human_added(item)
    if human:
        raised_by = item.provenance.raised_by
        if raised_by and _person(raised_by) == _person(reviewer):
            raise ReviewError(
                f"{where}: {reviewer} raised this human_added item; it is reviewed by someone else"
            )
        if decision == "verified":
            raise ReviewError(
                f"{where}: a human_added item (raised by {raised_by}) is kept only as 'edited': "
                "fill in its fields and IA anchor (an 'edit' of what you change, or none if it "
                "is already complete)"
            )
    elif decision == "edited" and not edit:
        raise ReviewError(f"{where}: decision 'edited' needs an 'edit' mapping of new values")
    if edit and decision != "edited":
        raise ReviewError(f"{where}: an edit needs decision 'edited', not {decision!r}")
    allowed = editable_fields(item)
    bad = sorted(set(edit) - set(allowed))
    if bad:
        raise ReviewError(f"{where}: fields {bad} cannot be edited; editable: {list(allowed)}")
    data = item.model_dump(mode="json") | dict(edit)
    if isinstance(item, DraftCandidate) and decision in DECIDED_STATUS:
        status = "human_confirmed_candidate"
    else:
        status = DECIDED_STATUS.get(decision, item.provenance.status)
    data["provenance"] = {**data["provenance"], "status": status}
    data["review"] = Review(
        decision=decision, audit=item.review.audit, reviewer=reviewer, note=note or None
    ).model_dump()
    try:
        decided = type(item).model_validate(data)
    except ValidationError as exc:
        raise ReviewError(f"{where}: the edit is not schema-valid: {exc}") from None
    if human:
        problems = human_added_problems(decided, where, None)
        if problems:
            raise ReviewError("; ".join(problems))
    return decided


# ----------------------------------------------------------------------------- the CI gate

_SECTIONS = ("expected_impacts", "important_omissions", "possibly_missing")
_ID_KEYS = ("expected_id", "omission_id", "candidate_id")
_REVIEW_FIELDS = "decision, audit, reviewer, note"


def _item_label(raw: Any, loc: tuple) -> tuple[str, tuple]:
    """('c12_e02', rest of the location) for a schema error inside an item."""
    if len(loc) >= 2 and loc[0] in _SECTIONS and isinstance(loc[1], int):
        try:
            item = raw[loc[0]][loc[1]]
            label = next(str(item[k]) for k in _ID_KEYS if k in item)
        except (LookupError, TypeError, StopIteration):
            label = f"{loc[0]}[{loc[1]}]"
        return label, tuple(loc[2:])
    return "draft", tuple(loc)


def explain_validation_error(raw: Any, exc: ValidationError) -> list[str]:
    """One plain sentence per schema error, by item id and field."""
    out = []
    for err in exc.errors():
        label, rest = _item_label(raw, tuple(err["loc"]))
        field = ".".join(str(p) for p in rest) or "(item)"
        value = err.get("input")
        if err["type"] == "extra_forbidden":
            hint = f"unknown field {field!r}; check its spelling and indentation"
            if len(rest) >= 2 and rest[-2] == "review":
                hint += f" (review fields: {_REVIEW_FIELDS})"
            out.append(f"{label}: {hint}")
        elif rest[-2:] == ("review", "decision"):
            out.append(
                f"{label}: review.decision is {value!r}; use one of verified, edited, rejected, "
                "unclear (or leave it pending)"
            )
        elif err["type"] == "missing":
            out.append(f"{label}: {field} is missing; was a line deleted or mis-indented?")
        else:
            out.append(f"{label}: {field}: {err['msg']} (got {value!r})")
    return out


def load_draft_for_review(path: Path) -> GoldenDraft:
    """``load_draft`` with errors a first-time reviewer can act on (raises ``ReviewError``)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReviewError(f"{path.name}: cannot read the draft: {exc}") from None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}" if mark is not None else "an unknown line"
        problem = getattr(exc, "problem", None) or str(exc).splitlines()[0]
        raise ReviewError(
            f"{path.name}: not valid YAML at or just above {where} ({problem}). Usually an "
            "indentation slip: inside an item, 'review:' is indented 2 spaces and decision, "
            "audit, reviewer and note 4 spaces. A note containing ': ' or ' #' needs quotes."
        ) from None
    try:
        return GoldenDraft.model_validate(raw)
    except ValidationError as exc:
        lines = "\n  ".join(explain_validation_error(raw, exc))
        raise ReviewError(f"{path.name}: the draft does not match its schema:\n  {lines}") from None


def review_gate(path: Path, *, allow_pending: bool, drafts_dir: Path = DRAFTS_DIR) -> list[str]:
    """The CI check of one draft file: the problems that block it (empty when it passes).

    Escalation counts the open drafts in ``drafts_dir`` that parse, plus the audit tally."""
    try:
        draft = load_draft_for_review(path)
        keys = scenario_keys_for(draft)
    except ReviewError as exc:
        return [str(exc)]
    others = []
    for other in sorted(drafts_dir.glob("*.yaml")):
        if other.resolve() == path.resolve():
            continue
        try:
            others.append(load_draft(other))
        except (OSError, ValidationError, yaml.YAMLError):
            continue  # its own gate reports it
    escalated = draft.fixture in escalated_fixtures([draft, *others], load_audit_tally())
    return check_draft(draft, keys, allow_pending=allow_pending, escalated=escalated)


def gate_hint(path: Path | str) -> str:
    """What a failing CI run tells the reviewer to do next."""
    return (
        "How to fix: docs/eval/reviewer-quickstart.md. Check locally with\n"
        f"  uv run python scripts/review_draft.py {path} --check\n"
        "A draft PR may leave items undecided; once the PR is marked ready for review, CI "
        "requires every decision."
    )
