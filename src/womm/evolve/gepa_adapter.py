"""GEPA adapter (U5): WOMM's prompt stage on the ``gepa`` engine, with our own evaluator,
reflective dataset and proposer.

GEPA (``gepa-ai/gepa``) owns the loop: minibatch sampling, acceptance, the per-instance Pareto
front over val, and the ``max_metric_calls`` budget. WOMM owns everything that touches data or
an LLM:

- ``evaluate`` turns a GEPA candidate (``{role: prompt text}``) into a SystemVersion through
  U2's typed ``edit_prompt`` ops (always against the cycle base, so a set of texts has one
  version id however it was reached), and scores it through U4's resumable replay store with the
  seed's pinned judge. Finished replays are never re-run, so a restarted GEPA run is cheap.
- ``make_reflective_dataset`` reads Failure Memory events, saved runs and train/val golden cases
  through ``PlannerView`` only.
- ``propose_new_texts`` asks the Improvement Planner (``Proposer``) for a structured
  ``PromptEdit`` per component, validates it with U2, and archives the accepted child (U3) under
  its GEPA parent. GEPA's reflection LM is never called, so litellm is never imported.

GEPA's engine is synchronous; it runs in a worker thread (``asyncio.to_thread``) and every adapter
method hands its async work back to the event loop that owns the database pools.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
from collections.abc import Coroutine, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from gepa import EvaluationBatch

from womm.eval.evaluators import CaseScore
from womm.eval.golden import GoldenCase
from womm.evolve.edits import (
    EditRejected,
    build_candidate,
    render_diff,
    role_prompts,
    validate_diff,
)
from womm.evolve.failure_memory import FailureEvent
from womm.evolve.planner_view import PLANNER_SPLITS, PlannerView
from womm.evolve.proposers import Budget, Proposer
from womm.llm.base import LLMError
from womm.models.run import RunResult
from womm.models.system_version import SystemVersion


@dataclass(frozen=True, order=True)
class CaseRef:
    """One GEPA data instance: a train or val golden case."""

    split: str
    case_id: str


class CandidateEvaluator(Protocol):
    """The write side the adapter needs: archive a candidate, replay it on train/val cases.
    Implementations refuse any split other than train/val (``ReplayStore`` does)."""

    async def archive(
        self,
        sv: SystemVersion,
        *,
        parent_id: str | None,
        origin: str,
        cycle_id: str | None,
        diff: dict | None,
        proposer: dict | None,
    ) -> None: ...

    async def evaluate(
        self, sv: SystemVersion, split: str, case_ids: list[str], repetitions: int
    ) -> list[CaseScore]: ...


def candidate_key(candidate: Mapping[str, str]) -> str:
    blob = json.dumps(dict(candidate), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class WommAdapter:
    """A GEPAAdapter over WOMM's prompt components. Its only data access is ``view``."""

    base: SystemVersion
    view: PlannerView
    evaluator: CandidateEvaluator
    proposer: Proposer
    budget: Budget
    cycle_id: str | None = None
    loop: asyncio.AbstractEventLoop | None = None
    rejections: list[dict] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    metric_calls: int = 0
    _replay_usd: dict[str, float] = field(default_factory=dict)
    _versions: dict[str, SystemVersion] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.view, PlannerView):
            raise TypeError(f"WommAdapter reads through a PlannerView only, not {type(self.view)}")
        self.loop = self.loop or asyncio.get_running_loop()
        self.components = role_prompts(self.base)
        self._versions[candidate_key(self.seed_candidate())] = self.base

    # ------------------------------------------------------------ plumbing

    def _await[T](self, coro: Coroutine[Any, Any, T]) -> T:
        """Run ``coro`` on the adapter's event loop from GEPA's worker thread."""
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            coro.close()
            raise RuntimeError("WommAdapter methods run in GEPA's worker thread, not the loop")
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    @property
    def spent_usd(self) -> float:
        return sum(self._replay_usd.values()) + self.proposer.spent_usd

    def over_budget(self) -> bool:
        cap = self.budget.max_usd
        return cap is not None and self.spent_usd >= cap

    def seed_candidate(self) -> dict[str, str]:
        return {role: self.base.prompt_file(path) for role, path in role_prompts(self.base).items()}

    def version_of(self, candidate: Mapping[str, str]) -> SystemVersion:
        """The SystemVersion of a candidate: U2 ``edit_prompt`` ops against the cycle base."""
        key = candidate_key(candidate)
        if key in self._versions:
            return self._versions[key]
        if set(candidate) != set(self.components):
            raise ValueError(f"candidate components {sorted(candidate)} are not the base's")
        seed = self.seed_candidate()
        ops = [
            {"op": "edit_prompt", "role": role, "new_text": text}
            for role, text in sorted(candidate.items())
            if text != seed[role]
        ]
        sv = build_candidate(self.base, validate_diff(self.base, ops)) if ops else self.base
        self._versions[key] = sv
        return sv

    async def _archive(self, sv: SystemVersion, parent: SystemVersion, extra: dict) -> None:
        if sv.version_id == parent.version_id or sv.version_id in self.archived:
            return
        ops = [
            {"op": "edit_prompt", "role": role, "new_text": sv.prompt_file(path)}
            for role, path in role_prompts(sv).items()
            if sv.prompt_file(path) != parent.prompt_file(role_prompts(parent)[role])
        ]
        await self.evaluator.archive(
            sv,
            parent_id=parent.version_id,
            origin="gepa",
            cycle_id=self.cycle_id,
            diff={"ops": ops, "rendered": render_diff(parent, sv), **extra},
            proposer=self.proposer.provenance("prompt"),
        )
        self.archived.append(sv.version_id)

    # ------------------------------------------------------------ GEPAAdapter: evaluate

    def evaluate(
        self, batch: list[CaseRef], candidate: dict[str, str], capture_traces: bool = False
    ) -> EvaluationBatch:
        sv = self.version_of(candidate)
        if sv.version_id != self.base.version_id and sv.version_id not in self.archived:
            # A candidate GEPA built without our proposer (e.g. restored from its own state).
            self._await(self._archive(sv, self.base, {}))
        by_split: dict[str, list[str]] = {}
        for ref in batch:
            if ref.split not in PLANNER_SPLITS:
                raise ValueError(f"GEPA instances are train/val only, got {ref.split!r}")
            by_split.setdefault(ref.split, []).append(ref.case_id)
        scores: dict[CaseRef, list[CaseScore]] = {}
        calls = 0
        for split, case_ids in by_split.items():
            reps = self._repetitions(split)
            got = self._await(self.evaluator.evaluate(sv, split, sorted(set(case_ids)), reps))
            calls += len(case_ids) * reps
            for s in got:
                scores.setdefault(CaseRef(split, s.case_id), []).append(s)
                if s.run_id:
                    self._replay_usd[s.run_id] = s.cost_usd
        self.metric_calls += calls
        outputs, values, trajectories, objectives = [], [], [], []
        for ref in batch:
            out = self._summarise(ref, scores.get(ref, []))
            outputs.append(out)
            values.append(out["score"])
            objectives.append({k: out[k] or 0.0 for k in ("coverage", "grounding")})
            trajectories.append({**out, "version_id": sv.version_id})
        return EvaluationBatch(
            outputs=outputs,
            scores=values,
            objective_scores=objectives,
            trajectories=trajectories if capture_traces else None,
            num_metric_calls=calls,
        )

    def _repetitions(self, split: str) -> int:
        b = self.budget
        return b.train_repetitions if split == "train" else b.val_repetitions

    def _summarise(self, ref: CaseRef, scores: list[CaseScore]) -> dict:
        """Mean coverage over the scored repetitions; 0 below the grounding floor or when no
        repetition was scored."""
        scored = [s for s in scores if s.outcome == "scored" and s.coverage is not None]

        def mean(metric: str) -> float | None:
            vals = [getattr(s, metric) for s in scored if getattr(s, metric) is not None]
            return statistics.fmean(vals) if vals else None

        coverage, grounding = mean("coverage"), mean("grounding")
        floor = self.budget.grounding_floor
        score = (
            0.0 if coverage is None or (grounding is not None and grounding < floor) else (coverage)
        )
        return {
            "split": ref.split,
            "case_id": ref.case_id,
            "score": score,
            "coverage": coverage,
            "grounding": grounding,
            "omissions_addressed": mean("omissions_addressed"),
            "run_ids": sorted(s.run_id for s in scored if s.run_id),
            "errored": len(scores) - len(scored),
        }

    # ------------------------------------------------------------ reflective dataset

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch,
        components_to_update: list[str],
    ) -> dict[str, list[dict]]:
        sv = self.version_of(candidate)
        return self._await(
            self._reflective(sv, list(eval_batch.trajectories or []), components_to_update)
        )

    async def _reflective(
        self, sv: SystemVersion, trajectories: list[dict], components: list[str]
    ) -> dict[str, list[dict]]:
        events = await self.view.failure_events(sv.version_id)
        patterns = await self.view.failure_patterns(sv.version_id)
        cases = {split: self.view.cases(split) for split in PLANNER_SPLITS}
        out: dict[str, list[dict]] = {c: [] for c in components}
        for traj in trajectories:
            case = cases.get(traj["split"], {}).get(traj["case_id"])
            if case is None:
                continue
            run_ids = set(traj["run_ids"])
            runs = [r for r in [await self.view.run(rid) for rid in sorted(run_ids)] if r]
            mine = [e for e in events if e.run_id in run_ids]
            if traj["score"] >= 1.0 and not mine:
                continue  # nothing to learn from a perfect case
            for comp in components:
                out[comp].append(_record(comp, case, traj, runs, mine, patterns))
        return {c: _cap(recs, self.budget.reflective_chars) for c, recs in out.items()}

    # ------------------------------------------------------------ proposals

    def propose_new_texts(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        return self._await(self._propose(candidate, reflective_dataset, components_to_update))

    async def _propose(self, candidate, reflective_dataset, components) -> dict[str, str]:
        unchanged = {c: candidate[c] for c in components}
        parent = self.version_of(candidate)
        new = dict(candidate)
        rationale: dict[str, str] = {}
        for comp in components:
            if self.over_budget():
                self._reject(comp, "budget", "max_usd reached before the proposal")
                continue
            try:
                edit = await self.proposer.propose_prompt(
                    comp, candidate[comp], list(reflective_dataset.get(comp, []))
                )
            except LLMError as exc:
                self._reject(comp, "proposer_error", str(exc))
                continue
            if edit.role != comp:
                self._reject(comp, "edit_prompt", f"proposal edits {edit.role!r}, not {comp!r}")
                continue
            new[comp] = edit.new_text
            rationale[comp] = edit.rationale
        changed = [c for c in components if new[c] != candidate[c]]
        if not changed:
            return unchanged
        try:
            validate_diff(
                parent, [{"op": "edit_prompt", "role": c, "new_text": new[c]} for c in changed]
            )
            child = self.version_of(new)
        except EditRejected as exc:
            self._reject(",".join(changed), exc.op or "diff", str(exc))
            return unchanged
        await self._archive(child, parent, {"rationale": rationale})
        return {c: new[c] for c in components}

    def _reject(self, component: str, op: str, reason: str) -> None:
        self.rejections.append({"component": component, "op": op, "reason": reason})


# ---------------------------------------------------------------- reflective records


def _agent_of(component: str) -> str | None:
    return component.split(":", 1)[1] if component.startswith("expert:") else None


def _record(
    component: str,
    case: GoldenCase,
    traj: dict,
    runs: list[RunResult],
    events: list[FailureEvent],
    patterns: list[dict],
) -> dict:
    """One GEPA-style record (Inputs / Generated Outputs / Feedback) of one case for one
    component, from train/val data only."""
    expected = {e.expected_id: e for e in case.expected_impacts}
    omissions = {o.omission_id: o for o in case.important_omissions}
    n_runs = max(len(runs), 1)
    missed, missed_om, keys = [], [], set()
    for item_id in sorted({e.item_id for e in events if e.kind == "missed_impact"}):
        if item := expected.get(item_id):
            owners = sorted({e.owner for e in events if e.item_id == item_id})
            hits = len({e.run_id for e in events if e.item_id == item_id})
            missed.append(
                {
                    "affected_actor": item.affected_actor,
                    "mechanism": item.mechanism,
                    "impact": item.impact,
                    "category": item.category or "uncategorised",
                    "provision_keys": item.provision_keys,
                    "owner": owners,
                    "missed_in_runs": f"{hits}/{n_runs}",
                }
            )
            keys |= set(item.provision_keys)
    for item_id in sorted({e.item_id for e in events if e.kind == "missed_omission"}):
        if item := omissions.get(item_id):
            missed_om.append(
                {"description": item.description, "provision_keys": item.provision_keys}
            )
            keys |= set(item.provision_keys)
    agent = _agent_of(component)
    produced: list[dict] = []
    for run in runs[:1]:  # one representative run keeps records short
        if agent is not None:
            produced = [
                {
                    "provision_key": f.provision_key,
                    "affected_actor": f.affected_actor,
                    "mechanism": f.mechanism,
                    "impact": f.impact,
                }
                for f in run.board
                if f.agent == agent
            ]
        elif component.startswith("planner"):
            trace = run.planner
            produced = [
                {
                    "planned_keys": trace.keys if trace else [],
                    "missed_keys_not_planned": sorted(keys - set(trace.keys if trace else [])),
                }
            ]
        elif run.dossier is not None:
            produced = [
                {
                    "impact": i.summary,
                    "provision_keys": sorted({f.provision_key for f in i.findings}),
                }
                for i in run.dossier.impacts
            ]
    unsupported = sorted(
        {
            e.item_id
            for e in events
            if e.kind == "unsupported_finding" and (agent is None or e.owner == agent)
        }
    )
    touched = {(e.kind, e.category, e.owner) for e in events}
    return {
        "Inputs": {
            "case_id": case.case_id,
            "scenario_id": case.scenario_id,
            "split": traj["split"],
        },
        "Generated Outputs": produced,
        "Feedback": {
            "score": traj["score"],
            "coverage": traj["coverage"],
            "grounding": traj["grounding"],
            "missed_expected_impacts": missed,
            "missed_omissions": missed_om,
            "unsupported_findings": unsupported,
            "patterns": [
                {k: p[k] for k in ("kind", "category", "owner", "persistent_misses", "proposals")}
                for p in patterns
                if (p["kind"], p["category"], p["owner"]) in touched
            ],
        },
    }


def _cap(records: list[dict], limit: int) -> list[dict]:
    """Keep whole records, lowest score first, while the JSON fits in ``limit`` characters."""
    out, used = [], 2
    for rec in sorted(records, key=lambda r: r["Feedback"]["score"]):
        size = len(json.dumps(rec, ensure_ascii=False)) + 1
        if used + size > limit:
            break
        out.append(rec)
        used += size
    return out
