"""Deterministic citation validation (R9): does each quoted evidence item exist in its source?

This measures quote existence, not whether the quote supports the claim (R12).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel

from womm.models.findings import ImpactFinding
from womm.models.regulation import Source
from womm.models.run import GroundingStats

MIN_TOTAL_WORDS = 6
MIN_SEGMENT_WORDS = 4

Reason = Literal[
    "ok", "unknown_source", "not_found", "too_short", "too_fragmented", "provision_not_in_diff"
]

_QUOTES = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
        "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
        "―": "-", "−": "-",
        "­": None,  # soft hyphen
    }
)  # fmt: skip
_LINEBREAK_HYPHEN = re.compile(r"(\w)-[ \t]*\n\s*(\w)")
_WS = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"\[?\s*\.{3,}\s*\]?")
_EDGE_PUNCT = " \t.,;:!?\"'()[]"


def normalize(text: str) -> str:
    """NFKC, casefold, unify quotes/dashes, join line-break hyphenation, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    text = _LINEBREAK_HYPHEN.sub(r"\1\2", text)
    return _WS.sub(" ", text).casefold().strip()


def match_quote(quote: str, source_text: str) -> Reason:
    """Check one quote against one source. NFKC turns '…' into '...', so one split handles both."""
    segments = [s.strip(_EDGE_PUNCT) for s in _ELLIPSIS.split(normalize(quote))]
    segments = [s for s in segments if s]
    counts = [len(s.split()) for s in segments]
    if sum(counts) < MIN_TOTAL_WORDS:
        return "too_short"
    if len(segments) > 1 and min(counts) < MIN_SEGMENT_WORDS:
        return "too_fragmented"
    haystack = normalize(source_text)
    pos = 0
    for seg in segments:
        found = haystack.find(seg, pos)
        if found < 0:
            return "not_found"
        pos = found + len(seg)
    return "ok"


class EvidenceVerdict(BaseModel):
    finding_id: str
    evidence_id: str
    source_id: str
    reason: Reason

    @property
    def passed(self) -> bool:
        return self.reason == "ok"


class CitationReport(BaseModel):
    verdicts: list[EvidenceVerdict]
    grounding: GroundingStats


class ValidationOutcome(BaseModel):
    supported: list[ImpactFinding]
    """Findings with at least one passing evidence item; failing items are removed."""
    unsupported: list[ImpactFinding]
    """Findings with no passing evidence (incl. empty evidence): open questions (AE1)."""
    report: CitationReport


def validate_findings(
    findings: list[ImpactFinding], sources: dict[str, Source], diff_keys: set[str]
) -> ValidationOutcome:
    verdicts: list[EvidenceVerdict] = []
    supported: list[ImpactFinding] = []
    unsupported: list[ImpactFinding] = []

    for f in findings:
        kept = []
        for ev in f.evidence:
            if f.provision_key not in diff_keys:
                reason: Reason = "provision_not_in_diff"
            elif ev.source_id not in sources:
                reason = "unknown_source"
            else:
                reason = match_quote(ev.quote, sources[ev.source_id].text)
            verdicts.append(
                EvidenceVerdict(
                    finding_id=f.finding_id,
                    evidence_id=ev.evidence_id,
                    source_id=ev.source_id,
                    reason=reason,
                )
            )
            if reason == "ok":
                kept.append(ev)
        if kept:
            supported.append(f.model_copy(update={"evidence": kept}))
        else:
            unsupported.append(f)

    passed = sum(v.passed for v in verdicts)
    return ValidationOutcome(
        supported=supported,
        unsupported=unsupported,
        report=CitationReport(
            verdicts=verdicts, grounding=GroundingStats(passed=passed, total=len(verdicts))
        ),
    )
