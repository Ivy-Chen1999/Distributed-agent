"""Whole-act comparison of two corpus versions for the console's Act screen.

Pure and deterministic: no LLM, no database. For every article and annex the comparison pairs
the paragraphs of the two versions and attaches the rule-based obligation records of each side,
summarised per paragraph. It reads only the committed corpus (``womm.data.corpus``).

- Article change kinds are those of ``womm.diff.diff_versions`` (whitespace-insensitive), so the
  screen and the Planner's diff never disagree.
- Paragraphs are matched by their printed number ("1.", "2a.") and its occurrence within the
  article: annexes restart their numbering per section. Text before the first number is its own
  paragraph. A renumbered or moved paragraph therefore shows as removed plus added; this is the
  stated basis (``paragraph_basis``), not an alignment.
- Obligation records follow the corpus rules (``Corpus.obligation_records``): a consolidated
  version borrows the adopted records only for units it left unchanged, so an amended unit has
  none on its after side. Withheld dates are counted by reason, never filled in.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import Field

from womm.data.corpus import Corpus, Obligation
from womm.data.fixtures import FixtureError
from womm.diff import diff_versions
from womm.models.base import StrictModel

ParagraphChange = Literal["added", "removed", "modified", "unchanged"]
DUTY_TYPES = ("duty", "prohibition")

_NUMBER = re.compile(r"^(\d+[a-z]?)\.\s")
# An article record names its paragraph (`art26.par3`), an annex record its point (`anxVII.pt_1`).
_PAR = re.compile(r"\.(?:par|pt_)(\d+[a-z]?)(?:\.|$)")
_WS = re.compile(r"\s+")


class VersionRef(StrictModel):
    version_id: str
    label: str
    celex: str
    date: str
    status: str


class ComparisonRef(StrictModel):
    """A pair of corpus versions the console can compare (one per ``delta_basis``)."""

    regulation_id: str
    before: VersionRef
    after: VersionRef
    scenario_ids: list[str] = Field(
        default_factory=list,
        description="Fixture scenarios with this before -> after pair; their runs map onto it.",
    )


class ObligationSummary(StrictModel):
    """Duties and prohibitions of one side of a paragraph or article."""

    duties: int = 0
    by_actor: dict[str, int] = Field(
        default_factory=dict, description="primary_actor -> count; 'unspecified' included."
    )
    public_sector: int = 0
    date_withheld: dict[str, int] = Field(
        default_factory=dict,
        description="Reason -> count, e.g. date_moved_2026 or amended_2026 (corpus date rules).",
    )
    obligation_ids: list[str] = Field(default_factory=list)


class ParagraphPair(StrictModel):
    label: str = Field(
        description="Printed paragraph number without the dot; '' for unnumbered text."
    )
    change: ParagraphChange
    before_text: str | None
    after_text: str | None
    before_obligations: ObligationSummary | None
    after_obligations: ObligationSummary | None


class ArticleComparison(StrictModel):
    provision_key: str
    kind: Literal["article", "annex"]
    number: str = Field(description="Number in the after version (the before version if removed).")
    heading: str
    before_number: str | None
    change: Literal["added", "removed", "modified", "unchanged"]
    superseded_before: bool = Field(
        description="The before text was amended or replaced by a consolidated version."
    )
    paragraphs: list[ParagraphPair]
    unplaced_before: ObligationSummary | None = Field(
        description="Before-side records whose unit names no paragraph found in the text."
    )
    unplaced_after: ObligationSummary | None


class ActComparison(StrictModel):
    regulation_id: str
    title: str
    before: VersionRef
    after: VersionRef
    paragraph_basis: Literal["number"] = "number"
    articles: list[ArticleComparison]


@dataclass
class _Block:
    label: str
    key: str
    text: str


def paragraphs(text: str) -> list[_Block]:
    """Split a provision text into numbered paragraphs, keyed by number and occurrence."""
    lines = [s.strip() for s in text.replace("\r", "").split("\n")]
    groups: list[tuple[str, list[str]]] = []
    for line in filter(None, lines):
        m = _NUMBER.match(line)
        if m or not groups:
            groups.append((m.group(1) if m else "", []))
        groups[-1][1].append(line)
    seen: Counter[str] = Counter()
    out: list[_Block] = []
    for label, body in groups:
        seen[label] += 1
        out.append(_Block(label, f"{label}#{seen[label]}", "\n".join(body)))
    return out


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


@dataclass
class _Acc:
    duties: int = 0
    by_actor: Counter = field(default_factory=Counter)
    public_sector: int = 0
    withheld: Counter = field(default_factory=Counter)
    ids: list[str] = field(default_factory=list)

    def add(self, r: Obligation) -> None:
        self.duties += 1
        self.by_actor[r.primary_actor or "unspecified"] += 1
        self.public_sector += bool(r.public_sector)
        if r.date_withheld:
            self.withheld[r.date_withheld] += 1
        self.ids.append(r.obligation_id)

    def summary(self) -> ObligationSummary:
        return ObligationSummary(
            duties=self.duties,
            by_actor=dict(sorted(self.by_actor.items())),
            public_sector=self.public_sector,
            date_withheld=dict(sorted(self.withheld.items())),
            obligation_ids=self.ids,
        )


def _by_paragraph(records: list[Obligation]) -> dict[str, _Acc]:
    """Duties and prohibitions per paragraph or annex point number ('' when the unit names none)."""
    out: dict[str, _Acc] = {}
    for r in records:
        if r.statement_type not in DUTY_TYPES:
            continue
        m = _PAR.search(r.unit_id)
        out.setdefault(m.group(1) if m else "", _Acc()).add(r)
    return out


def _ref(corpus: Corpus, version_id: str) -> VersionRef:
    i = corpus.version_info(version_id)
    return VersionRef(
        version_id=i.version_id, label=i.label, celex=i.celex, date=i.date, status=i.status
    )


class ScenarioRef(StrictModel):
    """A scenario that analyses a version (or a pair of versions) of the regulation; its finished
    runs are the analyses a reader can open. No impact-assessment reference is exposed."""

    scenario_id: str
    before_version: str | None
    after_version: str
    description: str


class RegulationEntry(StrictModel):
    """One regulation the console can open: its corpus versions and the comparisons between them.

    Only acts with a whole-act corpus are listed. Evaluation fixtures are not a catalogue: listing
    them would reveal which proposals the (sealed) holdout uses."""

    regulation_id: str
    title: str
    versions: list[VersionRef]
    comparisons: list[ComparisonRef]
    scenarios: list[ScenarioRef] = Field(
        default_factory=list,
        description="Scenarios on any version of this regulation, filled in by the API.",
    )


def regulation_entry(corpus: Corpus) -> RegulationEntry:
    return RegulationEntry(
        regulation_id=corpus.regulation.regulation_id,
        title=corpus.regulation.title,
        versions=[_ref(corpus, v.version_id) for v in corpus.index.versions],
        comparisons=comparisons(corpus),
    )


def comparisons(corpus: Corpus) -> list[ComparisonRef]:
    """The version pairs the corpus index declares (``delta_basis``), in index order."""
    out: list[ComparisonRef] = []
    seen: set[tuple[str, str]] = set()
    for info in corpus.index.versions:
        before, _, after = info.delta_basis.partition(" -> ")
        if not after or (before, after) in seen:
            continue
        seen.add((before, after))
        out.append(
            ComparisonRef(
                regulation_id=corpus.regulation.regulation_id,
                before=_ref(corpus, before),
                after=_ref(corpus, after),
            )
        )
    return out


def compare(corpus: Corpus, before_id: str, after_id: str) -> ActComparison:
    """Compare two corpus versions article by article. Raises FixtureError for an unknown or
    undeclared pair."""
    if not any(
        c.before.version_id == before_id and c.after.version_id == after_id
        for c in comparisons(corpus)
    ):
        raise FixtureError(f"no comparison {before_id!r} -> {after_id!r} in the corpus")
    before, after = corpus.version(before_id), corpus.version(after_id)
    old, new = before.by_key(), after.by_key()
    changed = {c.provision_key: c.kind for c in diff_versions(before, after).changes}
    articles: list[ArticleComparison] = []
    for key in [*new, *(k for k in old if k not in new)]:
        b, a = old.get(key), new.get(key)
        row = corpus.row(after_id, key) or corpus.row(before_id, key)
        before_row = corpus.row(before_id, key)
        _, brecs = corpus.obligation_records(before_id, key) if b else (None, [])
        _, arecs = corpus.obligation_records(after_id, key) if a else (None, [])
        bacc, aacc = _by_paragraph(brecs), _by_paragraph(arecs)
        bblocks = paragraphs(b.text) if b else []
        ablocks = paragraphs(a.text) if a else []
        bmap = {x.key: x for x in bblocks}
        used: set[str] = set()
        pairs: list[ParagraphPair] = []

        def side(
            acc: dict[str, _Acc], blk: _Block | None, present: bool
        ) -> ObligationSummary | None:
            if not present:
                return None
            if blk is None or not blk.key.endswith("#1"):
                return ObligationSummary()
            hit = acc.get(blk.label)
            return hit.summary() if hit else ObligationSummary()

        for ab in ablocks:
            bb = bmap.get(ab.key)
            if bb:
                used.add(bb.key)
            kind: ParagraphChange = (
                "added"
                if bb is None
                else "unchanged"
                if _norm(bb.text) == _norm(ab.text)
                else "modified"
            )
            pairs.append(
                ParagraphPair(
                    label=ab.label,
                    change=kind,
                    before_text=bb.text if bb else None,
                    after_text=ab.text,
                    before_obligations=side(bacc, bb, bb is not None),
                    after_obligations=side(aacc, ab, True),
                )
            )
        for bb in bblocks:
            if bb.key not in used:
                pairs.append(
                    ParagraphPair(
                        label=bb.label, change="removed", before_text=bb.text, after_text=None,
                        before_obligations=side(bacc, bb, True), after_obligations=None,
                    )
                )  # fmt: skip

        def unplaced(acc: dict[str, _Acc], blocks: list[_Block]) -> ObligationSummary | None:
            labels = {x.label for x in blocks if x.key.endswith("#1")}
            rest = _Acc()
            for label, x in acc.items():
                if label not in labels:
                    rest.duties += x.duties
                    rest.by_actor.update(x.by_actor)
                    rest.public_sector += x.public_sector
                    rest.withheld.update(x.withheld)
                    rest.ids.extend(x.ids)
            return rest.summary() if rest.duties else None

        articles.append(
            ArticleComparison(
                provision_key=key,
                kind=row.kind if row else "article",
                number=row.number if row else (a or b).article,
                heading=row.heading if row else "",
                before_number=before_row.number if before_row else (b.article if b else None),
                change=changed.get(key, "unchanged"),
                superseded_before=bool(b) and corpus.is_superseded(before_id, key),
                paragraphs=pairs,
                unplaced_before=unplaced(bacc, bblocks),
                unplaced_after=unplaced(aacc, ablocks),
            )
        )
    return ActComparison(
        regulation_id=corpus.regulation.regulation_id,
        title=corpus.regulation.title,
        before=_ref(corpus, before_id),
        after=_ref(corpus, after_id),
        articles=articles,
    )
