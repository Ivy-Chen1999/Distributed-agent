"""Promotion gate (U7; R28, R29, AE4): promote or reject a candidate on the sealed holdout.

The only ``womm.evolve`` module allowed to import ``womm.eval.holdout`` (the import-graph test
in ``tests/evolve/test_planner_boundary.py``); nothing Planner-side imports this module.

- **Pre-registered policy.** ``evals/promotion_policy.yaml`` fixes the primary metric, the
  guard tolerances, repetitions, ``failure_policy``, the formal mode, the holdout budget and
  ``publish_summary`` before any result. Every field is required. Every mode, dev included,
  refuses to run until a person has signed it (``signed_by``) and committed it: each mode reads
  the sealed holdout and spends its budget.
- **Mode before results.** ``dev`` when any role of either version is not on the api backend
  (labelled dev-only, never deployable); otherwise the policy's formal mode, falling back to
  ``weak`` when no minimum-detectable-delta report says the gate can resolve the expected gain.
  The one later change is data-driven, never result-driven: a statistical comparison whose
  primary CI is null (``insufficient_proposals``) is decided as weak, and the downgrade is
  recorded.
- **Preconditions** (each a refusal before any holdout run): a signed, committed policy (every
  mode); both versions on the same backends (per shared role; a role only one version has,
  such as a new expert, on a backend and model the other already uses); for formal modes a
  coverage-judge calibration of at least 85% for the gate's judge and an R34 formal noise run
  on record (dev mode skips both); holdout budget left (per cycle and in total). The budget
  is then reserved atomically in the holdout database (``holdout.budget_reservations``) before
  the comparison runs, and the cycle is the candidate's archived one (``gate_cycle_id``).
- **Decision.** Statistical: the primary metric's CI95 lower bound above 0. Weak and dev: its
  mean delta above 0. Every guard metric's mean delta at least minus its tolerance. An aborted
  comparison is rejected as ``inconclusive``, consumes no budget, and a repeated abort on the
  same pair is flagged for a human.
- **Records.** The decision is written next to its comparison in the holdout audit
  (``holdout.compare_audit.decision``). A summary goes to the main database's
  ``promotion_decisions`` only when the policy sets ``publish_summary``. The R37 diff check is
  copied into both, and never changes the decision.
"""

from __future__ import annotations

import contextlib
import hashlib
import subprocess
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from psycopg.types.json import Jsonb
from pydantic import Field, ValidationError, model_validator

from womm.api.db import Database
from womm.config import REPO_ROOT
from womm.decisions.service import DecisionService
from womm.eval import holdout
from womm.eval.evaluators import judge_version
from womm.eval.holdout import HoldoutComparison, HoldoutStore
from womm.eval.run_eval import FORMAL_MIN_REPETITIONS
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel
from womm.models.run import CodeIdentity
from womm.models.system_version import SystemVersion

POLICY_PATH = REPO_ROOT / "evals" / "promotion_policy.yaml"
RECORDS_PATH = REPO_ROOT / "evals" / "promotion_records.yaml"
CALIBRATION_MIN = 0.85
FORMAL_BACKEND = "api"
Metric = Literal["coverage", "omissions_addressed", "grounding"]
Mode = Literal["statistical", "weak", "dev"]
MODE_LABELS: dict[str, str] = {
    "statistical": "holdout, bootstrap CI",
    "weak": "weak threshold: directional",
    "dev": "dev-only, not deployable; weak threshold: directional",
}


class GateRefused(RuntimeError):
    """A precondition failed; nothing was run on the holdout."""


class SummaryNotRecorded(RuntimeError):
    """The decision is recorded in the holdout audit, but its main-database summary is not."""

    def __init__(self, decision: GateDecision, cause: BaseException) -> None:
        super().__init__(f"the decision {decision.gate_id} is recorded in the holdout audit, but "
                         f"its promotion_decisions summary was not written: {cause}")  # fmt: skip
        self.decision = decision


async def gate_cycle_id(archive: Any, candidate_id: str, requested: str | None) -> str:
    """The cycle whose per-cycle holdout budget a gate spends: the candidate's archived cycle,
    its dev source's (for an api twin) or its api twin's. ``requested`` (``--cycle-id``) must be
    one of those, so a fresh cycle id cannot bypass the per-cycle budget."""
    row = await archive.get(candidate_id)
    related = [row]
    if row and row.get("twin_of"):
        related.append(await archive.get(row["twin_of"]))
    related += await archive.twins(candidate_id)
    cycles = list(dict.fromkeys(r["cycle_id"] for r in related if r and r.get("cycle_id")))
    if not cycles:
        raise GateRefused(f"{candidate_id} has no cycle id in the archive: only candidates a "
                          "cycle archived (or their api twins) go to the gate")  # fmt: skip
    if requested is None:
        return cycles[0]
    if requested not in cycles:
        raise GateRefused(f"--cycle-id {requested} is not the candidate's archived cycle "
                          f"({', '.join(cycles)}): one holdout budget per cycle")  # fmt: skip
    return requested


# ----------------------------------------------------------------------------- policy


class Guard(StrictModel):
    max_drop: float | None = Field(default=None, ge=0)
    max_drop_noise_sd: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _one_tolerance(self) -> Guard:
        if (self.max_drop is None) == (self.max_drop_noise_sd is None):
            raise ValueError("a guard sets exactly one of max_drop and max_drop_noise_sd")
        return self


class HoldoutBudget(StrictModel):
    per_cycle: int = Field(ge=1)
    total: int = Field(ge=1)


class Bootstrap(StrictModel):
    n_boot: int = Field(ge=100)
    seed: int


class PromotionPolicy(StrictModel):
    """Every field is required: an incomplete policy is refused."""

    version: Literal[1]
    signed_by: str | None
    signed_on: str | None
    primary_metric: Metric
    guards: dict[Metric, Guard]
    formal_mode: Literal["statistical", "weak"]
    expected_gain: float = Field(gt=0)
    repetitions: int = Field(ge=2, description="Two at least, so run-to-run noise is measured.")
    failure_policy: Literal["abort", "score_zero"]
    holdout_budget: HoldoutBudget
    publish_summary: bool
    bootstrap: Bootstrap

    @model_validator(mode="after")
    def _primary_is_not_a_guard(self) -> PromotionPolicy:
        if self.primary_metric in self.guards:
            raise ValueError(f"{self.primary_metric} is the primary metric, not a guard")
        return self

    @property
    def signed(self) -> bool:
        return bool((self.signed_by or "").strip() and (self.signed_on or "").strip())


class JudgeCalibration(StrictModel):
    judge_version: str
    agreement: float = Field(ge=0, le=1)
    pairs: int = Field(ge=1)
    annotators: list[str] = Field(min_length=1)
    recorded_on: str


class FormalNoiseRun(StrictModel):
    system_version: str
    judge_version: str
    split: Literal["train", "val"]
    repetitions: int = Field(ge=FORMAL_MIN_REPETITIONS)
    experiment: str | None = None
    recorded_on: str


class MddReport(StrictModel):
    metric: Metric
    judge_version: str
    mdd: float = Field(gt=0)
    recorded_on: str


class PromotionRecords(StrictModel):
    judge_calibrations: list[JudgeCalibration] = Field(default_factory=list)
    formal_noise_runs: list[FormalNoiseRun] = Field(default_factory=list)
    mdd_reports: list[MddReport] = Field(default_factory=list)

    def calibration(self, jv: str) -> JudgeCalibration | None:
        return next((c for c in reversed(self.judge_calibrations) if c.judge_version == jv), None)

    def noise_run(self, jv: str) -> FormalNoiseRun | None:
        return next((r for r in reversed(self.formal_noise_runs) if r.judge_version == jv), None)

    def mdd(self, metric: str, jv: str) -> MddReport | None:
        return next(
            (m for m in reversed(self.mdd_reports) if m.metric == metric and m.judge_version == jv),
            None,
        )


def _read_yaml(path: Path, what: str) -> tuple[Any, str]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise GateRefused(f"no {what} at {path}") from None
    try:
        return yaml.safe_load(raw) or {}, hashlib.sha256(raw).hexdigest()
    except yaml.YAMLError as exc:
        raise GateRefused(f"{path}: {exc}") from None


def load_policy(path: Path = POLICY_PATH) -> tuple[PromotionPolicy, str]:
    """The policy and the sha256 of its file; an incomplete or invalid policy is refused."""
    data, sha = _read_yaml(path, "promotion policy")
    try:
        return PromotionPolicy.model_validate(data), sha
    except ValidationError as exc:
        raise GateRefused(f"{path} is incomplete or invalid: {exc}") from None


def load_records(path: Path = RECORDS_PATH) -> PromotionRecords:
    data, _ = _read_yaml(path, "promotion records")
    try:
        return PromotionRecords.model_validate(data)
    except ValidationError as exc:
        raise GateRefused(f"{path} is invalid: {exc}") from None


def files_committed(paths: list[Path], repo_root: Path = REPO_ROOT) -> bool:
    """True when every path is tracked by git and has no uncommitted change."""
    try:
        rel = [str(p.resolve().relative_to(repo_root.resolve())) for p in paths]
    except ValueError:
        return False  # outside the repository: never committed
    try:
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", *rel], cwd=repo_root,
                                 capture_output=True, check=False)  # fmt: skip
        status = subprocess.run(["git", "status", "--porcelain", "--", *rel], cwd=repo_root,
                                capture_output=True, text=True, check=False)  # fmt: skip
    except OSError:
        return False
    return tracked.returncode == 0 and status.returncode == 0 and not status.stdout.strip()


# ----------------------------------------------------------------------------- mode


class ModeChoice(StrictModel):
    mode: Mode
    deployable: bool
    notes: list[str] = Field(default_factory=list)


def _backends(sv: SystemVersion) -> dict[str, str]:
    return {name: role.backend for name, role in sv.spec.roles().items()}


def backend_mismatch(candidate: SystemVersion, incumbent: SystemVersion) -> str | None:
    """Why the two versions are not on the same backends, or None. Roles both versions have
    (keyed ``planner``, ``expert:<id>``, ...) must use the same backend. A role only one of
    them has (a topology candidate's new expert) must use a (backend, model) pair the other
    version already uses, so an added expert cannot bring a new backend or model family."""
    cand, inc = candidate.spec.roles(), incumbent.spec.roles()
    for name in sorted(cand.keys() & inc.keys()):
        if cand[name].backend != inc[name].backend:
            return (f"{name} is on {cand[name].backend} in the candidate, "
                    f"{inc[name].backend} in the incumbent")  # fmt: skip
    for mine, other in ((cand, inc), (inc, cand)):
        family = {(r.backend, r.model) for r in other.values()}
        for name in sorted(mine.keys() - other.keys()):
            if (mine[name].backend, mine[name].model) not in family:
                return (f"{name} ({mine[name].backend}, {mine[name].model}) is only in one "
                        "version and uses a backend and model the other does not")  # fmt: skip
    return None


def choose_mode(
    policy: PromotionPolicy, records: PromotionRecords, candidate: SystemVersion,
    incumbent: SystemVersion,
) -> ModeChoice:  # fmt: skip
    """The gate mode, from the backends, the policy and the records only (never a result)."""
    used = set(_backends(candidate).values()) | set(_backends(incumbent).values())
    if used != {FORMAL_BACKEND}:
        others = ", ".join(sorted(used - {FORMAL_BACKEND}))
        note = (f"dev mode: roles on {others}, not every role on the {FORMAL_BACKEND} backend; "
                "the decision is not deployable")  # fmt: skip
        return ModeChoice(mode="dev", deployable=False, notes=[note])
    if policy.formal_mode == "weak":
        return ModeChoice(mode="weak", deployable=True,
                          notes=["the policy pre-registers the weak threshold"])  # fmt: skip
    jv = judge_version(incumbent)
    mdd = records.mdd(policy.primary_metric, jv)
    if mdd is None:
        return ModeChoice(
            mode="weak",
            deployable=True,
            notes=["weak mode: no minimum-detectable-delta report for this judge"],
        )
    if mdd.mdd > policy.expected_gain:
        return ModeChoice(mode="weak", deployable=True, notes=[
            f"weak mode: the minimum detectable delta ({mdd.mdd:.3f}) exceeds the expected gain "
            f"({policy.expected_gain:.3f})"])  # fmt: skip
    return ModeChoice(mode="statistical", deployable=True)


# ----------------------------------------------------------------------------- preconditions


async def preflight(
    policy: PromotionPolicy,
    records: PromotionRecords,
    candidate: SystemVersion,
    incumbent: SystemVersion,
    *,
    store: HoldoutStore,
    cycle_id: str,
    policy_committed: bool,
) -> ModeChoice:
    """Every precondition, before any holdout run; raises GateRefused with the reason."""
    if candidate.version_id == incumbent.version_id:
        raise GateRefused("the candidate is the incumbent")
    if (why := backend_mismatch(candidate, incumbent)) is not None:
        raise GateRefused("candidate and incumbent must use the same backends (compare api twins "
                          f"with api twins, dev versions with dev versions): {why}")  # fmt: skip
    choice = choose_mode(policy, records, candidate, incumbent)
    # Every mode reads the sealed holdout, so every mode (dev included) needs the policy signed
    # and committed; only the formal modes also need the judge calibration and the R34 run.
    if not policy.signed:
        raise GateRefused("the promotion policy is not signed (signed_by, signed_on): a person "
                          "pre-registers it before any holdout comparison, dev mode "
                          "included")  # fmt: skip
    if not policy_committed:
        raise GateRefused("the promotion policy or records have uncommitted changes; a holdout "
                          "comparison runs only on committed, pre-registered files")  # fmt: skip
    if choice.mode != "dev":
        jv = judge_version(incumbent)
        calibration = records.calibration(jv)
        if calibration is None:
            raise GateRefused(f"no coverage-judge calibration on record for judge {jv}")
        if calibration.agreement < CALIBRATION_MIN:
            raise GateRefused(f"the coverage-judge calibration for {jv} is "
                              f"{calibration.agreement:.0%}, below {CALIBRATION_MIN:.0%}: fix "
                              "the judge rubric before any promotion run")  # fmt: skip
        if records.noise_run(jv) is None:
            raise GateRefused(f"no R34 formal noise run on record for judge {jv}")
    total, in_cycle = await store.budget_used(cycle_id)
    if total >= policy.holdout_budget.total:
        raise GateRefused(f"the holdout budget is exhausted ({total} of "
                          f"{policy.holdout_budget.total} comparisons used)")  # fmt: skip
    if in_cycle >= policy.holdout_budget.per_cycle:
        raise GateRefused(f"cycle {cycle_id} already used its {policy.holdout_budget.per_cycle} "
                          "holdout comparison(s)")  # fmt: skip
    return choice


# ----------------------------------------------------------------------------- decision


class DeltaSummary(StrictModel):
    mean_delta: float | None
    ci95_low: float | None
    ci95_high: float | None
    n_cases: int
    noise_sd: float | None = Field(description="The larger arm's pooled run-to-run SD.")


class GateDecision(StrictModel):
    """Aggregates only, by construction: built from a HoldoutComparison."""

    gate_id: str
    cycle_id: str
    candidate_version: str
    incumbent_version: str
    mode: Mode
    deployable: bool
    decision: Literal["promoted", "rejected"]
    label: str
    reasons: list[str]
    notes: list[str]
    deltas: dict[str, DeltaSummary]
    n_proposals: int
    flags: list[str]
    consumes_budget: bool
    policy_sha256: str
    git_sha: str | None
    r37: dict[str, Any] | None = None


def _noise_sd(cmp: HoldoutComparison, metric: str) -> float | None:
    noise = cmp.noise.get(metric)
    values = [v for v in (noise.candidate_sd, noise.baseline_sd) if v is not None] if noise else []
    return max(values) if values else None


def decide(
    cmp: HoldoutComparison,
    policy: PromotionPolicy,
    choice: ModeChoice,
    *,
    gate_id: str,
    cycle_id: str,
    policy_sha256: str,
    git_sha: str | None,
    prior_aborts: int = 0,
    r37: dict[str, Any] | None = None,
) -> GateDecision:
    """Apply the pre-registered rule to one comparison. The R37 check is copied, never used."""
    mode, notes, reasons = choice.mode, list(choice.notes), []
    deltas = {
        m: DeltaSummary(**d.model_dump(), noise_sd=_noise_sd(cmp, m)) for m, d in cmp.deltas.items()
    }
    if cmp.aborted:
        reasons = ["inconclusive"]
        if prior_aborts:
            reasons.append("repeated_abort")
            notes.append(f"this pair was aborted {prior_aborts} time(s) before: a human should "
                         "look at why")  # fmt: skip
        improved = False
    else:
        primary = cmp.deltas[policy.primary_metric]
        if mode == "statistical" and (primary.ci95_low is None or primary.ci95_high is None):
            mode = "weak"
            why = ("insufficient proposals" if "insufficient_proposals" in cmp.flags
                   else "no CI over every sealed case")  # fmt: skip
            notes.append(f"downgraded from statistical to weak: {why}, so the primary CI is null")
        if mode == "statistical":
            improved = primary.ci95_low > 0
            if not improved:
                reasons.append(f"{policy.primary_metric}_ci_not_above_zero")
        else:
            improved = primary.mean_delta is not None and primary.mean_delta > 0
            if not improved:
                reasons.append(f"{policy.primary_metric}_not_improved")
        for metric, guard in policy.guards.items():
            delta = cmp.deltas[metric].mean_delta
            if delta is None:
                notes.append(f"{metric}: no case measures it; guard not applicable")
                continue
            if guard.max_drop is not None:
                allowed = guard.max_drop
            else:
                sd = _noise_sd(cmp, metric)
                if sd is None:
                    reasons.append(f"{metric}_noise_unavailable")
                    continue
                allowed = guard.max_drop_noise_sd * sd
            if delta < -allowed - 1e-12:
                reasons.append(f"{metric}_regression")
    decision = "promoted" if improved and not reasons else "rejected"
    return GateDecision(
        gate_id=gate_id, cycle_id=cycle_id, candidate_version=cmp.candidate_version,
        incumbent_version=cmp.baseline_version, mode=mode, deployable=choice.deployable,
        decision=decision, label=f"{decision} ({MODE_LABELS[mode]})", reasons=reasons,
        notes=notes, deltas=deltas, n_proposals=cmp.n_proposals, flags=list(cmp.flags),
        consumes_budget=not cmp.aborted, policy_sha256=policy_sha256, git_sha=git_sha, r37=r37,
    )  # fmt: skip


# ----------------------------------------------------------------------------- records


async def record_summary(db: Database, decision: GateDecision) -> None:
    """The main-database summary row (only when the policy sets publish_summary)."""
    d = decision.model_dump(mode="json")
    async with db.pool.connection() as conn:
        await conn.execute(
            "INSERT INTO promotion_decisions (gate_id, cycle_id, candidate_version,"
            " incumbent_version, mode, deployable, decision, label, reasons, notes, deltas,"
            " n_proposals, flags, policy_sha256, git_sha, r37) VALUES (%s, %s, %s, %s, %s, %s,"
            " %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (d["gate_id"], d["cycle_id"], d["candidate_version"], d["incumbent_version"],
             d["mode"], d["deployable"], d["decision"], d["label"], Jsonb(d["reasons"]),
             Jsonb(d["notes"]), Jsonb(d["deltas"]), d["n_proposals"], Jsonb(d["flags"]),
             d["policy_sha256"], d["git_sha"], Jsonb(d["r37"]) if d["r37"] else None),
        )  # fmt: skip


Backends = Mapping[str, LLMBackend]


async def run_gate(
    *,
    candidate: SystemVersion,
    incumbent: SystemVersion,
    policy: PromotionPolicy,
    policy_sha256: str,
    records: PromotionRecords,
    store: HoldoutStore,
    backends: Backends | Callable[[SystemVersion], Backends],
    decisions: DecisionService | Callable[[SystemVersion], DecisionService],
    code: CodeIdentity,
    cycle_id: str,
    policy_committed: bool,
    r37: dict[str, Any] | None = None,
    summary_db: Database | None = None,
    resume: bool = False,
) -> GateDecision:
    """Preconditions, a budget reservation, one resumable holdout comparison, the decision and
    its records.

    The reservation is taken atomically before the comparison (concurrent gates cannot
    overspend) and released when the comparison fails; recording the decision consumes it (or
    releases it for an aborted, inconclusive comparison). When the main-database summary fails
    after the decision is recorded, SummaryNotRecorded carries the decision."""
    if policy.publish_summary and summary_db is None:
        raise GateRefused("publish_summary is on but no main database was given")
    choice = await preflight(policy, records, candidate, incumbent, store=store,
                             cycle_id=cycle_id, policy_committed=policy_committed)  # fmt: skip
    prior = await store.prior_aborts(candidate.version_id, incumbent.version_id)
    try:
        gate_id = await store.reserve_budget(
            f"gate_{uuid.uuid4().hex[:16]}", cycle_id, candidate.version_id,
            incumbent.version_id, per_cycle=policy.holdout_budget.per_cycle,
            total=policy.holdout_budget.total, resume=resume,
        )  # fmt: skip
    except holdout.BudgetRefused as exc:
        raise GateRefused(str(exc)) from None
    try:
        cmp = await holdout.compare(
            candidate, incumbent, policy.repetitions, store=store, backends=backends,
            decisions=decisions, code=code, n_boot=policy.bootstrap.n_boot,
            seed=policy.bootstrap.seed, failure_policy=policy.failure_policy, checkpoint=True,
            gate_id=gate_id, cycle_id=cycle_id,
        )  # fmt: skip
        decision = decide(cmp, policy, choice, gate_id=gate_id, cycle_id=cycle_id,
                          policy_sha256=policy_sha256, git_sha=code.git_sha, prior_aborts=prior,
                          r37=r37)  # fmt: skip
        await store.record_decision(gate_id, decision.model_dump(mode="json"))
    except BaseException:
        with contextlib.suppress(Exception):  # never mask the original failure
            await store.release_budget(gate_id)
        raise
    if policy.publish_summary:
        try:
            await record_summary(summary_db, decision)
        except Exception as exc:
            raise SummaryNotRecorded(decision, exc) from exc
    return decision


def format_decision(d: GateDecision | Mapping[str, Any]) -> str:
    d = d.model_dump(mode="json") if isinstance(d, GateDecision) else dict(d)
    lines = [f"{d['candidate_version']} vs {d['incumbent_version']}: {d['label']}"]
    if d["reasons"]:
        lines.append("reasons: " + ", ".join(d["reasons"]))
    for metric, x in d["deltas"].items():
        mean = "n/a" if x["mean_delta"] is None else f"{x['mean_delta']:+.3f}"
        ci = ("CI null" if x["ci95_low"] is None
              else f"CI95 [{x['ci95_low']:+.3f}, {x['ci95_high']:+.3f}]")  # fmt: skip
        sd = "" if x["noise_sd"] is None else f", noise SD {x['noise_sd']:.3f}"
        lines.append(f"  {metric}: {mean} ({ci}{sd}, {x['n_cases']} cases)")
    lines += [f"note: {n}" for n in d["notes"]]
    r37 = d.get("r37")
    if r37:
        lines.append(f"R37 diff check: {r37.get('status')}"
                     + (" (regression; not gating)" if r37.get("regression") else "")
                     + (f" ({r37['reason']}; not gating)" if r37.get("status") == "error"
                        else ""))  # fmt: skip
    if not d["deployable"]:
        lines.append("dev-only: this decision can never change the deployed default")
    elif d["decision"] == "promoted":
        lines.append("next: a human commits PROMOTIONS.md and the default version")
    return "\n".join(lines)
