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
from womm.evolve.edits import EditRejected, build_candidate, render_diff, validate_diff
from womm.evolve.gepa_adapter import (
    PROPOSAL_SPLITS,
    CandidateEvaluator,
    CaseRef,
    WommAdapter,
    population,
)
from womm.evolve.planner_view import PlannerView
from womm.evolve.proposers import Budget, Proposer, pattern_key
from womm.evolve.replay import (
    ReplayIncomplete,
    ReplayStore,
    ReplayWorker,
    code_version,
    judge_version,
)
from womm.llm.base import LLMError
from womm.models.system_version import SystemVersion

log = logging.getLogger("womm.evolve.cycle")


@dataclass
class ReplayEvaluator:
    """``CandidateEvaluator`` on the archive (U3) and the resumable replay store (U4). The judge
    is the worker's ``judge_sv`` (the seed's), so candidates cannot move their own yardstick."""

    archive_store: Archive
    store: ReplayStore
    worker: ReplayWorker

    @property
    def judge_version(self) -> str:
        """The pinned judge: archive metrics and failure patterns are read for this judge."""
        return judge_version(self.worker.judge_sv)

    @property
    def code_version(self) -> str:
        """The code replays run on: failure patterns are read for this code only."""
        return code_version(self.worker.code)

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
        status = await self.worker.run_batch(batch)
        if not status["complete"]:
            why = (
                f"halted: {status['halted']}"
                if status["halted"]
                else (f"{status['infra_errored']} infra-errored item(s) past the attempts cap")
            )
            raise ReplayIncomplete(f"replay batch {batch} is incomplete ({why}); resume it with "
                                   f"womm evolve worker {batch} --resume")  # fmt: skip
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
    evaluator: CandidateEvaluator,
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
    best = await choose_candidate(
        view, base, front, budget.grounding_tolerance, getattr(evaluator, "judge_version", None)
    )
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


async def _val_means(
    view: PlannerView, version_id: str, judge: str | None = None
) -> dict[str, float]:
    return {
        m["metric"]: m["mean"]
        for m in await view.metrics(version_id, judge_version=judge)
        if m["split"] == "val" and m["level"] == "split"
    }


async def choose_candidate(
    view: PlannerView,
    base: SystemVersion,
    front: list[str],
    grounding_tolerance: float,
    judge: str | None = None,
) -> str:
    """From the val Pareto front: the best mean val coverage whose val grounding is within
    tolerance of the base's (archive metrics, read through PlannerView). Ties keep the earlier
    candidate; the base wins when nothing beats it."""
    base_m = await _val_means(view, base.version_id, judge)
    best_id, best_cov = base.version_id, base_m.get("coverage", float("-inf"))
    floor = base_m.get("grounding", 0.0) - grounding_tolerance
    for vid in front:
        m = await _val_means(view, vid, judge)
        cov = m.get("coverage")
        if cov is None or m.get("grounding", 0.0) < floor:
            continue
        if cov > best_cov:
            best_id, best_cov = vid, cov
    return best_id


# ---------------------------------------------------------------- topology stage


MAX_EXAMPLES = 20


def unowned_patterns(patterns: list[dict], min_proposals: int = 2) -> list[dict]:
    """Persistent ``(missed_impact, category, owner = none)`` patterns spanning at least
    ``min_proposals`` proposals (fixtures), most persistent first. Single-run data
    (persistence unknown) never qualifies."""
    return [
        p
        for p in patterns
        if p["kind"] == "missed_impact"
        and p["owner"] == "none"
        and p["persistence"] == "known"
        and p["persistent_misses"] > 0
        and len(p["proposals"]) >= min_proposals
    ]


@dataclass
class TopologyResult:
    candidate: SystemVersion | None
    reason: str
    target: dict | None = None
    proposal: dict | None = None
    rejections: list[dict] = field(default_factory=list)
    spent_usd: float = 0.0


async def _examples(
    view: PlannerView, version_id: str, target: dict, population: dict
) -> list[dict]:
    """The missed impacts behind ``target``: train golden text only."""
    cases = view.cases("train")
    seen, out = set(), []
    for e in await view.failure_events(version_id, PROPOSAL_SPLITS, **population):
        key = (e.case_id, e.item_id)
        if (e.kind, e.category, e.owner) != ("missed_impact", target["category"], "none"):
            continue
        if key in seen or e.case_id not in target["cases"] or e.case_id not in cases:
            continue
        seen.add(key)
        item = next((i for i in cases[e.case_id].expected_impacts if i.expected_id == e.item_id),
                    None)  # fmt: skip
        if item is not None:
            out.append({"proposal": e.fixture, "affected_actor": item.affected_actor,
                        "mechanism": item.mechanism, "impact": item.impact,
                        "provision_keys": item.provision_keys})  # fmt: skip
    return out[:MAX_EXAMPLES]


async def run_topology_stage(
    *,
    seed: SystemVersion,
    parent: SystemVersion,
    view: PlannerView,
    evaluator: CandidateEvaluator,
    proposer: Proposer,
    budget: Budget,
    cycle_id: str | None = None,
) -> TopologyResult:
    """Propose one new expert (registry entry, prompt, router gloss) on top of ``parent``, the
    prompt stage's choice, only when an unowned miss pattern persists on it across enough
    train proposals (val is for selection only). A candidate has at most ``seed``'s expert
    count + 1. The candidate is archived (origin topology) and replayed on val."""
    if len(parent.spec.experts) > len(seed.spec.experts):
        return TopologyResult(None, "expert cap reached: the parent already adds an expert")
    targets = unowned_patterns(
        await view.failure_patterns(parent.version_id, PROPOSAL_SPLITS, **population(evaluator)),
        budget.min_pattern_proposals,
    )
    if not targets:
        return TopologyResult(
            None,
            "no persistent (missed_impact, category, owner none) pattern spanning "
            f"{budget.min_pattern_proposals}+ proposals",
        )
    target = targets[0]
    start_usd = proposer.spent_usd
    if budget.max_usd is not None and start_usd >= budget.max_usd:
        return TopologyResult(None, "budget: max_usd reached before the topology stage", target)
    experts = [{"id": e.id, "domain": e.domain, "router_gloss": e.router_gloss}
               for e in parent.spec.experts]  # fmt: skip
    examples = await _examples(view, parent.version_id, target, population(evaluator))
    rejections: list[dict] = []
    try:
        proposal = await proposer.propose_expert(target, examples, experts)
    except LLMError as exc:
        rejections.append({"op": "add_expert", "reason": f"proposer_error: {exc}"})
        return TopologyResult(None, "the expert proposal failed", target, None, rejections)
    raw = proposal.model_dump()
    if proposal.target_pattern.strip() != pattern_key(target):
        reason = f"proposal targets {proposal.target_pattern!r}, not {pattern_key(target)!r}"
        rejections.append({"op": "add_expert", "reason": reason})
        return TopologyResult(None, "proposal rejected", target, raw, rejections)
    op = {"op": "add_expert", "id": proposal.id, "domain": proposal.domain,
          "prompt_text": proposal.prompt_text, "router_gloss": proposal.router_gloss}  # fmt: skip
    try:
        diff = validate_diff(parent, [op])
    except EditRejected as exc:
        rejections.append({"op": exc.op or "add_expert", "reason": str(exc)})
        return TopologyResult(None, "proposal rejected", target, raw, rejections)
    child = build_candidate(parent, diff)
    await evaluator.archive(
        child, parent_id=parent.version_id, origin="topology", cycle_id=cycle_id,
        diff={"ops": diff.ops_json(), "rendered": render_diff(parent, child),
              "rationale": proposal.rationale, "target_pattern": pattern_key(target)},
        proposer=proposer.provenance("expert"),
    )  # fmt: skip
    val = sorted(view.cases("val"))
    scores = await evaluator.evaluate(child, "val", val, budget.val_repetitions) if val else []
    spent = proposer.spent_usd - start_usd + sum(s.cost_usd for s in scores)
    return TopologyResult(child, "proposed", target, raw, rejections, spent)


# ---------------------------------------------------------------- the cycle


@dataclass
class CycleResult:
    chosen: SystemVersion
    prompt: PromptStageResult | None
    topology: TopologyResult | None


async def run_cycle(
    *,
    base: SystemVersion,
    view: PlannerView,
    evaluator: CandidateEvaluator,
    proposer: Proposer,
    budget: Budget,
    stage: str = "both",
    cycle_id: str | None = None,
    run_dir: str | None = None,
) -> CycleResult:
    """Prompt stage, then (``both`` or ``topology``) the topology stage on its choice, then one
    candidate for the gate: the better of the two by val coverage, grounding within
    tolerance."""
    if stage not in ("prompt", "topology", "both"):
        raise ValueError(f"unknown stage {stage!r}")
    prompt = None
    parent = base
    if stage in ("prompt", "both"):
        prompt = await run_prompt_stage(base=base, view=view, evaluator=evaluator,
                                        proposer=proposer, budget=budget, cycle_id=cycle_id,
                                        run_dir=run_dir)  # fmt: skip
        parent = prompt.best
    topology = None
    if stage in ("topology", "both"):
        topology = await run_topology_stage(seed=base, parent=parent, view=view,
                                            evaluator=evaluator, proposer=proposer,
                                            budget=budget, cycle_id=cycle_id)  # fmt: skip
    chosen = parent
    if topology and topology.candidate is not None:
        val = sorted(view.cases("val"))
        await evaluator.evaluate(base, "val", val, budget.val_repetitions)  # cached when replayed
        front = [v for v in (parent.version_id, topology.candidate.version_id)
                 if v != base.version_id]  # fmt: skip
        best = await choose_candidate(
            view, base, front, budget.grounding_tolerance, getattr(evaluator, "judge_version", None)
        )
        chosen = {parent.version_id: parent, topology.candidate.version_id: topology.candidate}.get(
            best, base
        )
    return CycleResult(chosen, prompt, topology)
