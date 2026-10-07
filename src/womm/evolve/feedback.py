"""Analyst feedback (U11, origin R31): marks on findings of train/val eval runs.

**Capture surface.** A LangSmith annotation queue (origin scope note: v1 human review reuses
LangSmith annotation queues first). ``scripts/import_feedback.py --queue`` adds the traced
graph runs of a version's train/val eval reports to a queue whose rubric is one categorical
feedback key, ``womm_analyst``, with the five marks of R31. The analyst picks a mark and writes
the details in the comment, one ``field: value`` line each (``finding``, ``provisions``,
``actor``, ``mechanism``, ``impact``, ``category``, ``edited``); any other line is the note. A
``correction`` dict with the same fields, when the feedback has one, takes precedence.

**Flow.** ``scripts/import_feedback.py`` reads the marks back and resolves each against the
eval report that produced the run (trace id -> WOMM run id, case, split, repetition) and the
saved run (``runs/<run_id>.json``, for the finding and the experts touching a provision):

- every mark becomes one ``analyst_feedback`` row (the audit log), keyed by the LangSmith
  feedback id, so an import is idempotent;
- ``missing_impact`` and ``weak_evidence`` also become Failure Memory events of the human kinds
  ``analyst_missing_impact`` / ``analyst_weak_evidence`` (``source = human``), aggregated with
  the judge's events by ``(kind, category, owner)``;
- ``missing_impact`` on a **train** run is also a golden-case candidate (a val run's never is:
  val is for selection). It is never added to a golden case: it is ``queued`` until
  ``stage_candidates`` appends it to the case's open train draft as a ``possibly_missing`` item
  of origin ``human_added`` raised by the analyst, decision ``pending``, where the review gate
  (``womm.eval.golden_review.check_draft``) blocks publication until a reviewer other than the
  analyst edits it (with its IA anchor) or rejects it. Published, it is an expected impact of
  ``origin: human``, whose misses the new-expert trigger ignores.

``accept``, ``reject`` and ``edit`` are recorded only (v1 has no consumer for them).

**Holdout.** Feedback on holdout material never exists: holdout runs are never traced (so no
annotation queue can hold one), eval reports never contain one, a report naming a holdout split
is refused, every resolution re-checks the split, the database tables ``CHECK`` the split, and
staging refuses a holdout (or val) draft or a fixture registered as a holdout proposal.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, model_validator

from womm.eval.drafting import (
    AnchorCheck,
    Category,
    Derivability,
    DimensionVerdict,
    DraftCandidate,
    GoldenDraft,
    ItemProvenance,
    JudgeBlock,
    Review,
    case_prefix,
    draft_path,
    load_draft,
    write_draft,
)
from womm.eval.golden import HOLDOUT_REFUSAL, GoldenError
from womm.eval.golden_review import HOLDOUT_REGISTRY, ReviewError, refuse_holdout_fixture
from womm.evolve.failure_memory import UNCATEGORISED, CaseRun, FailureEvent
from womm.models.run import RunResult
from womm.tracing import is_sealed

FEEDBACK_KEY = "womm_analyst"
Mark = Literal["accept", "reject", "edit", "missing_impact", "weak_evidence"]
MARKS: tuple[str, ...] = get_args(Mark)
# The numeric value of each category in the LangSmith feedback config (a categorical feedback
# stores the chosen category's value as its score).
MARK_CODES: dict[int, str] = dict(enumerate(MARKS))
FINDING_MARKS = frozenset({"accept", "reject", "edit", "weak_evidence"})
FIELDS = ("finding", "provisions", "actor", "mechanism", "impact", "category", "edited")
_FIELD_LINE = re.compile(rf"^\s*({'|'.join(FIELDS)})\s*:\s*(.*)$", re.IGNORECASE)
CANDIDATE_FLAG = "analyst feedback {feedback_id}: IA anchor, category and wording not yet checked"


class FeedbackError(ValueError):
    pass


class AnalystFeedback(BaseModel):
    """One analyst mark, parsed from a LangSmith feedback."""

    feedback_id: str
    trace_run_id: str
    mark: Mark
    finding_id: str | None = None
    provision_keys: list[str] = Field(default_factory=list)
    affected_actor: str | None = None
    mechanism: str | None = None
    impact: str | None = None
    category: str | None = None
    edited: str | None = None
    note: str | None = None
    analyst: str | None = None
    created_at: dt.datetime
    modified_at: dt.datetime | None = Field(
        default=None, description="LangSmith's last modification; a newer one replaces the mark."
    )

    @model_validator(mode="after")
    def _required_fields(self) -> AnalystFeedback:
        if self.mark in FINDING_MARKS and not self.finding_id:
            raise ValueError(f"a {self.mark!r} mark names the finding ('finding: <finding_id>')")
        if self.mark == "missing_impact" and not self.impact:
            raise ValueError("a 'missing_impact' mark describes the impact ('impact: ...')")
        if self.mark == "edit" and not self.edited:
            raise ValueError("an 'edit' mark carries the edited text ('edited: ...')")
        if self.category is not None and self.category not in get_args(Category):
            raise ValueError(f"unknown category {self.category!r}; one of {get_args(Category)}")
        return self


class FeedbackRecord(BaseModel):
    """A resolved mark: the audit row, its Failure Memory event and its golden-candidate state."""

    feedback: AnalystFeedback
    run: CaseRun
    event: FailureEvent | None = None
    golden_candidate: Literal["queued"] | None = None


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, Mapping) else getattr(obj, name, None)


def _mark(value: Any, score: Any) -> str:
    if isinstance(value, str) and value.strip().lower() in MARKS:
        return value.strip().lower()
    if isinstance(score, int | float) and float(score).is_integer() and int(score) in MARK_CODES:
        return MARK_CODES[int(score)]
    raise FeedbackError(f"unknown mark (value={value!r}, score={score!r}); one of {MARKS}")


def _fields(comment: str | None, correction: Any) -> tuple[dict[str, str], str | None]:
    found: dict[str, str] = {}
    note: list[str] = []
    for line in (comment or "").splitlines():
        m = _FIELD_LINE.match(line)
        if m and m.group(2).strip():
            found[m.group(1).lower()] = m.group(2).strip()
        elif line.strip():
            note.append(line.strip())
    if isinstance(correction, Mapping):
        for name in FIELDS:
            value = correction.get(name)
            if isinstance(value, list):
                value = ", ".join(str(v) for v in value)
            if value is not None and str(value).strip():
                found[name] = str(value).strip()
    return found, "\n".join(note) or None


def parse_feedback(fb: Any) -> AnalystFeedback:
    """An ``AnalystFeedback`` from a LangSmith ``Feedback`` (or a mapping with its fields)."""
    if _get(fb, "key") != FEEDBACK_KEY:
        raise FeedbackError(f"feedback key {_get(fb, 'key')!r} is not {FEEDBACK_KEY!r}")
    fid = str(_get(fb, "id"))
    if _get(fb, "run_id") is None:
        raise FeedbackError(f"feedback {fid} is not attached to a run")
    found, note = _fields(_get(fb, "comment"), _get(fb, "correction"))
    source = _get(fb, "feedback_source")
    analyst = None
    if source is not None:
        analyst = _get(source, "user_name") or _get(source, "user_id")
    try:
        return AnalystFeedback(
            feedback_id=fid,
            trace_run_id=str(_get(fb, "run_id")),
            mark=_mark(_get(fb, "value"), _get(fb, "score")),
            finding_id=found.get("finding"),
            provision_keys=[k.strip() for k in found.get("provisions", "").split(",")
                            if k.strip()],
            affected_actor=found.get("actor"),
            mechanism=found.get("mechanism"),
            impact=found.get("impact"),
            category=found.get("category"),
            edited=found.get("edited"),
            note=note,
            analyst=str(analyst) if analyst is not None else None,
            created_at=_get(fb, "created_at") or dt.datetime.now(dt.UTC),
            modified_at=_get(fb, "modified_at"),
        )  # fmt: skip
    except ValueError as exc:
        raise FeedbackError(f"feedback {fid}: {exc}") from None


def _check_split(split: str | None, what: str) -> None:
    if is_sealed(split) or split not in ("train", "val"):
        raise GoldenError(
            f"{what}: analyst feedback is taken on train/val runs only, got split {split!r}; "
            f"{HOLDOUT_REFUSAL}"
        )


def report_runs(runs_dir: Path, system_version: str) -> dict[str, CaseRun]:
    """The scored train/val runs of ``system_version``'s eval reports in ``runs_dir``, by the
    LangSmith id of their graph run (the run an analyst marks). A report that names a holdout
    split is refused before anything is read from it."""
    out: dict[str, CaseRun] = {}
    for path in sorted(runs_dir.glob("eval_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("system_version") != system_version:
            continue
        meta = data.get("metadata") or {}
        splits = {meta.get("split"), *(meta.get("splits") or [])}
        splits |= {r.get("split") for r in data.get("case_runs") or []}
        if any(is_sealed(s) for s in splits):
            raise GoldenError(f"{path.name}: refusing a report with holdout runs; "
                              f"{HOLDOUT_REFUSAL}")  # fmt: skip
        runs = {r["run_id"]: CaseRun.model_validate(r) for r in data.get("case_runs") or []}
        for score in data.get("scores") or []:
            trace, run_id = score.get("graph_run_id"), score.get("run_id")
            if trace and run_id in runs:
                out[str(trace)] = runs[run_id]
    return out


def load_run(runs_dir: Path, run_id: str) -> RunResult | None:
    path = runs_dir / f"{run_id}.json"
    if path.parent != runs_dir or not path.is_file():
        return None
    return RunResult.model_validate_json(path.read_text(encoding="utf-8"))


def resolve_feedback(fb: AnalystFeedback, case_run: CaseRun, run: RunResult) -> FeedbackRecord:
    """The record of one mark on a scored train/val run. A sealed split is refused; a mark that
    names a finding the run does not have is refused."""
    _check_split(case_run.split, f"feedback {fb.feedback_id}")
    if run.run_id != case_run.run_id:
        raise FeedbackError(f"feedback {fb.feedback_id}: run {run.run_id} is not "
                            f"{case_run.run_id}")  # fmt: skip
    findings = {f.finding_id: f for f in run.board}
    finding = None
    if fb.finding_id is not None:
        finding = findings.get(fb.finding_id)
        if finding is None:
            raise FeedbackError(
                f"feedback {fb.feedback_id}: run {run.run_id} has no finding {fb.finding_id!r}"
            )
    common = {
        "case_id": case_run.case_id, "fixture": case_run.fixture, "split": case_run.split,
        "run_id": case_run.run_id, "repetition": case_run.repetition,
        "system_version": case_run.system_version, "judge_version": case_run.judge_version,
        "git_sha": case_run.git_sha,
    }  # fmt: skip
    detail = {"feedback_id": fb.feedback_id, "analyst": fb.analyst, "note": fb.note}
    event = None
    if fb.mark == "weak_evidence" and finding is not None:
        event = FailureEvent(
            kind="analyst_weak_evidence", item_id=f"{finding.agent}:{finding.provision_key}",
            category=UNCATEGORISED, touching_agents=[finding.agent], owner=finding.agent,
            detail=detail | {"finding_id": finding.finding_id}, **common,
        )  # fmt: skip
    elif fb.mark == "missing_impact":
        keys = sorted(set(fb.provision_keys))
        touching = sorted({f.agent for f in run.board if f.provision_key in keys})
        owner = "none" if not touching else touching[0] if len(touching) == 1 else "multiple"
        event = FailureEvent(
            kind="analyst_missing_impact",
            item_id=f"human:{','.join(keys)}" if keys else f"human:fb:{fb.feedback_id}",
            category=fb.category or UNCATEGORISED, touching_agents=touching, owner=owner,
            detail=detail | {"impact": fb.impact, "affected_actor": fb.affected_actor,
                             "mechanism": fb.mechanism, "provision_keys": keys},
            **common,
        )  # fmt: skip
    return FeedbackRecord(
        feedback=fb, run=case_run, event=event,
        golden_candidate="queued" if fb.mark == "missing_impact" and case_run.split == "train"
        else None,
    )  # fmt: skip


# ----------------------------------------------------------------- golden candidates


def golden_candidate(row: Mapping[str, Any], candidate_id: str) -> DraftCandidate:
    """A queued ``missing_impact`` row as a ``possibly_missing`` draft item that only a human can
    decide: origin ``human_added``, decision ``pending``, no judge verdict and no IA anchor yet.
    The review gate requires a named reviewer to verify, edit (adding the IA anchor) or reject
    it before the draft can be published."""
    payload = row.get("payload") or {}
    unchecked = DimensionVerdict(verdict="unknown", reason="analyst feedback; not judged")
    note = (row.get("note") or "").strip()
    return DraftCandidate(
        candidate_id=candidate_id,
        affected_actor=payload.get("affected_actor") or "",
        mechanism=payload.get("mechanism") or "",
        impact=payload.get("impact") or "",
        why_missing=f"analyst {row.get('analyst') or 'unknown'} marked it missing in run "
        f"{row['run_id']} of {row['system_version']}" + (f": {note}" if note else ""),
        provision_keys=list(payload.get("provision_keys") or []),
        ia_section="",
        ia_anchor="",
        anchor=AnchorCheck(status="anchor_not_found", against="ia"),
        category=payload.get("category") or "other",
        derivability=Derivability(verdict="partly", reason="analyst feedback; not judged"),
        flags=[CANDIDATE_FLAG.format(feedback_id=row["feedback_id"])],
        judge=JudgeBlock(
            anchor_faithfulness=unchecked,
            derivability=unchecked,
            category=unchecked,
            overall="uncertain",
        ),  # fmt: skip
        provenance=ItemProvenance(
            origin="human_added",
            status="needs_human",
            drafting_model="none (analyst feedback)",
            raised_by=row.get("analyst") or None,
        ),  # fmt: skip
        review=Review(decision="pending"),
    )


def _staged_ids(draft: GoldenDraft) -> set[str]:
    out = set()
    for c in draft.possibly_missing:
        for flag in c.flags:
            m = re.match(r"^analyst feedback (\S+):", flag)
            if m:
                out.add(m.group(1))
    return out


def _next_candidate_id(draft: GoldenDraft) -> str:
    prefix = f"{case_prefix(draft.case_id)}_fb"
    taken = {i.item_id for i in draft.items()}
    n = 1
    while f"{prefix}{n:02d}" in taken:
        n += 1
    return f"{prefix}{n:02d}"


def stage_candidates(
    rows: Iterable[Mapping[str, Any]],
    drafts_dir: Path,
    *,
    registry: Path = HOLDOUT_REGISTRY,
) -> tuple[dict[str, Path], list[str]]:
    """Append queued ``missing_impact`` rows to their case's open golden draft in
    ``drafts_dir``. Returns ``(staged feedback id -> draft path, problems)``; a row whose case has
    no open draft stays queued (it is staged once the case is re-drafted). Re-staging a row
    already in the draft only reports it as staged. Holdout drafts and fixtures registered as
    holdout proposals are refused."""
    staged: dict[str, Path] = {}
    problems: list[str] = []
    by_case: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case_id"], []).append(row)
    for case_id, case_rows in sorted(by_case.items()):
        path = drafts_dir / f"{case_id}.yaml"
        if not path.is_file():
            continue
        draft = load_draft(path)
        try:
            _check_split(draft.split, f"draft {case_id}")
            refuse_holdout_fixture(draft.fixture, f"draft {case_id}", registry)
        except (GoldenError, ReviewError) as exc:
            problems.append(str(exc))
            continue
        if draft.split != "train":
            problems.append(f"draft {case_id}: analyst candidates are staged into train drafts "
                            f"only, not {draft.split!r} (val is for selection)")  # fmt: skip
            continue
        if draft_path(case_id, draft.split, drafts_dir) != path.resolve():
            problems.append(f"draft {case_id}: unexpected path {path}")
            continue
        present = _staged_ids(draft)
        added = 0
        for row in case_rows:
            if row["fixture"] != draft.fixture:
                problems.append(f"feedback {row['feedback_id']}: fixture {row['fixture']} is not "
                                f"draft {case_id}'s {draft.fixture}")  # fmt: skip
                continue
            if row["feedback_id"] not in present:
                draft.possibly_missing.append(golden_candidate(row, _next_candidate_id(draft)))
                added += 1
            staged[row["feedback_id"]] = path
        if added:
            s = draft.stats
            s.items += added
            s.possibly_missing += added
            s.pending += added
            s.human_decisions_needed += added
            write_draft(draft, drafts_dir)
    return staged, problems
