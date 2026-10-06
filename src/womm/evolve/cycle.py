"""The self-evolution cycle (U5): prompt stage on GEPA, then the topology stage, then one
candidate for the gate.

Planner-side: reads go through ``PlannerView`` only; writes are archive rows (U3) and replay
items (U4), both train/val only. Nothing here can reach the holdout or a promotion decision.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import gepa

from womm.eval.evaluators import CaseScore
from womm.evolve.archive import Archive
from womm.evolve.gepa_adapter import CaseRef, WommAdapter
from womm.evolve.planner_view import PlannerView
from womm.evolve.proposers import Budget, Proposer
from womm.evolve.replay import ReplayStore, ReplayWorker
from womm.models.system_version import SystemVersion

log = logging.getLogger("womm.evolve.cycle")


@dataclass
class ReplayEvaluator:
    """``CandidateEvaluator`` on the archive (U3) and the resumable replay store (U4). The judge
    is the worker's ``judge_sv`` (the seed's), so candidates cannot move their own yardstick."""

    archive_store: Archive
    store: ReplayStore
    worker: ReplayWorker

    async def archive(self, sv, *, parent_id, origin, cycle_id, diff, proposer) -> None:
        await self.archive_store.archive(
            sv, origin=origin, parent_id=parent_id, cycle_id=cycle_id, diff=diff, proposer=proposer
        )

    async def evaluate(
        self, sv: SystemVersion, split: str, case_ids: list[str], repetitions: int
    ) -> list[CaseScore]:
        if await self.archive_store.get(sv.version_id) is None:
            raise KeyError(f"{sv.version_id} is not archived; archive it before replaying")
        batch = await self.store.submit(
            sv.version_id,
            split,
            repetitions,
            judge_sv=self.worker.judge_sv,
            code=self.worker.code,
            case_ids=case_ids,
        )
        await self.worker.run_batch(batch)
        return await self.store.results(batch)


class _UsdStopper:
    """A GEPA stop condition on the cycle's spend (replays plus proposer calls)."""

    def __init__(self, adapter: WommAdapter) -> None:
        self.adapter = adapter

    def __call__(self, _state) -> bool:
        return self.adapter.over_budget()


class _QuietLogger:
    def log(self, message: str) -> None:
        log.debug("gepa: %s", message)


@dataclass
class PromptStageResult:
    best: SystemVersion
    front: list[str]
    candidates: list[str]
    archived: list[str]
    rejections: list[dict]
    metric_calls: int
    spent_usd: float
    val_scores: dict[str, float] = field(default_factory=dict)


async def run_prompt_stage(
    *,
    base: SystemVersion,
    view: PlannerView,
    evaluator,
    proposer: Proposer,
    budget: Budget,
    cycle_id: str | None = None,
    run_dir: str | None = None,
    seed: int = 0,
) -> PromptStageResult:
    """GEPA over ``base``'s prompt components on train (minibatches) and val (Pareto front),
    within ``budget``. ``run_dir`` lets GEPA resume its own state; replays are cached anyway."""
    train = sorted(view.cases("train"))
    val = sorted(view.cases("val"))
    if not train or not val:
        raise ValueError("the prompt stage needs at least one train and one val case")
    adapter = WommAdapter(
        base=base,
        view=view,
        evaluator=evaluator,
        proposer=proposer,
        budget=budget,
        cycle_id=cycle_id,
    )
    result = await asyncio.to_thread(
        gepa.optimize,
        seed_candidate=adapter.seed_candidate(),
        trainset=[CaseRef("train", c) for c in train],
        valset=[CaseRef("val", c) for c in val],
        adapter=adapter,
        candidate_selection_strategy="pareto",
        module_selector="round_robin",
        reflection_minibatch_size=min(budget.minibatch_size, len(train)),
        max_metric_calls=budget.max_metric_calls,
        stop_callbacks=[_UsdStopper(adapter)],
        perfect_score=1.0,
        skip_perfect_score=True,
        use_merge=False,
        run_dir=run_dir,
        seed=seed,
        logger=_QuietLogger(),
        display_progress_bar=False,
    )
    versions = [adapter.version_of(c).version_id for c in result.candidates]
    front_idx = sorted({i for s in result.per_val_instance_best_candidates.values() for i in s})
    front = list(dict.fromkeys(versions[i] for i in front_idx))
    scores = {versions[i]: s for i, s in enumerate(result.val_aggregate_scores)}
    best = await choose_candidate(view, base, front, budget.grounding_tolerance)
    by_id = {adapter.version_of(c).version_id: adapter.version_of(c) for c in result.candidates}
    return PromptStageResult(
        best=by_id.get(best, base),
        front=front,
        candidates=versions,
        archived=list(adapter.archived),
        rejections=list(adapter.rejections),
        metric_calls=adapter.metric_calls,
        spent_usd=adapter.spent_usd,
        val_scores=scores,
    )


async def _val_means(view: PlannerView, version_id: str) -> dict[str, float]:
    return {
        m["metric"]: m["mean"]
        for m in await view.metrics(version_id)
        if m["split"] == "val" and m["level"] == "split"
    }


async def choose_candidate(
    view: PlannerView, base: SystemVersion, front: list[str], grounding_tolerance: float
) -> str:
    """From the val Pareto front: the best mean val coverage whose val grounding is within
    tolerance of the base's (archive metrics, read through PlannerView). Ties keep the earlier
    candidate; the base wins when nothing beats it."""
    base_m = await _val_means(view, base.version_id)
    best_id, best_cov = base.version_id, base_m.get("coverage", float("-inf"))
    floor = base_m.get("grounding", 0.0) - grounding_tolerance
    for vid in front:
        m = await _val_means(view, vid)
        cov = m.get("coverage")
        if cov is None or m.get("grounding", 0.0) < floor:
            continue
        if cov > best_cov:
            best_id, best_cov = vid, cov
    return best_id
