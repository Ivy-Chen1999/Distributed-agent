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
"preferred option", "Regulatory Scrutiny Board", "SWD (", "IA", ...) and rejects the memorandum,
naming the section, unless the occurrence is an allow-listed phrase from the proposal's
``import.yaml``. Text is normalised first (soft hyphens deleted, hyphens and dashes read as
spaces, whitespace collapsed, case folded), so "impact-assessment" or "SWD (2022)" match too.
A proposal whose kept text trips the guard is fixed there, where the exception stays
reviewable, never by loosening the guard:

- an extra strip pattern removes a whole section;
- a **sentence redaction** (``redact_sentences``) removes one sentence that restates or cites the
  IA in an otherwise useful section. Each entry names the section, the exact sentence or a unique
  prefix of it, and a reason; an entry that no longer matches exactly one sentence fails the
  import, so a stale entry cannot pass silently. Sentences end at ``.``, ``!`` or ``?`` but not
  after an abbreviation such as "e.g.", "i.e.", "cf.", "Art." or "No.". The memorandum sources
  record only how many sentences were redacted in which section (``redactions``); neither the
  sentence nor the reason reaches an agent-visible file, because a reason can restate the IA;
- an **allow-list** entry masks one phrase that is not about this proposal's IA (e.g.
  "environmental impact assessment" of another instrument). It names its kept section, must
  occur there exactly once, must contain a marker and must carry a qualifying word beyond it:
  "the impact assessment" or "IAs" would mask the marker itself and are refused.

A second, **quantitative guard** (``quant_guard``) catches IA findings restated without any IA
marker ("Nine out of ten platforms ..."): every kept sentence with an estimate ("estimate",
"estimated"), a percentage, a EUR or euro amount, or an "N out of ten" / "one in five" ratio
needs a manual decision at import. It must be either redacted (``redact_sentences``) or listed
in the proposal's ``quant_allow`` (section, sentence or unique prefix, reason), e.g. a fine of
"6% of total worldwide annual turnover" that the proposal itself sets. An allow entry that
matches no flagged sentence fails, so a stale entry cannot pass silently. Reasons stay in
``import.yaml``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sized
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
# Regexes over normalised text (``normalise``: casefolded, dashes as spaces, one space).
LEAK_MARKERS = (
    r"impact assessment",
    r"impact analysis",  # an IA conclusion under another name
    r"\bias?\b",  # "the IA", "IAs"; false positives ("Annex IA") need a scoped allow entry
    r"preferred option",
    r"preferred policy option",
    r"regulatory scrutiny board",
    r"\bswd\s*\(",
    r"\bsec\s*\(\s*\d{4}\s*\)",  # the RSB opinion's SEC reference
    r"staff working document",  # the accompanying SWD cited without its number
    r"public consultation",  # consultation results belong to the stripped section 3
    r"problem definition",  # IA template term for the problem analysis
)
_MARKERS = [re.compile(m) for m in LEAK_MARKERS]
_NUM_WORD = (
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|fifty|hundred"
    r"|a hundred|a thousand|thousand)"
)
# Quantitative markers over ``normalise``d text: each flags its sentence for a manual decision.
QUANT_MARKERS = (
    r"\bestimat(?:e|es|ed)\b",
    r"\d(?:[\d.,]*\d)?\s*(?:%|per\s?cent\b|percent\b|percentage points?\b)",
    r"(?:\beur\b|€)\s*\d",
    r"\d(?:[\d.,]*\d)?\s*(?:(?:bn|m|mn|million|billion|thousand|trillion)\s+)?(?:\beur\b|€|euros?\b)",
    rf"\b(?:\d+|{_NUM_WORD})\s+out\s+of\s+(?:every\s+)?(?:\d+|{_NUM_WORD})\b",
    rf"\b(?:\d+|{_NUM_WORD})\s+in\s+(?:every\s+)?{_NUM_WORD}\b",
)
_QUANT = [re.compile(m) for m in QUANT_MARKERS]
# Words that cannot make an allow entry specific: what is left of "the impact assessment" or
# "impact assessments" once the marker is removed.
_FUNCTION_WORDS = frozenset(
    {
        *("a", "an", "the", "this", "that", "these", "those", "its", "it", "their", "our"),
        *("his", "her", "such", "any", "each", "every", "some", "all", "no", "own", "said"),
        *("same", "s", "of", "in", "on", "at", "to", "for", "by", "with", "from", "as", "and"),
        "or",
    }
)
_DASHES = re.compile(r"[\-\u2010-\u2015\u2212\u2043\ufe58\ufe63\uff0d]")
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
    records: dict[str, list[str]]  # full heading -> reasons, in entry order (import log only)


@dataclass(frozen=True)
class Allow:
    """One phrase the leak guard ignores in one kept section (from ``import.yaml``)."""

    section: str  # the section's full heading or its number, as for ``Redaction``
    text: str  # the exact phrase; must occur exactly once in that section


# A sentence ends at ., ! or ? (plus closing quotes/brackets) followed by whitespace and an
# opening capital, quote or bracket; or at the end of its block. A period that closes an
# abbreviation does not end a sentence.
_SENTENCE_END = re.compile(r"[.!?][\"'\u201d\u2019)\]]*\s+(?=[A-Z\u201c\u2018\"'(])")
ABBREVIATIONS = frozenset(
    {
        *("e.g.", "i.e.", "cf.", "art.", "arts.", "no.", "nos.", "para.", "paras.", "p."),
        *("pp.", "vol.", "ch.", "sec.", "fig.", "ibid.", "op.", "mr.", "mrs.", "ms.", "dr."),
        *("st.", "vs.", "approx."),
    }
)


def _norm(text: str) -> str:
    return " ".join(text.split())


def normalise(text: str) -> str:
    """Text as the leak guard reads it: soft hyphens deleted, hyphens and dashes as spaces,
    whitespace collapsed to one space, case folded."""
    return _norm(_DASHES.sub(" ", text.replace("\u00ad", ""))).casefold()


def marker_hits(normalised: str) -> list[re.Match[str]]:
    """Every leak-marker match in already ``normalise``d text."""
    return [m for pattern in _MARKERS for m in pattern.finditer(normalised)]


def _sentence_ends(text: str) -> list[int]:
    ends = []
    for m in _SENTENCE_END.finditer(text):
        if text[m.start()] == ".":
            word = text[: m.start() + 1].rsplit(None, 1)[-1].lstrip("(\"'\u201c\u2018[")
            if word.casefold() in ABBREVIATIONS:
                continue
        ends.append(m.end())
    return ends


def sentence_starts(text: str) -> set[int]:
    """Offsets where a sentence starts in ``text`` (abbreviation-aware)."""
    return {0, *_sentence_ends(text)}


def _find_section(
    sections: list[MemorandumSection], wanted: str
) -> tuple[MemorandumSection | None, int]:
    key = _norm(wanted).casefold()
    targets = [
        s for s in sections if key in (_norm(s.full_heading).casefold(), _norm(s.number).casefold())
    ]
    return (targets[0] if len(targets) == 1 else None), len(targets)


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
    Reasons are returned for the import log only; they never reach a source."""
    kept = [replace(s, blocks=list(s.blocks)) for s in sections]
    records: dict[str, list[str]] = {}
    errors: list[str] = []
    for r in redactions:
        target, count = _find_section(kept, r.section)
        if target is None:
            errors.append(f"section {r.section!r} matches {count} kept sections")
            continue
        needle = _norm(r.sentence)
        if not needle or not r.reason.strip():
            errors.append(f"{target.full_heading!r}: empty sentence or reason")
            continue
        blocks = [_norm(b) for b in target.blocks]
        hits = [
            (i, pos)
            for i, block in enumerate(blocks)
            for pos in sentence_starts(block)
            if block.startswith(needle, pos)
        ]
        if len(hits) != 1:
            what = "not found" if not hits else f"starts {len(hits)} sentences"
            errors.append(f"{target.full_heading!r}: sentence {needle[:60]!r}... {what}")
            continue
        i, pos = hits[0]
        block = blocks[i]
        end = next((e for e in _sentence_ends(block) if e > pos + len(needle)), len(block))
        target.blocks[i] = (block[:pos] + block[end:]).strip()
        target.blocks[:] = [b for b in target.blocks if b]
        records.setdefault(target.full_heading, []).append(r.reason.strip())
    if errors:
        raise MemorandumError(f"{label}: redaction failed:\n  " + "\n  ".join(errors))
    return RedactResult(kept, records)


def _allow_error(text: str) -> str | None:
    """Why an allow entry is too broad, or None. It must contain a marker and keep a
    qualifying word once every marker is removed (not just articles, determiners or 's')."""
    norm = normalise(text)
    if not marker_hits(norm):
        return "contains no IA marker"
    rest = norm
    for pattern in _MARKERS:
        rest = pattern.sub(" ", rest)
    words = re.findall(r"[^\W_]+", rest)
    if not [w for w in words if w not in _FUNCTION_WORDS]:
        return "has no qualifying word beyond the marker"
    return None


def leak_hits(text: str) -> list[str]:
    """Context snippets of every leak marker in ``text`` (normalised first)."""
    norm = normalise(text)
    return [
        f"{m.re.pattern!r}: ...{norm[max(0, m.start() - 60) : m.end() + 60]}..."
        for m in marker_hits(norm)
    ]


def leak_check_text(text: str, *, what: str, label: str = "memorandum") -> None:
    """Raise when free text written into a public file (e.g. a scenario description) carries an
    IA marker. No allow-list: such text is ours and can simply be rephrased."""
    hits = leak_hits(text)
    if hits:
        raise MemorandumError(f"{label}: {what}: {len(hits)} IA marker(s):\n  " + "\n  ".join(hits))


def leak_guard(
    sections: list[MemorandumSection], *, allow: Iterable[Allow] = (), label: str = "memorandum"
) -> None:
    """Raise naming the section and marker when kept text still carries IA conclusions.

    Each ``allow`` entry masks its phrase once, in its own section only. An entry fails when its
    section is not kept, when the phrase occurs there other than exactly once, or when it is
    too broad (``_allow_error``): the allow-list cannot loosen the guard or keep a stale entry."""
    masks: dict[int, list[str]] = {}
    errors: list[str] = []
    for a in allow:
        why = _allow_error(a.text)
        if why:
            errors.append(f"allow entry {a.text!r} would mask a bare marker ({why})")
            continue
        target, count = _find_section(sections, a.section)
        if target is None:
            errors.append(
                f"allow entry {a.text!r}: section {a.section!r} matches {count} kept sections"
            )
            continue
        phrase = normalise(a.text)
        found = sum(normalise(t).count(phrase) for t in (target.full_heading, *target.blocks))
        if found != 1:
            errors.append(
                f"allow entry {a.text!r} occurs {found} times in {target.full_heading!r} "
                "(must be exactly once)"
            )
            continue
        masks.setdefault(id(target), []).append(phrase)
    if errors:
        raise MemorandumError(f"{label}: allow-list failed:\n  " + "\n  ".join(errors))
    hits: list[str] = []
    for s in sections:
        phrases = masks.get(id(s), [])
        for text in (s.full_heading, *s.blocks):
            norm = normalise(text)
            for phrase in phrases:
                norm = norm.replace(phrase, " ")
            for m in marker_hits(norm):
                context = norm[max(0, m.start() - 60) : m.end() + 60]
                hits.append(f"{s.full_heading!r} contains {m.re.pattern!r}: ...{context}...")
    if hits:
        raise MemorandumError(
            f"{label}: {len(hits)} IA marker(s) in kept memorandum sections:\n  "
            + "\n  ".join(hits)
        )


@dataclass(frozen=True)
class QuantAllow:
    """One kept sentence with a quantitative marker, reviewed as not an IA finding."""

    section: str  # the section's full heading or its number, as for ``Redaction``
    sentence: str  # the exact sentence, or a prefix of it that starts exactly one sentence
    reason: str


def quant_hits(text: str) -> list[str]:
    """The quantitative markers in ``text`` (normalised first), as matched snippets."""
    norm = normalise(text)
    return [m.group(0) for pattern in _QUANT for m in pattern.finditer(norm)]


def sentences(text: str) -> list[str]:
    """``text`` split into sentences (abbreviation-aware, whitespace collapsed)."""
    block = _norm(text)
    cuts = sorted(sentence_starts(block)) + [len(block)]
    return [block[a:b].strip() for a, b in zip(cuts, cuts[1:], strict=False) if block[a:b].strip()]


def quant_guard(
    sections: list[MemorandumSection],
    *,
    allow: Iterable[QuantAllow] = (),
    label: str = "memorandum",
) -> None:
    """Raise listing every kept sentence with a quantitative marker that no ``allow`` entry
    covers, and every allow entry that is stale (no section, no sentence, several sentences,
    or a sentence without a marker)."""
    covered: set[tuple[int, int, int]] = set()  # (section id, block index, sentence start)
    errors: list[str] = []
    for a in allow:
        target, count = _find_section(sections, a.section)
        if target is None:
            errors.append(
                f"quant_allow {a.sentence[:50]!r}: section {a.section!r} matches {count} kept "
                "sections"
            )
            continue
        needle = _norm(a.sentence)
        if not needle or not a.reason.strip():
            errors.append(f"quant_allow in {target.full_heading!r}: empty sentence or reason")
            continue
        hits = []
        for i, raw in enumerate(target.blocks):
            block = _norm(raw)
            hits += [(i, pos) for pos in sentence_starts(block) if block.startswith(needle, pos)]
        if len(hits) != 1:
            what = "not found" if not hits else f"starts {len(hits)} sentences"
            errors.append(f"quant_allow {needle[:60]!r} in {target.full_heading!r}: {what}")
            continue
        i, pos = hits[0]
        block = _norm(target.blocks[i])
        end = next((e for e in _sentence_ends(block) if e > pos), len(block))
        if not quant_hits(block[pos:end]):
            errors.append(
                f"quant_allow {needle[:60]!r} in {target.full_heading!r}: the sentence has no "
                "quantitative marker (stale entry)"
            )
            continue
        covered.add((id(target), i, pos))
    if errors:
        raise MemorandumError(f"{label}: quant_allow failed:\n  " + "\n  ".join(errors))
    flagged: list[str] = []
    for s in sections:
        for i, raw in enumerate(s.blocks):
            block = _norm(raw)
            cuts = sorted(sentence_starts(block)) + [len(block)]
            for a, b in zip(cuts, cuts[1:], strict=False):
                sentence = block[a:b].strip()
                if sentence and quant_hits(sentence) and (id(s), i, a) not in covered:
                    flagged.append(f"{s.full_heading!r}: {sentence}")
    if flagged:
        raise MemorandumError(
            f"{label}: {len(flagged)} kept sentence(s) with an estimate, percentage, EUR amount "
            "or ratio need a manual decision (redact them, or list them in quant_allow with a "
            "reason):\n  " + "\n  ".join(flagged)
        )


def memorandum_sources(
    kept: list[MemorandumSection],
    removed: list[str],
    *,
    version_id: str,
    label: str,
    redactions: Mapping[str, Sized] | None = None,
) -> list[Source]:
    """One source per kept top-level section; each records the full strip list and, per own
    section with redactions, only their count ("redacted: 2 sentences in <heading>").

    ``redactions`` maps a full heading to its redacted entries (``RedactResult.records``); only
    their number is used, so no reason text can reach the agent-visible source."""
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
                    f"redacted: {n} sentence{'s' if n != 1 else ''} in {s.full_heading}"
                    for s in group
                    if (n := len(redactions.get(s.full_heading, ())))
                ],
            )
        )
    ids = [s.source_id for s in sources]
    if len(set(ids)) != len(ids):
        raise MemorandumError(f"{label}: duplicate memorandum source ids {ids}")
    return sources
