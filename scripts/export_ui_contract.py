"""Export JSON Schemas and a sample run for the demo frontend (R35/R36).

Run: uv run python scripts/export_ui_contract.py
Writes docs/ui/schema/*.json and docs/ui/sample_run.json. The sample is illustrative data only.
"""

from __future__ import annotations

import json
from pathlib import Path

from womm.api.evolution import CandidateDetail, CandidateDiff, Lineage
from womm.models.decisions import DecisionRecord
from womm.models.dossier import (
    Disagreement,
    DossierImpact,
    ImpactChain,
    ImpactDossier,
    OpenQuestion,
)
from womm.models.findings import (
    ExpertFailure,
    FindingDraft,
    ImpactFinding,
    Provenance,
)
from womm.models.run import CallUsage, CodeIdentity, GroundingStats, RunResult, RunStatus

OUT = Path(__file__).resolve().parents[1] / "docs" / "ui"
SV = "sv_example00001"
RUN = "run_3f2a9c1e-example"


def finding(
    agent: str, idx: int, key: str, actor: str, impact: str, mech: str, src: str, quote: str
):
    draft = FindingDraft(
        provision_key=key,
        affected_actor=actor,
        impact=impact,
        mechanism=mech,
        evidence=[{"source_id": src, "quote": quote}],
        confidence=0.7,
    )
    prov = Provenance(
        agent=agent,
        system_version=SV,
        prompt_hash="ph_example",
        backend="claude_code",
        model="claude-sonnet-5",
    )
    return ImpactFinding.from_draft(draft, run_id=RUN, index=idx, provenance=prov)


def main() -> None:
    f_legal = finding(
        "legal",
        0,
        "ai_act/high_risk/risk_management",
        "Providers of high-risk AI systems",
        "New obligation to operate a documented risk management system",
        "Article requires a continuous, iterative risk management process",
        "com2021_206/art_9",
        "A risk management system shall be established, implemented, documented and maintained",
    )
    f_fiscal = finding(
        "fiscal",
        0,
        "ai_act/high_risk/risk_management",
        "Providers of high-risk AI systems",
        "Recurring compliance cost for risk management and documentation",
        "Documented, maintained process implies staff time and tooling",
        "com2021_206/art_9",
        "shall be established, implemented, documented and maintained in relation to high-risk AI",
    )
    f_stake = finding(
        "stakeholder",
        0,
        "ai_act/sme/sandboxes",
        "SMEs and start-ups",
        "Priority access to regulatory sandboxes partially offsets compliance burden",
        "Sandbox access lowers cost of testing conformity",
        "com2021_206/art_55",
        "provide small-scale providers and start-ups with priority access to the AI regulatory sandboxes",
    )
    dossier = ImpactDossier(
        run_id=RUN,
        scenario_id="eval_provider_compliance_costs",
        status="degraded",
        system_version=SV,
        impacts=[
            DossierImpact(
                impact_id="I1",
                summary="Providers must run a documented risk management system, creating recurring cost.",
                findings=[f_legal, f_fiscal],
                merged=True,
            ),
            DossierImpact(
                impact_id="I2",
                summary="SMEs get priority sandbox access, partly offsetting the burden.",
                findings=[f_stake],
                merged=True,
            ),
        ],
        chains=[
            ImpactChain(impact_ids=["I1", "I2"], description="Compliance cost -> SME mitigation")
        ],
        disagreements=[
            Disagreement(
                finding_ids=[f_fiscal.finding_id, f_stake.finding_id],
                note="Fiscal sees net burden on SMEs; Stakeholder sees sandboxes as offsetting.",
            )
        ],
        open_questions=[
            OpenQuestion(
                question="Does the conformity assessment cost fall on importers as well?",
                reason="evidence_unresolved",
            )
        ],
        failed_experts=[
            ExpertFailure(
                agent="workforce", error_kind="timeout", message="exceeded 240s", attempts=3
            )
        ],
    )
    decisions = [
        DecisionRecord(
            decision_point="router.relevance",
            subject=agent,
            input_summary="diff: 12 provisions added (high-risk requirements, SME measures)",
            decision=decision,
            probability=p,
            mode="shadow",
            decider="stub",
            system_version=SV,
        )
        for agent, decision, p in [
            ("legal", "relevant", 0.97),
            ("fiscal", "relevant", 0.88),
            ("stakeholder", "not_relevant", 0.35),
        ]
    ]
    run = RunResult(
        run_id=RUN,
        scenario_id=dossier.scenario_id,
        status=RunStatus.degraded,
        system_version=SV,
        code_identity=CodeIdentity(git_sha="b1c007b", dirty=False, claude_cli_version="2.1.283"),
        dossier=dossier,
        board=[f_legal, f_fiscal, f_stake],
        failures=dossier.failed_experts,
        decisions=decisions,
        grounding=GroundingStats(passed=3, total=4),
        usage=[
            CallUsage(
                role="expert",
                agent="legal",
                backend="claude_code",
                model="claude-sonnet-5",
                input_tokens=5200,
                output_tokens=900,
                latency_s=21.4,
            ),
        ],
    )

    schema_dir = OUT / "schema"
    schema_dir.mkdir(parents=True, exist_ok=True)
    for name, model in {
        "run_result": RunResult,
        "impact_dossier": ImpactDossier,
        "impact_finding": ImpactFinding,
        "decision_record": DecisionRecord,
        "evolution_lineage": Lineage,
        "evolution_candidate": CandidateDetail,
        "evolution_diff": CandidateDiff,
    }.items():
        (schema_dir / f"{name}.schema.json").write_text(
            json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n"
        )
    (OUT / "sample_run.json").write_text(run.model_dump_json(indent=2) + "\n")
    print(f"wrote {schema_dir} and {OUT / 'sample_run.json'}")


if __name__ == "__main__":
    main()
