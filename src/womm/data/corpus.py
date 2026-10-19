"""Load the committed whole-act provision corpus (data/corpus/<regulation>/).

Built by ``scripts/build_corpus.py``; read locally at runtime, never fetched. Files:

- ``proposal.json``, ``final.json``, ``consolidated.json``: one Regulation-shaped version each,
  covering every article and annex;
- ``obligations.json``: ``version_id -> provision key -> records`` for the proposal and the
  adopted text (the consolidated text has none of its own);
- ``index.json``: version metadata and one text-free row per unit and version.

The corpus has no memorandum and no impact-assessment material (R2); those stay in the fixture.
The corpus is a text store with the same ``version()`` / ``sources`` surface as ``Fixture``, so
retrieval can resolve text against either.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from womm.config import REPO_ROOT
from womm.data.fixtures import FixtureError
from womm.models.base import StrictModel
from womm.models.dossier import LawVersion
from womm.models.regulation import Regulation, RegulationVersion, Source

DEFAULT_CORPUS_DIR = REPO_ROOT / "data" / "corpus" / "ai_act"
DeltaKind = Literal["added", "removed", "modified", "unchanged"]
# The colleague's unit-level delta (``delta_status``, proposal -> adopted), per obligation record.
UNIT_DELTA_VALUES = ("added", "modified", "split_merge", "minor_edit", "unchanged")

# Versions that Regulation (EU) 2026/1744 (the Digital Omnibus, in force 27 July 2026) has
# amended. A run on one of them analyses superseded law and is labelled pre-Omnibus.
PRE_OMNIBUS_VERSIONS = frozenset({"reg2024_1689"})
PRE_OMNIBUS_NOTE = (
    "Pre-Omnibus: Regulation (EU) 2024/1689 as adopted, before the amendments of Regulation "
    "(EU) 2026/1744 (in force since 27 July 2026). Some provisions and dates have changed."
)
# The marker on adopted-text material for a unit a consolidated version amended or inserted:
# the title of a 2024 provision source shown in a run on the consolidated text, and every record
# (and the title) of a 2024 obligation view of such a unit.
SUPERSEDED_MARK = "as adopted 2024 — superseded in part by Regulation (EU) 2026/1744"


def index_line(row: Mapping[str, Any] | IndexRow) -> str:
    """One index row as a Planner reads it: key | [number] heading | delta | actor counts.

    The printed number is shown only where the key does not already end with it (crosswalk
    keys, renumbered proposal articles). Shared with ``scripts/build_corpus.py``."""
    if not isinstance(row, Mapping):
        row = row.model_dump()
    label = "Art" if row["kind"] == "article" else "Annex"
    number = "" if row["key"].endswith(f"/{row['number']}") else f"{label} {row['number']} "
    parts = [row["key"], f"{number}{row['heading']}".strip(), row["delta"]]
    if row["obligations"]:
        parts.append(", ".join(f"{a} {n}" for a, n in row["obligations"].items()))
    return " | ".join(parts)


def law_version(version: RegulationVersion) -> LawVersion:
    """Dossier header metadata for the version a run analyses."""
    pre = version.version_id in PRE_OMNIBUS_VERSIONS
    return LawVersion(
        version_id=version.version_id,
        status=version.status,
        source=version.source,
        date=version.date.isoformat(),
        pre_omnibus=pre,
        note=PRE_OMNIBUS_NOTE if pre else None,
    )


class Obligation(StrictModel):
    """One rule-based obligation record, with the corpus date rules already applied: a date is
    present only together with its ``date_label``; stale dates are withheld."""

    obligation_id: str
    unit_id: str
    article: str | None
    division: str | None
    statement_type: str | None
    modal: str | None
    primary_actor: str | None
    actors: str | None
    addressee_text: str | None
    condition: str | None
    action: str | None
    timing: str | None
    public_sector: bool | None
    span: str
    applies_from: str | None
    date_label: str | None
    date_withheld: str | None
    # Not rendered into any obligation view (``womm.retrieval._field_lines``): the cost step
    # reads it to mark obligations changed or added after the proposal.
    unit_delta: str | None = None

    @property
    def actor_unspecified(self) -> bool:
        return self.primary_actor in (None, "", "unspecified")


class CorpusVersion(StrictModel):
    version_id: str
    label: str
    celex: str
    date: str
    status: str
    file: str
    delta_basis: str
    obligations_from: str


class IndexRow(StrictModel):
    key: str
    version: str
    kind: Literal["article", "annex"]
    number: str
    heading: str
    delta: DeltaKind
    obligations: dict[str, int]
    chars: int


class CorpusIndex(StrictModel):
    regulation_id: str
    versions: list[CorpusVersion]
    rows: list[IndexRow]


@dataclass(frozen=True)
class Corpus:
    regulation: Regulation
    obligations: dict[str, dict[str, list[Obligation]]]
    index: CorpusIndex

    def version(self, version_id: str) -> RegulationVersion:
        for v in self.regulation.versions:
            if v.version_id == version_id:
                return v
        raise FixtureError(f"unknown corpus version {version_id!r}")

    def version_info(self, version_id: str) -> CorpusVersion:
        for v in self.index.versions:
            if v.version_id == version_id:
                return v
        raise FixtureError(f"unknown corpus version {version_id!r}")

    @cached_property
    def _rows(self) -> dict[tuple[str, str], IndexRow]:
        return {(r.version, r.key): r for r in self.index.rows}

    def row(self, version_id: str, key: str) -> IndexRow | None:
        return self._rows.get((version_id, key))

    def index_rows(self, version_id: str) -> list[IndexRow]:
        return [r for r in self.index.rows if r.version == version_id]

    @cached_property
    def sources(self) -> dict[str, Source]:
        """Every unit's text as a citable source (kind ``provision`` or ``annex``)."""
        out: dict[str, Source] = {}
        for v in self.regulation.versions:
            info = self.version_info(v.version_id)
            for p in v.provisions:
                row = self.row(v.version_id, p.provision_key)
                is_annex = row is not None and row.kind == "annex"
                number = row.number if row is not None else p.article
                label = "Annex" if is_annex else "Article"
                heading = f": {row.heading}" if row is not None and row.heading else ""
                out[p.source_id] = Source(
                    source_id=p.source_id,
                    title=f"{info.label}, {label} {number}{heading}",
                    kind="annex" if is_annex else "provision",
                    text=p.text,
                )
        return out

    def is_consolidated(self, version_id: str) -> bool:
        """Whether ``version_id`` is a consolidated text (False for a version not in the corpus,
        such as a fixture-only version)."""
        return any(
            v.version_id == version_id and v.status == "consolidated" for v in self.index.versions
        )

    @cached_property
    def _superseded(self) -> dict[str, frozenset[str]]:
        """Base version -> the keys a consolidated version of it amended or inserted. The base is
        the version the consolidated text borrows its obligation records from (the adopted
        text)."""
        out: dict[str, frozenset[str]] = {}
        for info in self.index.versions:
            if info.status != "consolidated":
                continue
            keys = {r.key for r in self.index_rows(info.version_id) if r.delta != "unchanged"}
            out[info.obligations_from] = out.get(info.obligations_from, frozenset()) | keys
        return out

    def is_superseded(self, version_id: str, key: str) -> bool:
        """Whether a consolidated version amended (or inserted) ``key`` of ``version_id``."""
        return key in self._superseded.get(version_id, frozenset())

    @cached_property
    def _source_units(self) -> dict[str, tuple[str, str]]:
        return {
            p.source_id: (v.version_id, p.provision_key)
            for v in self.regulation.versions
            for p in v.provisions
        }

    def for_target(self, source: Source, after_version: str) -> Source:
        """``source`` as a run on ``after_version`` shows it. In a run on a consolidated version,
        a provision text of the version it consolidates whose unit was amended carries
        ``SUPERSEDED_MARK`` in its title; the text is never changed. Any other source is
        returned as is."""
        if not self.is_consolidated(after_version):
            return source
        unit = self._source_units.get(source.source_id)
        if unit is None or source.kind not in ("provision", "annex"):
            return source
        version_id, key = unit
        base = self.version_info(after_version).obligations_from
        if version_id != base or not self.is_superseded(version_id, key):
            return source
        return source.model_copy(update={"title": f"{source.title} ({SUPERSEDED_MARK})"})

    def obligation_records(self, version_id: str, key: str) -> tuple[str, list[Obligation]]:
        """``(records_version, records)`` an obligation view of ``key`` in ``version_id`` may use.

        The consolidated version has no records of its own: it borrows the adopted records, and
        only for units 2026/1744 left unchanged (an amended or inserted unit gets no view)."""
        info = self.version_info(version_id)
        records_from = info.obligations_from
        if records_from != version_id:
            row = self.row(version_id, key)
            if row is None or row.delta != "unchanged":
                return records_from, []
        return records_from, list(self.obligations.get(records_from, {}).get(key, []))


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError(f"cannot read {path}: {exc}") from exc


def validate_corpus(corpus: Corpus) -> None:
    """Raise FixtureError naming the first index row or obligation key that does not resolve."""
    versions = {v.version_id: v.by_key() for v in corpus.regulation.versions}
    if len(versions) != len(corpus.regulation.versions):
        raise FixtureError("duplicate version_id in corpus")
    infos = {v.version_id for v in corpus.index.versions}
    if infos != set(versions):
        raise FixtureError(
            f"index versions {sorted(infos)} differ from corpus versions {sorted(versions)}"
        )
    for row in corpus.index.rows:
        if row.key not in versions.get(row.version, {}):
            raise FixtureError(f"index key {row.key!r} is not a provision of {row.version}")
    for vid, by_key in corpus.obligations.items():
        if vid not in versions:
            raise FixtureError(f"obligations for unknown version {vid!r}")
        unknown = sorted(set(by_key) - set(versions[vid]))
        if unknown:
            raise FixtureError(f"obligations of {vid} name unknown keys {unknown[:5]}")
        for records in by_key.values():
            for r in records:
                if r.unit_delta is not None and r.unit_delta not in UNIT_DELTA_VALUES:
                    raise FixtureError(
                        f"obligation {r.obligation_id}: unknown unit_delta {r.unit_delta!r}"
                    )
    for info in corpus.index.versions:
        if info.obligations_from not in versions:
            raise FixtureError(
                f"{info.version_id} takes obligations from unknown {info.obligations_from!r}"
            )


def load_corpus(directory: Path = DEFAULT_CORPUS_DIR) -> Corpus:
    try:
        index = CorpusIndex.model_validate(_read_json(directory / "index.json"))
        regulation: Regulation | None = None
        for info in index.versions:
            reg = Regulation.model_validate(_read_json(directory / info.file))
            if regulation is None:
                regulation = reg
            elif reg.regulation_id != regulation.regulation_id:
                raise FixtureError(
                    f"{info.file} is for {reg.regulation_id!r}, not {regulation.regulation_id!r}"
                )
            else:
                regulation = regulation.model_copy(
                    update={"versions": [*regulation.versions, *reg.versions]}
                )
        if regulation is None:
            raise FixtureError(f"index.json in {directory} lists no versions")
        raw = _read_json(directory / "obligations.json")
        obligations = {
            vid: {key: [Obligation.model_validate(r) for r in recs] for key, recs in by.items()}
            for vid, by in raw.items()
        }
    except ValidationError as exc:
        raise FixtureError(f"corpus in {directory} does not match the models: {exc}") from exc
    corpus = Corpus(regulation, obligations, index)
    validate_corpus(corpus)
    return corpus


@cache
def load_default_corpus() -> Corpus:
    """The committed AI Act corpus, loaded once per process (explore runs)."""
    return load_corpus(DEFAULT_CORPUS_DIR)
