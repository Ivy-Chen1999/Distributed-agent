import pytest

from womm.citations import AgentSources, match_quote, normalize, validate_findings
from womm.models.findings import FindingDraft, ImpactFinding, Provenance
from womm.models.regulation import Source

SRC = (
    "1. A risk management system shall be established, implemented, documented and "
    "maintained in relation to high-risk AI systems.\n\n"
    "2. The risk management system shall consist of a continuous iterative process run "
    "throughout the entire lifecycle of a high-risk AI system, requiring regular systematic "
    "updating. It shall comprise the identification and analysis of the known and foreseeable "
    "risks and the obli-\ngations of providers."
)


def test_exact_quote():
    assert match_quote("A risk management system shall be established, implemented", SRC) == "ok"


def test_curly_quotes_and_dashes():
    src = "the so-called “serious incident” shall be reported without undue delay"
    assert match_quote('the so‑called "serious incident" shall be reported', src) == "ok"


def test_linebreak_hyphenation_joined():
    assert match_quote("foreseeable risks and the obligations of providers", SRC) == "ok"


def test_quote_spanning_paragraphs():
    q = "in relation to high-risk AI systems. 2. The risk management system shall consist"
    assert match_quote(q, SRC) == "ok"


def test_ellipsis_segments_in_order():
    q = "A risk management system shall be established … shall consist of a continuous"
    assert match_quote(q, SRC) == "ok"
    q_rev = "shall consist of a continuous ... A risk management system shall be established"
    assert match_quote(q_rev, SRC) == "not_found"


def test_bracketed_ellipsis():
    q = "A risk management system shall be [...] maintained in relation to high-risk"
    assert match_quote(q, SRC) == "ok"


def test_fragmented_quote_rejected():
    q = "the … provider … shall … system … risk … management"
    assert match_quote(q, SRC) == "too_fragmented"


def test_too_short_quote():
    assert match_quote("the Commission shall", SRC) == "too_short"


def test_case_and_whitespace_insensitive():
    assert match_quote("A RISK   MANAGEMENT system SHALL be established", SRC) == "ok"


def test_normalize_idempotent():
    s = normalize(SRC)
    assert normalize(s) == s


PROV = Provenance(agent="legal", system_version="sv", prompt_hash="p", backend="fake", model="m")


def _finding(idx: int, key: str, *evidence: tuple[str, str]) -> ImpactFinding:
    draft = FindingDraft(
        provision_key=key,
        affected_actor="providers",
        impact="i",
        mechanism="m",
        evidence=[{"source_id": s, "quote": q} for s, q in evidence],
        confidence=0.5,
    )
    return ImpactFinding.from_draft(draft, run_id="r", index=idx, provenance=PROV)


SOURCES = {
    "art9": Source(source_id="art9", title="Art 9", kind="provision", text=SRC),
    "art10": Source(source_id="art10", title="Art 10", kind="provision", text="Data governance."),
}
GOOD = "A risk management system shall be established, implemented"


def test_unknown_source_and_wrong_source():
    f_unknown = _finding(0, "k9", ("nope", GOOD))
    f_wrong = _finding(1, "k9", ("art10", GOOD))
    out = validate_findings([f_unknown, f_wrong], SOURCES, {"k9"})
    reasons = [v.reason for v in out.report.verdicts]
    assert reasons == ["unknown_source", "not_found"]
    assert len(out.unsupported) == 2


def test_partial_evidence_kept_and_grounding_counts_all_items():
    """Covers AE1 partially: one passing item keeps the finding supported."""
    f = _finding(0, "k9", ("art9", GOOD), ("art9", "this text is definitely not in the source"))
    out = validate_findings([f], SOURCES, {"k9"})
    (s,) = out.supported
    assert [e.quote for e in s.evidence] == [GOOD]
    assert out.report.grounding.passed == 1
    assert out.report.grounding.total == 2
    assert out.report.grounding.rate == pytest.approx(0.5)


def test_all_evidence_failing_is_unsupported():
    """Covers AE1: no passing evidence -> unsupported (becomes an open question)."""
    f = _finding(0, "k9", ("art9", "entirely fabricated sentence about compliance costs here"))
    out = validate_findings([f], SOURCES, {"k9"})
    assert out.supported == []
    assert [u.finding_id for u in out.unsupported] == [f.finding_id]


def test_empty_evidence_is_unsupported():
    f = _finding(0, "k9")
    out = validate_findings([f], SOURCES, {"k9"})
    assert out.unsupported and out.report.grounding.total == 0
    assert out.report.grounding.rate is None


def test_provision_not_in_diff():
    f = _finding(0, "other", ("art9", GOOD))
    out = validate_findings([f], SOURCES, {"k9"})
    assert out.report.verdicts[0].reason == "provision_not_in_diff"
    assert out.unsupported


# ---------- per-agent validation (R11) ----------


def test_per_agent_checks_the_finding_agents_own_sources():
    """`legal` retrieved only art10: a quote from art9 is unknown_source for it, even though
    art9 is in the run's shared sources."""
    f = _finding(0, "k9", ("art9", GOOD))
    views = {"legal": AgentSources(frozenset({"k9"}), {"art10": SOURCES["art10"]})}
    out = validate_findings([f], SOURCES, {"k9"}, views)
    assert [v.reason for v in out.report.verdicts] == ["unknown_source"]
    views = {"legal": AgentSources(frozenset({"k9"}), {"art9": SOURCES["art9"]})}
    assert validate_findings([f], {}, {"k9"}, views).report.verdicts[0].reason == "ok"


def test_per_agent_provision_out_of_scope_beats_a_citable_source():
    f = _finding(0, "k9", ("art9", GOOD))
    views = {"legal": AgentSources(frozenset({"k10"}), {"art9": SOURCES["art9"]})}
    out = validate_findings([f], SOURCES, {"k9", "k10"}, views)
    assert [v.reason for v in out.report.verdicts] == ["provision_out_of_scope"]
    assert out.unsupported


def test_per_agent_missing_agent_may_cite_nothing():
    f = _finding(0, "k9", ("art9", GOOD))
    out = validate_findings([f], SOURCES, {"k9"}, {})
    assert [v.reason for v in out.report.verdicts] == ["provision_out_of_scope"]
    # The diff check still comes first.
    g = _finding(1, "other", ("art9", GOOD))
    assert validate_findings([g], SOURCES, {"k9"}, {}).report.verdicts[0].reason == (
        "provision_not_in_diff"
    )
