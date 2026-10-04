"""Strip impact-assessment material from an explanatory memorandum, failing closed (R9, R24).

Golden cases are scored against the proposal's impact assessment (IA), so memorandum text that
restates the IA's findings would leak the answer. Headings differ between proposals ("3.3.
Impact assessment", "•. Impact assessment", "Results of ex-post evaluations, stakeholder
consultations and impact assessments", ...), so sections are removed by **topic pattern** rather
than exact heading, together with their subsections:

- ``ia_results``: ex-post evaluations, stakeholder consultations, collection and use of
  expertise, impact assessment, regulatory fitness (the Better Regulation section 3 and any
  subsection with those topics elsewhere)
- ``fundamental_rights``: the IA's conclusions on fundamental-rights impacts
- ``proportionality``: the proportionality subsection asserts that costs are proportionate,
  i.e. the IA's cost conclusion (the "LEGAL BASIS, SUBSIDIARITY AND PROPORTIONALITY" parent
  heading is not matched, so legal basis and subsidiarity stay)
- ``budget``: budgetary implications quote the IA's public-sector estimates

Then a **leak guard** scans everything kept for IA-conclusion markers ("impact assessment",
"preferred option", "Regulatory Scrutiny Board", "SWD(") and rejects the memorandum, naming
the section, unless the occurrence is an allow-listed citation from the proposal's
``import.yaml``. A proposal whose kept text trips the guard is fixed there, where the exception
stays reviewable, never by loosening the guard:

- an extra strip pattern removes a whole section;
- a **sentence redaction** (``redact_sentences``) removes one sentence that restates or cites the
  IA in an otherwise useful section. Each entry names the section, the exact sentence or a unique
  prefix of it, and a reason; an entry that no longer matches exactly one sentence fails the
  import, so a stale entry cannot pass silently. The reasons, not the sentences, are recorded on
  the memorandum sources (``redactions``), next to ``stripped_sections``;
- an **allow-list** entry masks one exact phrase that is not about this proposal's IA (e.g.
  "environmental impact assessment" of another instrument). An entry must be longer than a bare
  marker and must occur in the kept text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from womm.data.parse_proposal import MemorandumSection
from womm.models.regulation import Source

TOPIC_PATTERNS: dict[str, str] = {
    "ia_results": (
        r"ex-?\s?post evaluation|stakeholder consultation|impact assessment"
        r"|collection and use of expertise|regulatory fitness"
    ),
    "fundamental_rights": r"^fundamental rights\b",
    "proportionality": r"^proportionality\b",
    "budget": r"^budgetary implications?\b",
}
# Topics every Better Regulation memorandum has; a memorandum without them is not recognised.
REQUIRED_TOPICS = ("ia_results", "proportionality", "budget")
LEAK_MARKERS = (
    "impact assessment",
    "preferred option",
    "preferred policy option",
    "regulatory scrutiny board",
    "swd(",
    "staff working document",  # the accompanying SWD cited without its number
    "public consultation",  # consultation results belong to the stripped section 3
    "problem definition",  # IA template term for the problem analysis
)
# Top-level sections of the standard memorandum template -> readable source_id suffix.
TEMPLATE_SLUGS = {
    "context of the proposal": "context",
    "legal basis, subsidiarity and proportionality": "legal_basis",
    "other elements": "other_elements",
}


class MemorandumError(ValueError):
    """The memorandum cannot be stripped safely; nothing from it may be used."""


@dataclass(frozen=True)
class StripResult:
    kept: list[MemorandumSection]
    removed: list[str]  # full headings, in document order
    topics: dict[str, list[str]]  # topic -> full headings it matched


def _topic(heading: str, patterns: Mapping[str, str]) -> str | None:
    for topic, pattern in patterns.items():
        if re.search(pattern, heading.strip(), flags=re.IGNORECASE):
            return topic
    return None


def strip_topics(
    sections: list[MemorandumSection],
    *,
    extra_patterns: Mapping[str, str] | None = None,
    required: Iterable[str] = REQUIRED_TOPICS,
    label: str = "memorandum",
) -> StripResult:
    """Drop every section whose heading matches a topic pattern, with its subsections.

    Fails naming ``label`` and the headings it saw when a required topic matches nothing: an
    unrecognised memorandum must not pass with its IA section intact."""
    patterns = {**TOPIC_PATTERNS, **(extra_patterns or {})}
    kept: list[MemorandumSection] = []
    removed: list[str] = []
    topics: dict[str, list[str]] = {}
    drop_level: int | None = None
    for s in sections:
        if drop_level is not None and s.level > drop_level:
            removed.append(s.full_heading)
            continue
        drop_level = None
        topic = _topic(s.heading, patterns)
        if topic:
            drop_level = s.level
            removed.append(s.full_heading)
            topics.setdefault(topic, []).append(s.full_heading)
            continue
        kept.append(s)
    missing = [t for t in required if t not in topics]
    if missing:
        seen = [s.full_heading for s in sections]
        raise MemorandumError(f"{label}: no memorandum section for {missing}; headings: {seen}")
    return StripResult(kept, removed, topics)


@dataclass(frozen=True)
class Redaction:
    """One sentence to remove from a kept memorandum section (from ``import.yaml``)."""

    section: str  # the section's full heading ("1.3. Consistency ...") or its number ("1.3.")
    sentence: str  # the exact sentence, or a prefix of it that starts exactly one sentence
    reason: str


@dataclass(frozen=True)
class RedactResult:
    kept: list[MemorandumSection]
    records: dict[str, list[str]]  # full heading -> reasons, in entry order


# A sentence ends at ., ! or ? (plus closing quotes/brackets) followed by whitespace and an
# opening capital, quote or bracket; or at the end of its block.
_SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)\]]*\s+(?=[A-Z\u201c\u2018\"'(])")


def _norm(text: str) -> str:
    return " ".join(text.split())


def _sentence_starts(text: str) -> set[int]:
    return {0, *(m.end() for m in _SENTENCE_END.finditer(text))}


def redact_sentences(
    sections: list[MemorandumSection],
    redactions: Iterable[Redaction],
    *,
    label: str = "memorandum",
) -> RedactResult:
    """Remove each listed sentence from its kept section, failing closed.

    An entry fails, naming ``label``, when its section is not among ``sections`` (e.g. it was
    stripped), when its text starts no sentence there, or when it starts more than one. The
    removed sentence runs from the match to the next sentence end at or after the entry's end.
    Reasons are recorded on the sources, so a reason containing a leak marker fails too."""
    kept = [replace(s, blocks=list(s.blocks)) for s in sections]
    records: dict[str, list[str]] = {}
    errors: list[str] = []
    for r in redactions:
        wanted = _norm(r.section).casefold()
        targets = [
            s
            for s in kept
            if wanted in (_norm(s.full_heading).casefold(), _norm(s.number).casefold())
        ]
        if len(targets) != 1:
            errors.append(f"section {r.section!r} matches {len(targets)} kept sections")
            continue
        (target,) = targets
        needle = _norm(r.sentence)
        if not needle or not r.reason.strip():
            errors.append(f"{target.full_heading!r}: empty sentence or reason")
            continue
        if any(m in r.reason.casefold() for m in LEAK_MARKERS):
            # The reason is copied into the agent-visible source; it must not leak either.
            errors.append(f"{target.full_heading!r}: reason {r.reason!r} contains an IA marker")
            continue
        blocks = [_norm(b) for b in target.blocks]
        hits = [
            (i, pos)
            for i, block in enumerate(blocks)
            for pos in _sentence_starts(block)
            if block.startswith(needle, pos)
        ]
        if len(hits) != 1:
            what = "not found" if not hits else f"starts {len(hits)} sentences"
            errors.append(f"{target.full_heading!r}: sentence {needle[:60]!r}... {what}")
            continue
        i, pos = hits[0]
        block = blocks[i]
        end = next(
            (m.end() for m in _SENTENCE_END.finditer(block) if m.end() > pos + len(needle)),
            len(block),
        )
        target.blocks[i] = (block[:pos] + block[end:]).strip()
        target.blocks[:] = [b for b in target.blocks if b]
        records.setdefault(target.full_heading, []).append(r.reason.strip())
    if errors:
        raise MemorandumError(f"{label}: redaction failed:\n  " + "\n  ".join(errors))
    return RedactResult(kept, records)


def leak_guard(
    sections: list[MemorandumSection], *, allow: Iterable[str] = (), label: str = "memorandum"
) -> None:
    """Raise naming the section and marker when kept text still carries IA conclusions.

    ``allow`` holds exact phrases (e.g. "environmental impact assessment") that are masked
    first. A phrase that is no longer than a bare marker, or that occurs nowhere, fails: the
    allow-list cannot loosen the guard globally or keep a stale entry."""
    allowed = [_norm(a).casefold() for a in allow]
    bad = [a for a in allowed if not a or any(a in m for m in LEAK_MARKERS)]
    if bad:
        raise MemorandumError(f"{label}: allow-list entries {bad} would mask a bare marker")
    texts = [_norm(t).casefold() for s in sections for t in (s.full_heading, *s.blocks)]
    unused = [a for a in allowed if not any(a in t for t in texts)]
    if unused:
        raise MemorandumError(f"{label}: allow-list entries {unused} occur nowhere (stale)")
    hits: list[str] = []
    for s in sections:
        for text in (s.full_heading, *s.blocks):
            folded = _norm(text).casefold()
            for phrase in allowed:
                folded = folded.replace(phrase, " ")
            for marker in LEAK_MARKERS:
                for m in re.finditer(re.escape(marker), folded):
                    context = folded[max(0, m.start() - 60) : m.end() + 60]
                    hits.append(f"{s.full_heading!r} contains {marker!r}: ...{context}...")
    if hits:
        raise MemorandumError(
            f"{label}: {len(hits)} IA marker(s) in kept memorandum sections:\n  "
            + "\n  ".join(hits)
        )


def memorandum_sources(
    kept: list[MemorandumSection],
    removed: list[str],
    *,
    version_id: str,
    label: str,
    redactions: Mapping[str, list[str]] | None = None,
) -> list[Source]:
    """One source per kept top-level section; each records the full strip list and the
    reasons for the sentences redacted from its own sections ("<heading>: <reason>")."""
    redactions = redactions or {}
    groups: list[list[MemorandumSection]] = []
    for s in kept:
        if s.level == 1 or not groups:
            groups.append([])
        groups[-1].append(s)

    sources = []
    for group in groups:
        top = group[0]
        lines = []
        for s in group:
            lines.append(s.full_heading)
            lines.extend(s.blocks)
        number = top.number.rstrip(".")
        slug = TEMPLATE_SLUGS.get(top.heading.casefold(), f"section_{number}")
        sources.append(
            Source(
                source_id=f"{version_id}/memorandum/{slug}",
                title=(f"{label}, Explanatory memorandum, {top.number} {top.heading.capitalize()}"),
                kind="memorandum",
                text="\n\n".join(lines),
                stripped_sections=removed,
                redactions=[
                    f"{s.full_heading}: {reason}"
                    for s in group
                    for reason in redactions.get(s.full_heading, [])
                ],
            )
        )
    ids = [s.source_id for s in sources]
    if len(set(ids)) != len(ids):
        raise MemorandumError(f"{label}: duplicate memorandum source ids {ids}")
    return sources
