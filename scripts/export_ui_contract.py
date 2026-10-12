"""Export the JSON Schemas of the console's API for the demo frontend (R35/R36).

    uv run python scripts/export_ui_contract.py            # write docs/ui/schema/*.json
    uv run python scripts/export_ui_contract.py --check    # fail if they are out of date
    uv run python scripts/export_ui_contract.py --sample-out <file.json>

``docs/ui/sample_run.json`` is a real run kept as committed; ``--sample-out`` writes a small
illustrative run elsewhere and never overwrites it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from womm.api.evolution import CandidateDetail, CandidateDiff, Lineage
from womm.models.cost import CostSection
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


SCHEMAS = {
    "run_result": RunResult,
    "impact_dossier": ImpactDossier,
    "impact_finding": ImpactFinding,
    "decision_record": DecisionRecord,
    "evolution_lineage": Lineage,
    "evolution_candidate": CandidateDetail,
    "evolution_diff": CandidateDiff,
    "cost_section": CostSection,
}


def schema_texts() -> dict[str, str]:
    """File name -> the schema as it is written."""
    return {
        f"{name}.schema.json": json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False)
        + "\n"
        for name, model in SCHEMAS.items()
    }


def stale(schema_dir: Path) -> list[str]:
    """Schema files that differ from the models (missing ones included)."""
    out = []
    for name, text in schema_texts().items():
        path = schema_dir / name
        if not path.exists() or path.read_text(encoding="utf-8") != text:
            out.append(name)
    return out


def illustrative_run() -> RunResult:
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

    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only compare; exit 1 when stale")
    parser.add_argument("--sample-out", type=Path, help="write an illustrative run here")
    parser.add_argument("--out", type=Path, default=OUT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    schema_dir = args.out / "schema"
    if args.check:
        bad = stale(schema_dir)
        print(f"out of date: {', '.join(bad)}" if bad else "schemas up to date")
        return 1 if bad else 0
    schema_dir.mkdir(parents=True, exist_ok=True)
    for name, text in schema_texts().items():
        (schema_dir / name).write_text(text, encoding="utf-8")
    print(f"wrote {schema_dir}")
    if args.sample_out:
        if args.sample_out.resolve() == (OUT / "sample_run.json").resolve():
            print("refusing to overwrite the committed real sample run", file=sys.stderr)
            return 2
        args.sample_out.write_text(illustrative_run().model_dump_json(indent=2) + "\n")
        print(f"wrote {args.sample_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
