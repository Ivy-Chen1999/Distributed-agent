"""Deterministic Impact Dossier assembly (R8, AE1).

Synthesis only proposes an ID-level structure. This module enforces the invariants:
- unsupported findings always become 'evidence_unresolved' open questions, whatever synthesis says;
- impacts may only reference supported findings (unknown or unsupported IDs are dropped + noted);
- every supported finding ends up somewhere; anything synthesis left out goes to `unprocessed`;
- if synthesis failed, supported findings are listed unmerged and the run is degraded.
"""

from __future__ import annotations

from langgraph.runtime import Runtime

from womm.graph.state import RIAState, WommContext
from womm.graph.synthesis import dispatched_ids
from womm.models.dossier import (
    DiscardedFinding,
    DossierImpact,
    DossierStatus,
    ImpactDossier,
    OpenQuestion,
)
from womm.models.findings import ImpactFinding


def _unresolved_question(f: ImpactFinding) -> OpenQuestion:
    return OpenQuestion(
        question=f"Unverified ({f.agent}): {f.impact} — affects {f.affected_actor}",
        finding_id=f.finding_id,
        reason="evidence_unresolved",
    )


def assemble(state: RIAState, sv_id: str, expert_ids: list[str]) -> ImpactDossier:
    base = {"run_id": state["run_id"], "scenario_id": state["scenario_id"], "system_version": sv_id}
    failures = state.get("failures", {})
    failed_experts = [failures[e] for e in expert_ids if e in failures]

    if fatal := state.get("fatal_error"):
        return ImpactDossier(**base, status="failed", failed_experts=failed_experts, notes=[fatal])
    if state["diff"].is_empty:
        return ImpactDossier(**base, status="no_changes", notes=["no provision changes"])
    if len(failed_experts) == len(expert_ids):
        return ImpactDossier(
            **base, status="failed", failed_experts=failed_experts,
            notes=["all experts failed; synthesis skipped"],
        )  # fmt: skip

    validation = state["validation"]
    supported = {f.finding_id: f for f in validation.supported}
    unsupported = {f.finding_id for f in validation.unsupported}
    open_questions = [_unresolved_question(f) for f in validation.unsupported]
    notes: list[str] = []
    status: DossierStatus = "degraded" if failed_experts else "succeeded"

    plan = state.get("synthesis")
    if plan is None:
        notes.append(f"synthesis unavailable: {state.get('synthesis_error') or 'unknown error'}")
        impacts = [
            DossierImpact(impact_id=f"I{i}", summary=f.impact, findings=[f], merged=False)
            for i, f in enumerate(supported.values(), 1)
        ]
        return ImpactDossier(
            **base, status="degraded", impacts=impacts, open_questions=open_questions,
            failed_experts=failed_experts, notes=notes,
        )  # fmt: skip

    def usable(fid: str, where: str) -> bool:
        if fid in supported:
            return True
        why = "unsupported" if fid in unsupported else "unknown"
        notes.append(f"synthesis referenced {why} finding {fid} in {where}; ignored")
        return False

    accounted: set[str] = set()
    impacts: list[DossierImpact] = []
    seen_labels: set[str] = set()
    for imp in plan.impacts:
        if imp.impact_id in seen_labels:
            notes.append(f"duplicate impact_id {imp.impact_id} from synthesis; later one dropped")
            continue
        seen_labels.add(imp.impact_id)
        ids = [fid for fid in dict.fromkeys(imp.finding_ids) if usable(fid, imp.impact_id)]
        ids = [fid for fid in ids if fid not in accounted]
        if not ids:
            notes.append(f"impact {imp.impact_id} dropped: no usable findings")
            continue
        accounted.update(ids)
        impacts.append(
            DossierImpact(
                impact_id=imp.impact_id,
                summary=imp.summary,
                findings=[supported[fid] for fid in ids],
                merged=True,
            )
        )

    impact_ids = {i.impact_id for i in impacts}
    chains = [c for c in plan.chains if all(i in impact_ids for i in c.impact_ids)]
    disagreements = []
    for d in plan.disagreements:
        ids = [fid for fid in dict.fromkeys(d.finding_ids) if fid in supported]
        if len(ids) >= 2:
            disagreements.append(d.model_copy(update={"finding_ids": ids}))

    for q in plan.open_questions:
        fid = q.finding_id
        if fid is not None and not usable(fid, "open_questions"):
            fid = None
        if fid is not None:
            accounted.add(fid)
        open_questions.append(OpenQuestion(question=q.question, finding_id=fid, reason="synthesis"))

    discarded = []
    for d in plan.discarded:
        if usable(d.finding_id, "discarded") and d.finding_id not in accounted:
            accounted.add(d.finding_id)
            discarded.append(DiscardedFinding(finding=supported[d.finding_id], reason=d.reason))

    unprocessed = [f for fid, f in supported.items() if fid not in accounted]
    if unprocessed:
        notes.append(f"{len(unprocessed)} supported finding(s) not placed by synthesis")

    return ImpactDossier(
        **base,
        status=status,
        impacts=impacts,
        chains=chains,
        disagreements=disagreements,
        open_questions=open_questions,
        discarded=discarded,
        unprocessed=unprocessed,
        failed_experts=failed_experts,
        notes=notes,
    )


async def assemble_node(state: RIAState, runtime: Runtime[WommContext]) -> dict:
    sv = runtime.context.sv
    dossier = assemble(state, sv.version_id, dispatched_ids(state, runtime))
    header = {"law_version": state.get("law_version")}
    if planner_notes := state.get("planner_notes"):
        header["notes"] = [*planner_notes, *dossier.notes]
    return {"dossier": dossier.model_copy(update=header)}
