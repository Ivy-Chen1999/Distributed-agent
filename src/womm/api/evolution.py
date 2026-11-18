"""Evolution page data (self-evolution plan U9; R36 page 4, R37 visibility).

Read-only views over the main database: the candidate archive (lineage, config diffs, train/val
metrics, the R37 diff-check score) and, only when the promotion policy publishes them, the
promotion decision summaries. The deployed API is never given the holdout database's URL, so
a holdout result reaches this page only as a ``promotion_decisions`` summary: aggregates, with
no case or scenario id. Labels and reasons come from the gate as written; this module carries no
promotion logic of its own (the node badge only names a weak or dev pass ``not-rejected``).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

from womm.evolve.diff_regression import (
    DIFF_CHECK_SPLIT,
    SCORE_METRIC,
    is_regression,
    newest,
    shared_judge,
)

PANEL_SPLITS = ("train", "val")
# What a lineage node shows against its parent: coverage, and the citation rate so that a gain in
# coverage bought with unsupported claims is visible on the tree.
NODE_METRICS = ("coverage", "grounding")
# The smallest band a node change must exceed to be called a gain or a loss: below it the change
# does not show at the tree's two decimals.
NOISE_FLOOR = 0.005
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


class CrossJudgeOut(BaseModel):
    """The cross-family judge re-score of the gate's comparison (``womm evolve rescore``):
    aggregates only, report-only."""

    label: Literal["agrees", "cross-judge-sensitive", "inconclusive"]
    mean_delta: float | None
    ci95_low: float | None
    ci95_high: float | None
    judge: str
    gate_delta: float | None
    rescored_on: str | None = None


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
    cross_judge: CrossJudgeOut | None = None


class NodeMetric(BaseModel):
    mean: float | None
    noise: float | None
    parent_mean: float | None
    delta: float | None
    # True: the change exceeds the larger run-to-run noise band of the two versions; False:
    # within it; None: no parent score under this judge, or no noise estimate.
    beyond_noise: bool | None


class NodeScore(BaseModel):
    split: Literal["train", "val"]
    judge_version: str
    metrics: dict[str, NodeMetric]


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
    score: NodeScore | None = None


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
    judge_version: str | None = None
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
    prompt_rationales: dict[str, str]
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
    """The archive (ids and origins only), its diff-check metrics and the published decisions,
    indexed once per request. A candidate's spec, diff and train/val metrics are read for that
    candidate only and passed to ``detail`` / ``diff``."""

    def __init__(
        self,
        archive: list[dict],
        metrics: list[dict],
        decisions: list[dict],
        publish_summary: bool | None,
        scores: list[dict] | None = None,
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
        self.scores: dict[str, list[dict]] = {}
        for m in scores or []:
            self.scores.setdefault(m["version_id"], []).append(m)
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

    def diff_scores(self, vid: str) -> dict[str, dict]:
        """Judge version -> the latest diff-check score under it (the node's own, else its api
        twin's), with when it was recorded."""
        for v in (vid, *self.twins.get(vid, [])):
            scores = {m["judge_version"]: {"mean": m["mean"], "sd": m["sd"], "n": m["n"],
                                           "recorded_at": _iso(m.get("updated_at"))}
                      for m in self.metrics.get(v, []) if m["split"] == DIFF_CHECK_SPLIT
                      and m["level"] == "case" and m["metric"] == SCORE_METRIC
                      and m["mean"] is not None}  # fmt: skip
            if scores:
                return scores
        return {}

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
        """The R37 score next to the reference's, compared only under a judge both were scored
        by (the most recently recorded one); never two different judges."""
        scores = self.diff_scores(vid)
        ref = self.reference(vid)
        ref_scores = self.diff_scores(ref) if ref in self.rows else {}
        if not scores:
            return R37Out(
                status="not_run", score=None, reference_version=ref,
                reference_score=_score(newest(ref_scores)), regression=None,
                message="R37 diff check not run for this version (womm evolve diffcheck; the "
                "reference answers may still be a template)",
            )  # fmt: skip
        jv = shared_judge(scores, ref_scores)
        if jv is None:
            message = "monitoring only: never gates a promotion"
            if ref_scores:
                message = ("not compared: no diff-check score under a judge both versions share "
                           "(monitoring only)")  # fmt: skip
            return R37Out(status="available", score=_score(newest(scores)), reference_version=ref,
                          reference_score=_score(newest(ref_scores)), regression=None,
                          message=message)  # fmt: skip
        regression = is_regression(scores[jv], ref_scores[jv])
        message = ("monitoring only: never gates a promotion" if not regression
                   else "regression on the R37 diff check (monitoring only: the decision is "
                        "unchanged)")  # fmt: skip
        return R37Out(status="available", judge_version=jv, score=_score(scores[jv]),
                      reference_version=ref, reference_score=_score(ref_scores[jv]),
                      regression=regression, message=message)  # fmt: skip

    # ------------------------------------------------------------ lineage

    def split_scores(self, vid: str, split: str) -> dict[str, dict]:
        """Judge version -> the node's ``NODE_METRICS`` on ``split`` as (mean, pooled noise),
        the node's own else its api twin's, with when they were recorded."""
        for v in (vid, *self.twins.get(vid, [])):
            out: dict[str, dict] = {}
            for m in self.scores.get(v, []):
                if m["split"] != split:
                    continue
                entry = out.setdefault(m["judge_version"], {"recorded_at": "", "metrics": {}})
                entry["metrics"][m["metric"]] = (m["mean"], m["noise"])
                entry["recorded_at"] = max(entry["recorded_at"], _iso(m.get("updated_at")))
            if out:
                return out
        return {}

    def node_score(self, vid: str) -> NodeScore | None:
        """The node's scores next to its parent's tree node (the parent, not the incumbent R37
        compares with), only under a judge both were scored by: val first, else train, on the
        first split with a shared judge; without one, the node's own newest scores, no delta."""
        parent = self.rows[vid]["parent_id"]
        parent = self.logical(parent) if parent in self.rows else None
        sides = [(split, self.split_scores(vid, split),
                  self.split_scores(parent, split) if parent else {})
                 for split in ("val", "train")]  # fmt: skip
        sides = [x for x in sides if x[1]]
        if not sides:
            return None
        shared = [(split, own, theirs, shared_judge(own, theirs)) for split, own, theirs in sides]
        split, own, theirs, jv = next((x for x in shared if x[3] is not None), shared[0])
        if jv is None:
            jv = max(own, key=lambda j: (own[j]["recorded_at"], j))
        ref = theirs.get(jv, {}).get("metrics", {})
        metrics = {}
        for name in NODE_METRICS:
            if name not in own[jv]["metrics"]:
                continue
            mean, noise = own[jv]["metrics"][name]
            parent_mean, parent_noise = ref.get(name, (None, None))
            delta = None if mean is None or parent_mean is None else mean - parent_mean
            # Called a change only against both versions' noise bands, never one side's, and
            # never below a floor: identical runs give a zero band.
            beyond = None
            if delta is not None and noise is not None and parent_noise is not None:
                beyond = abs(delta) > max(noise, parent_noise, NOISE_FLOOR)
            metrics[name] = NodeMetric(mean=mean, noise=noise, parent_mean=parent_mean,
                                       delta=delta, beyond_noise=beyond)  # fmt: skip
        return NodeScore(split=split, judge_version=jv, metrics=metrics)

    def node(self, vid: str) -> LineageNode:
        row = self.rows[vid]
        decisions = self.node_decisions(vid)
        latest = decisions[-1] if decisions else None
        expert = row.get("new_expert")
        badges = [row["origin"]]
        if expert and row["origin"] != "topology":
            badges.append("topology")
        if latest:
            badges.append(decision_badge(latest))
            if latest.mode == "dev":
                badges.append("dev-only")
        return LineageNode(
            version_id=vid, name=row["name"], parent_id=row["parent_id"],
            cycle_id=row["cycle_id"], origin=row["origin"], created_at=_iso(row["created_at"]),
            badges=badges, twins=self.twins.get(vid, []),
            decision=latest.decision if latest else None, label=latest.label if latest else None,
            new_expert=expert, r37_regression=self.r37(vid).regression,
            score=self.node_score(vid),
        )  # fmt: skip

    def lineage(self) -> Lineage:
        nodes = [self.node(v) for v in self.order if not self.is_twin(v)]
        return Lineage(nodes=nodes, publish_summary=self.publish_summary)

    # ------------------------------------------------------------ one candidate

    def detail(self, vid: str, row: dict, metrics: list[dict]) -> CandidateDetail:
        """``row``: the full archive row of ``self.logical(vid)``; ``metrics``: its train/val
        metric rows."""
        vid = self.logical(vid)
        diff = row["diff"] or {}
        expert = _added_expert(row)
        spec = row["spec"] or {}
        rationale, prompt_rationales = _rationales(diff.get("rationale"))
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
            rationale=rationale,
            prompt_rationales=prompt_rationales,
            proposer=row["proposer"],
            new_expert=None
            if expert is None
            else NewExpert(
                id=expert["id"],
                domain=expert["domain"],
                router_gloss=expert["router_gloss"],
                prompt_text=expert["prompt_text"],
                target_pattern=_pattern(diff.get("target_pattern")),
                rationale=rationale,
            ),  # fmt: skip
            metrics=split_metrics(metrics),
            holdout=holdout,
            r37=self.r37(vid),
        )

    @staticmethod
    def diff(row: dict) -> CandidateDiff:
        rendered = (row["diff"] or {}).get("rendered") or {}
        return CandidateDiff(version_id=row["version_id"], parent_id=row["parent_id"],
                             summary=rendered.get("summary"),
                             prompts=rendered.get("prompts") or {})  # fmt: skip


def decision_badge(d: DecisionOut) -> str:
    """``promoted`` for a statistical promotion only; a weak or dev pass is ``not-rejected``
    (owner decision 2026-10-15: directional, never the deployed default); an aborted
    comparison is ``inconclusive``."""
    if "inconclusive" in d.reasons:  # aborted: no result, not a rejection on the merits
        return "inconclusive"
    return "not-rejected" if d.decision == "promoted" and d.mode != "statistical" else d.decision


def split_metrics(rows: list[dict]) -> list[SplitMetrics]:
    """Split-level train/val aggregates per (split, judge), with the pooled case noise."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for m in rows:
        if m["split"] in PANEL_SPLITS:
            groups.setdefault((m["split"], m["judge_version"]), []).append(m)
    out = []
    for (split, jv), group in sorted(groups.items()):
        metrics = {}
        for m in group:
            if m["level"] != "split":
                continue
            cases = [c for c in group if c["level"] == "case" and c["metric"] == m["metric"]]
            metrics[m["metric"]] = MetricOut(mean=m["mean"], sd=m["sd"], n=m["n"],
                                             noise_sd=_pooled_sd(cases))  # fmt: skip
        if metrics:
            out.append(SplitMetrics(split=split, judge_version=jv, metrics=metrics))
    return out


def _rationales(raw: Any) -> tuple[str | None, dict[str, str]]:
    """The stored ``rationale`` split by stage: the topology stage writes one sentence, the
    prompt stage one reason per edited role ({role: reason})."""
    if isinstance(raw, str):
        return raw, {}
    if isinstance(raw, dict):
        return None, {str(k): v for k, v in raw.items() if isinstance(v, str) and v}
    return None, {}


def _pattern(value: Any) -> dict[str, Any] | None:
    """The new expert's target pattern. The topology stage archives it as its key,
    ``kind/category/owner``; a mapping is passed through."""
    if isinstance(value, str):
        parts = value.strip().split("/")
        if len(parts) == 3:
            return dict(zip(("kind", "category", "owner"), parts, strict=True))
        return {"pattern": value}
    return value if isinstance(value, dict) else None


def _score(s: dict | None) -> Score | None:
    return None if s is None else Score(mean=s["mean"], sd=s["sd"], n=s["n"])


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
