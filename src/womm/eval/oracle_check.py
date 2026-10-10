"""Oracle ("full-score") check: every golden case must be passable by its own reference answer.

Following Anthropic's eval guidance, each task gets a reference solution that passes all graders,
so a task or grader that cannot be passed is found before it scores the system. Here the
reference solution is a dossier built from the case itself: one dossier impact per expected
impact (its affected_actor, mechanism and impact) and one per important omission (its
description). That dossier is scored with the real coverage judge (``score_case``). An expected
impact judged not covered, or an omission judged not addressed, points at a broken item or a
miscalibrated judge, not at the system. A case passes only at coverage 1.0 and omissions 1.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from womm.eval.drafting import GoldenDraft
from womm.eval.evaluators import CaseScore, score_case
from womm.eval.golden import (
    HOLDOUT_REFUSAL,
    ExpectedImpact,
    GoldenCase,
    GoldenError,
    Omission,
)
from womm.eval.golden_review import DROPPED_DECISIONS
from womm.llm.base import LLMBackend
from womm.models.dossier import DossierImpact, ImpactDossier
from womm.models.findings import ImpactFinding, Provenance
from womm.models.run import CodeIdentity, RunResult, RunStatus
from womm.models.system_version import RoleConfig

ORACLE = "oracle"
# Candidates enter a published case only when accepted; pending ones are not part of the answer.
ACCEPTED_CANDIDATE = ("verified", "edited", "llm_kept")


@dataclass
class OracleResult:
    case_id: str
    coverage: float | None
    omissions: float | None
    uncovered: dict[str, str] = field(default_factory=dict)
    unaddressed: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def passed(self) -> bool:
        # A case without important omissions has nothing to address: omissions stays None.
        return (
            self.error is None
            and self.coverage == 1.0
            and (self.omissions is None or self.omissions == 1.0)
        )


def _finding(n: int, item: ExpectedImpact) -> ImpactFinding:
    """The reference answer as one finding. No evidence: grounding is not what is checked."""
    return ImpactFinding(
        finding_id=f"oracle_f{n}",
        agent=ORACLE,
        provision_key=item.provision_keys[0],
        affected_actor=item.affected_actor,
        mechanism=item.mechanism,
        impact=item.impact,
        evidence=[],
        confidence=1.0,
        provenance=Provenance(
            agent=ORACLE, system_version=ORACLE, prompt_hash="", backend=ORACLE, model=ORACLE
        ),
    )


def oracle_run(case: GoldenCase) -> RunResult:
    """A succeeded run whose dossier is the case's own reference answer.

    Impact ids are neutral (I1, I2, ...), never the expected or omission ids, so the judge has
    to match on content."""
    impacts = [
        DossierImpact(impact_id=f"I{n}", summary=e.impact, findings=[_finding(n, e)], merged=True)
        for n, e in enumerate(case.expected_impacts, start=1)
    ]
    start = len(impacts) + 1
    impacts += [
        DossierImpact(impact_id=f"I{n}", summary=o.description, findings=[], merged=True)
        for n, o in enumerate(case.important_omissions, start=start)
    ]
    run_id = f"{ORACLE}_{case.case_id}"
    dossier = ImpactDossier(
        run_id=run_id,
        scenario_id=case.scenario_id,
        status="succeeded",
        system_version=ORACLE,
        impacts=impacts,
    )
    return RunResult(
        run_id=run_id,
        scenario_id=case.scenario_id,
        status=RunStatus.succeeded,
        system_version=ORACLE,
        code_identity=CodeIdentity(git_sha=None, dirty=False),
        dossier=dossier,
    )


def oracle_result(score: CaseScore) -> OracleResult:
    """Coverage, omissions and the failed ids with the judge's justification."""
    error = score.error or score.judge_error
    if score.outcome == "errored" and not error:
        error = "errored"
    out = OracleResult(
        case_id=score.case_id,
        coverage=score.coverage,
        omissions=score.omissions_addressed,
        error=error,
    )
    if score.judge:
        out.uncovered = {
            v.expected_id: v.justification for v in score.judge.expected if not v.covered
        }
        out.unaddressed = {
            v.omission_id: v.justification for v in score.judge.omissions if not v.addressed
        }
    return out


async def check_case(
    case: GoldenCase, judge_backend: LLMBackend, judge_role: RoleConfig, judge_prompt: str
) -> OracleResult:
    """Score the case's own reference answer with the coverage judge. Holdout is refused."""
    if case.split == "holdout":
        raise GoldenError(f"{case.case_id}: {HOLDOUT_REFUSAL}")
    score, _ = await score_case(case, oracle_run(case), judge_backend, judge_role, judge_prompt)
    return oracle_result(score)


def case_from_draft(draft: GoldenDraft) -> GoldenCase:
    """The case a draft would become, for checking before review is finished: every impact and
    omission not dropped (kept or still pending) and every accepted candidate."""
    if draft.split == "holdout":
        raise GoldenError(f"{draft.case_id}: {HOLDOUT_REFUSAL}")
    items = [i for i in draft.expected_impacts if i.review.decision not in DROPPED_DECISIONS]
    items += [c for c in draft.possibly_missing if c.review.decision in ACCEPTED_CANDIDATE]
    impacts = [
        ExpectedImpact(
            expected_id=i.item_id,
            affected_actor=i.affected_actor,
            mechanism=i.mechanism,
            impact=i.impact,
            provision_keys=list(i.provision_keys),
            ia_section=i.ia_section,
        )
        for i in items
    ]
    omissions = [
        Omission(
            omission_id=o.omission_id,
            description=o.description,
            provision_keys=list(o.provision_keys),
            source=o.ia_section,
        )
        for o in draft.important_omissions
        if o.review.decision not in DROPPED_DECISIONS
    ]
    if not impacts:
        raise GoldenError(f"{draft.case_id}: no expected impacts left to check")
    return GoldenCase(
        case_id=draft.case_id,
        scenario_id=draft.scenario_id,
        ia_reference=draft.ia_reference,
        fixture=draft.fixture,
        split=draft.split,
        expected_impacts=impacts,
        important_omissions=omissions,
    )
