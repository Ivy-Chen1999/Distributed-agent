"""Turn LangGraph task stream chunks into small, UI-friendly RunEvents (R35)."""

from __future__ import annotations

from typing import Any

from womm.models.run import RunEvent


def _payload(node: str, result: dict[str, Any] | None) -> dict[str, Any]:
    """Summaries only: counts, statuses and decisions, never full texts."""
    if not result:
        return {}
    out: dict[str, Any] = {}
    if board := result.get("board"):
        out["findings"] = {agent: len(fs) for agent, fs in board.items()}
    if failures := result.get("failures"):
        out["failures"] = {agent: f.error_kind for agent, f in failures.items() if f is not None}
    if decisions := result.get("decisions"):
        out["decisions"] = [
            {"subject": d.subject, "decision": d.decision, "probability": d.probability,
             "mode": d.mode, "decider": d.decider}
            for d in decisions
        ]  # fmt: skip
    if dispatched := result.get("dispatched"):
        out["dispatched"] = dispatched
    if focus := result.get("focus"):
        out["focus_areas"] = len(focus.focus_areas)
    if validation := result.get("validation"):
        g = validation.report.grounding
        out["grounding"] = {"passed": g.passed, "total": g.total}
        out["supported"] = len(validation.supported)
        out["unsupported"] = len(validation.unsupported)
    if (err := result.get("fatal_error")) or (err := result.get("synthesis_error")):
        out["error"] = err[:300]
    if dossier := result.get("dossier"):
        out["status"] = dossier.status
        out["impacts"] = len(dossier.impacts)
    return out


def task_event(run_id: str, seq: int, chunk: dict[str, Any]) -> RunEvent:
    node = chunk.get("name", "?")
    if "input" in chunk:
        return RunEvent(run_id=run_id, seq=seq, node=node, event="started")
    if chunk.get("error"):
        return RunEvent(
            run_id=run_id, seq=seq, node=node, event="failed",
            payload={"error": str(chunk["error"])[:300]},
        )  # fmt: skip
    result = chunk.get("result")
    return RunEvent(
        run_id=run_id,
        seq=seq,
        node=node,
        event="finished",
        payload=_payload(node, result if isinstance(result, dict) else None),
    )
