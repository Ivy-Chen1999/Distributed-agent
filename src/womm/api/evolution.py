"""Evolution page data (self-evolution plan U9; R36 page 4, R37 visibility).

Read-only views over the main database: the candidate archive (lineage, config diffs, train/val
metrics, the R37 diff-check score) and, only when the promotion policy publishes them, the
promotion decision summaries. The deployed API is never given the holdout database's URL, so
a holdout result reaches this page only as a ``promotion_decisions`` summary: aggregates, with
no case or scenario id. Labels and reasons come from the gate as written; this module carries no
promotion logic of its own.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from womm.evolve.diff_regression import DIFF_CHECK_SPLIT, SCORE_METRIC, is_regression

PANEL_SPLITS = ("train", "val")
NOT_SUBMITTED = "Not submitted to holdout."
SEALED = (
    "No published holdout decision: not submitted to holdout, or the decision is recorded in "
    "the sealed audit only (publish_summary is off; `womm evolve show` reads it locally)."
)


class DeltaOut(BaseModel):
    mean_delta: float | None
    ci95_low: float | None
    ci95_high: float | None
    n_cases: int
    noise_sd: float | None = None


class DecisionOut(BaseModel):
    """A published promotion decision summary (aggregates only)."""

    gate_id: str
    created_at: str
    candidate_version: str
    incumbent_version: str
    mode: Literal["statistical", "weak", "dev"]
    deployable: bool
    decision: Literal["promoted", "rejected"]
    label: str
    reasons: list[str]
    notes: list[str]
    deltas: dict[str, DeltaOut]
    n_proposals: int
    flags: list[str]


class LineageNode(BaseModel):
    version_id: str
    name: str
    parent_id: str | None
    cycle_id: str | None
    origin: Literal["seed", "gepa", "topology", "twin", "manual"]
    created_at: str
    badges: list[str]
    twins: list[str]
    decision: Literal["promoted", "rejected"] | None
    label: str | None
    new_expert: str | None
    r37_regression: bool | None


class Lineage(BaseModel):
    nodes: list[LineageNode]
    publish_summary: bool | None


class MetricOut(BaseModel):
    mean: float | None
    sd: float | None
    n: int
    noise_sd: float | None


class SplitMetrics(BaseModel):
    split: Literal["train", "val"]
    judge_version: str
    metrics: dict[str, MetricOut]


class Score(BaseModel):
    mean: float | None
    sd: float | None
    n: int


class R37Out(BaseModel):
    status: Literal["available", "not_run"]
    score: Score | None
    reference_version: str | None
    reference_score: Score | None
    regression: bool | None
    message: str


class NewExpert(BaseModel):
    id: str
    domain: str
    router_gloss: str
    prompt_text: str
    target_pattern: dict[str, Any] | None
    rationale: str | None


class ExpertOut(BaseModel):
    id: str
    domain: str
    router_gloss: str | None


class HoldoutPanel(BaseModel):
    status: Literal["published", "not_submitted", "sealed"]
    message: str
    decisions: list[DecisionOut]


class CandidateDetail(BaseModel):
    node: LineageNode
    experts: list[ExpertOut]
    summary: dict[str, Any] | None
    rationale: str | None
    proposer: dict[str, Any] | None
    new_expert: NewExpert | None
    metrics: list[SplitMetrics]
    holdout: HoldoutPanel
    r37: R37Out


class CandidateDiff(BaseModel):
    version_id: str
    parent_id: str | None
    summary: dict[str, Any] | None
    prompts: dict[str, str]


def read_publish_summary(path: Path | None) -> bool | None:
    """The policy's publish_summary, or None when the file is not deployed (the API image
    carries no evals/)."""
    if path is None:
        return None
    try:
        value = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("publish_summary")
    except (OSError, yaml.YAMLError, AttributeError):
        return None
    return value if isinstance(value, bool) else None


class EvolutionView:
    """The archive, its metrics and the published decisions, indexed once per request."""

    def __init__(
        self,
        archive: list[dict],
        metrics: list[dict],
        decisions: list[dict],
        publish_summary: bool | None,
    ) -> None:
        self.rows = {r["version_id"]: r for r in archive}
        self.order = [r["version_id"] for r in archive]
        self.publish_summary = publish_summary
        self.metrics: dict[str, list[dict]] = {}
        for m in metrics:
            self.metrics.setdefault(m["version_id"], []).append(m)
        self.twins: dict[str, list[str]] = {}
        for vid in self.order:
            twin_of = self.rows[vid]["twin_of"]
            if twin_of in self.rows:
                self.twins.setdefault(twin_of, []).append(vid)
        self.decisions: dict[str, list[DecisionOut]] = {}
        for d in decisions:
            out = DecisionOut.model_validate(d | {"created_at": _iso(d["created_at"])})
            self.decisions.setdefault(d["candidate_version"], []).append(out)

    # ------------------------------------------------------------ helpers

    def is_twin(self, vid: str) -> bool:
        return self.rows[vid]["twin_of"] in self.rows

    def logical(self, vid: str) -> str:
        """The tree node a version is shown under: an api twin collapses into its dev node."""
        return self.rows[vid]["twin_of"] if self.is_twin(vid) else vid

    def node_decisions(self, vid: str) -> list[DecisionOut]:
        found = [d for v in (vid, *self.twins.get(vid, [])) for d in self.decisions.get(v, [])]
        return sorted(found, key=lambda d: (d.created_at, d.gate_id))

    def diff_score(self, vid: str) -> Score | None:
        for v in (vid, *self.twins.get(vid, [])):
            rows = [m for m in self.metrics.get(v, []) if m["split"] == DIFF_CHECK_SPLIT
                    and m["level"] == "case" and m["metric"] == SCORE_METRIC]  # fmt: skip
            if rows:
                r = rows[-1]
                return Score(mean=r["mean"], sd=r["sd"], n=r["n"])
        return None

    def reference(self, vid: str) -> str | None:
        """What a version's R37 score is compared with: the incumbent of its latest decision,
        else its parent's tree node."""
        decisions = self.node_decisions(vid)
        if decisions:
            incumbent = decisions[-1].incumbent_version
            return self.logical(incumbent) if incumbent in self.rows else incumbent
        parent = self.rows[vid]["parent_id"]
        return self.logical(parent) if parent in self.rows else None

    def r37(self, vid: str) -> R37Out:
        score = self.diff_score(vid)
        ref = self.reference(vid)
        ref_score = self.diff_score(ref) if ref in self.rows else None
        if score is None or score.mean is None:
            return R37Out(
                status="not_run", score=None, reference_version=ref, reference_score=ref_score,
                regression=None, message="R37 diff check not run for this version (womm evolve "
                "diffcheck; the reference answers may still be a template)",
            )  # fmt: skip
        regression = None
        if ref_score is not None and ref_score.mean is not None:
            regression = is_regression(score.model_dump(), ref_score.model_dump())
        message = ("monitoring only: never gates a promotion" if regression is not True
                   else "regression on the R37 diff check (monitoring only: the decision is "
                        "unchanged)")  # fmt: skip
        return R37Out(
            status="available",
            score=score,
            reference_version=ref,
            reference_score=ref_score,
            regression=regression,
            message=message,
        )

    # ------------------------------------------------------------ lineage

    def node(self, vid: str) -> LineageNode:
        row = self.rows[vid]
        decisions = self.node_decisions(vid)
        latest = decisions[-1] if decisions else None
        expert = _added_expert(row)
        badges = [row["origin"]]
        if expert and row["origin"] != "topology":
            badges.append("topology")
        if latest:
            badges.append(latest.decision)
            if latest.mode == "dev":
                badges.append("dev-only")
        return LineageNode(
            version_id=vid, name=row["name"], parent_id=row["parent_id"],
            cycle_id=row["cycle_id"], origin=row["origin"], created_at=_iso(row["created_at"]),
            badges=badges, twins=self.twins.get(vid, []),
            decision=latest.decision if latest else None, label=latest.label if latest else None,
            new_expert=expert["id"] if expert else None, r37_regression=self.r37(vid).regression,
        )  # fmt: skip

    def lineage(self) -> Lineage:
        nodes = [self.node(v) for v in self.order if not self.is_twin(v)]
        return Lineage(nodes=nodes, publish_summary=self.publish_summary)

    # ------------------------------------------------------------ one candidate

    def detail(self, vid: str) -> CandidateDetail:
        vid = self.logical(vid)
        row = self.rows[vid]
        diff = row["diff"] or {}
        expert = _added_expert(row)
        spec = row["spec"] or {}
        decisions = self.node_decisions(vid)
        if decisions:
            holdout = HoldoutPanel(status="published", message="", decisions=decisions)
        elif self.publish_summary:
            holdout = HoldoutPanel(status="not_submitted", message=NOT_SUBMITTED, decisions=[])
        else:
            holdout = HoldoutPanel(status="sealed", message=SEALED, decisions=[])
        return CandidateDetail(
            node=self.node(vid),
            experts=[
                ExpertOut(id=e["id"], domain=e["domain"], router_gloss=e.get("router_gloss"))
                for e in spec.get("experts", [])
            ],  # fmt: skip
            summary=(diff.get("rendered") or {}).get("summary"),
            rationale=diff.get("rationale"),
            proposer=row["proposer"],
            new_expert=None
            if expert is None
            else NewExpert(
                id=expert["id"],
                domain=expert["domain"],
                router_gloss=expert["router_gloss"],
                prompt_text=expert["prompt_text"],
                target_pattern=diff.get("target_pattern"),
                rationale=diff.get("rationale"),
            ),  # fmt: skip
            metrics=self.split_metrics(vid),
            holdout=holdout,
            r37=self.r37(vid),
        )

    def split_metrics(self, vid: str) -> list[SplitMetrics]:
        groups: dict[tuple[str, str], list[dict]] = {}
        for m in self.metrics.get(vid, []):
            if m["split"] in PANEL_SPLITS:
                groups.setdefault((m["split"], m["judge_version"]), []).append(m)
        out = []
        for (split, jv), rows in sorted(groups.items()):
            metrics = {}
            for m in rows:
                if m["level"] != "split":
                    continue
                cases = [c for c in rows if c["level"] == "case" and c["metric"] == m["metric"]]
                metrics[m["metric"]] = MetricOut(mean=m["mean"], sd=m["sd"], n=m["n"],
                                                 noise_sd=_pooled_sd(cases))  # fmt: skip
            if metrics:
                out.append(SplitMetrics(split=split, judge_version=jv, metrics=metrics))
        return out

    def diff(self, vid: str) -> CandidateDiff:
        row = self.rows[vid]
        rendered = (row["diff"] or {}).get("rendered") or {}
        return CandidateDiff(version_id=vid, parent_id=row["parent_id"],
                             summary=rendered.get("summary"),
                             prompts=rendered.get("prompts") or {})  # fmt: skip


def _added_expert(row: dict) -> dict | None:
    ops = (row["diff"] or {}).get("ops") or []
    return next((op for op in ops if op.get("op") == "add_expert"), None)


def _pooled_sd(case_rows: list[dict]) -> float | None:
    """Within-case run-to-run SD, pooled over cases (the noise band)."""
    dof = sum(r["n"] - 1 for r in case_rows if r["sd"] is not None and r["n"] > 1)
    if dof == 0:
        return None
    ss = sum(r["sd"] ** 2 * (r["n"] - 1) for r in case_rows if r["sd"] is not None and r["n"] > 1)
    return math.sqrt(ss / dof)


def _iso(value: Any) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
