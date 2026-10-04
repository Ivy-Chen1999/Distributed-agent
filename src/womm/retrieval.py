"""Layer 1 retrieval: resolve provision keys through an expert's data scope.

``retrieve`` is the only path from the corpus to an expert's context. For each requested key it
grants the full text (both versions), or an obligation view built from the corpus records, or
nothing; and it records one ``RetrievalRecord`` per requested key either way. Refusals and
unknown keys are data, never exceptions.

Text comes from ``text_store``: the fixture in preset mode (so v0 texts stay byte-stable), the
corpus in explore mode. Obligation views always come from the corpus.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

from womm.data.corpus import Corpus, Obligation
from womm.models.regulation import RegulationVersion, Source
from womm.models.run import RetrievalRecord, RetrievalStatus
from womm.models.system_version import DataScope

ObligationView = Literal["actors", "full"]


class TextStore(Protocol):
    """``Fixture`` and ``Corpus`` both satisfy this."""

    @property
    def sources(self) -> Mapping[str, Source]: ...

    def version(self, version_id: str) -> RegulationVersion: ...


@dataclass(frozen=True)
class Retrieval:
    sources: list[Source]
    records: list[RetrievalRecord]

    @property
    def granted_keys(self) -> list[str]:
        return [r.key for r in self.records if r.granted]

    @property
    def refused_keys(self) -> list[str]:
        return [r.key for r in self.records if r.status in ("out_of_scope", "unknown_key")]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _field_lines(record: Obligation, view: ObligationView) -> list[str]:
    actors = record.actors or record.primary_actor
    date = (
        f"{record.applies_from} ({record.date_label})"
        if record.applies_from and record.date_label  # an unlabelled date is never shown
        else None
    )
    if view == "actors":
        fields = [
            ("addressee", record.addressee_text),
            ("actors", actors),
            ("condition", record.condition),
            ("applies from", date),
        ]
        if record.actor_unspecified:
            fields.append(("span", record.span))
    else:
        kind = ", ".join(x for x in (record.statement_type, record.modal) if x)
        public = None if record.public_sector is None else ("yes" if record.public_sector else "no")
        fields = [
            ("statement", kind or None),
            ("primary actor", record.primary_actor),
            ("actors", record.actors),
            ("addressee", record.addressee_text),
            ("condition", record.condition),
            ("action", record.action),
            ("timing", record.timing),
            ("public sector", public),
            ("applies from", date),
            ("span", record.span),
        ]
    return [f"[{record.obligation_id}]"] + [f"{name}: {value}" for name, value in fields if value]


def render_obligations(records: Iterable[Obligation], view: ObligationView) -> str:
    """The text of an obligation-view source: one block per record. Under ``actors`` the action
    is never shown, and the verbatim span only where the primary actor is unspecified (the span
    fallback); under ``full`` every field and the span are shown."""
    return "\n\n".join("\n".join(_field_lines(r, view)) for r in records)


def obligations_source(corpus: Corpus, version_id: str, key: str, view: ObligationView) -> Source:
    """The obligation view of ``key`` in ``version_id``, or a ValueError if it has no records.

    The source id names the version the records come from: ``<records_version>/obligations/
    art_<n>`` (or ``annex_<roman>``), so the consolidated version's borrowed 2024 records and the
    2024 version's own share one source."""
    records_from, records = corpus.obligation_records(version_id, key)
    if not records:
        raise ValueError(f"no obligation records for {key!r} in {version_id}")
    provision = corpus.version(records_from).by_key()[key]
    suffix = provision.source_id.split("/", 1)[1]
    text_title = corpus.sources[provision.source_id].title
    return Source(
        source_id=f"{records_from}/obligations/{suffix}",
        title=f"{text_title} (obligation records, {view} view)",
        kind="obligations",
        text=render_obligations(records, view),
    )


def retrieve(
    scope: DataScope | None,
    keys: Iterable[str],
    before_version: str | None,
    after_version: str,
    text_store: TextStore,
    corpus: Corpus | None,
    *,
    agent: str,
    clock: Callable[[], dt.datetime] = _utc_now,
) -> Retrieval:
    """Resolve ``keys`` through ``scope`` for ``agent``.

    Sources follow ``Fixture.scenario_sources`` order: the before version's provisions in
    document order, then the after version's, each as its text or its obligation view. Records
    follow request order, one per distinct key. ``scope=None`` grants every known key's text
    (the v0 behaviour) and never reads ``corpus``. Memorandum sources are not handled here
    (see ``memorandum_sources``)."""
    requested = list(dict.fromkeys(keys))
    version_ids = [v for v in (before_version, after_version) if v is not None]
    versions = [text_store.version(v) for v in version_ids]
    present = {v.version_id: v.by_key() for v in versions}

    status: dict[str, RetrievalStatus] = {}
    obligation_sources: dict[tuple[str, str], Source] = {}
    for key in requested:
        in_versions = [vid for vid in version_ids if key in present[vid]]
        if not in_versions:
            status[key] = "unknown_key"
        elif scope is None or scope.sees_text(key):
            status[key] = "granted_text"
        elif scope.obligations != "none":
            if corpus is None:
                raise ValueError("an obligation view needs the provision corpus")
            for vid in in_versions:
                if corpus.obligation_records(vid, key)[1]:
                    obligation_sources[(vid, key)] = obligations_source(
                        corpus, vid, key, scope.obligations
                    )
            granted = any((vid, key) in obligation_sources for vid in in_versions)
            status[key] = "granted_obligations" if granted else "out_of_scope"
        else:
            status[key] = "out_of_scope"

    ordered: dict[str, Source] = {}
    ids_by_key: dict[str, list[str]] = {k: [] for k in requested}
    for v in versions:
        for p in v.provisions:
            key = p.provision_key
            if status.get(key) == "granted_text":
                source = text_store.sources.get(p.source_id)
            elif status.get(key) == "granted_obligations":
                source = obligation_sources.get((v.version_id, key))
            else:
                continue
            if source is None:
                continue
            ordered.setdefault(source.source_id, source)
            if source.source_id not in ids_by_key[key]:
                ids_by_key[key].append(source.source_id)

    at = clock()
    records = [
        RetrievalRecord(agent=agent, key=k, status=status[k], source_ids=ids_by_key[k], at=at)
        for k in requested
    ]
    return Retrieval(sources=list(ordered.values()), records=records)


def memorandum_sources(scope: DataScope | None, sources: Mapping[str, Source]) -> list[Source]:
    """The stripped memorandum sources an expert gets: all of them when unscoped (v0) or when the
    scope sets ``sees_memorandum``, otherwise none. They are not logged as retrievals."""
    if scope is not None and not scope.sees_memorandum:
        return []
    return [s for s in sources.values() if s.kind == "memorandum"]
