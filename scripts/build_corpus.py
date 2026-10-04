"""Build data/corpus/ai_act/: the whole AI Act in three versions, its obligations and an index.

    uv run python scripts/build_corpus.py [--refresh] [--accept-upstream-changes]

Versions (every article and annex of each):

- ``com2021_206``: the Commission proposal COM(2021) 206, from the colleague's provision units;
- ``reg2024_1689``: Regulation (EU) 2024/1689 as adopted, from the colleague's provision units;
- ``reg2024_1689_c20260727``: the AI Act as consolidated on 27 July 2026 (CELEX
  02024R1689-20260727), after Regulation (EU) 2026/1744. Articles and annexes that 2026/1744
  amended or inserted are parsed from the EUR-Lex consolidated XHTML
  (``womm.data.parse_consolidated``); every other one keeps the 2024 text, after a check that
  the consolidated text has the same words (it differs only by footnote calls and spacing).

The amended set is read from the consolidation markers (``▼Mn``) and must equal the articles
and annexes named by the points of Article 1 of Regulation (EU) 2026/1744, or the build fails.

Provision keys: the 15 hand-maintained crosswalk keys (data/fixtures/ai_act/crosswalk.yaml) for
scenario articles; otherwise ``ai_act/art/<n>`` and ``ai_act/annex/<roman>`` by the adopted
numbering, shared with the proposal unit the colleague's container alignment pairs with it.
Inserted articles keep their printed number (``ai_act/art/4a``). Proposal units with no adopted
counterpart get ``ai_act/proposal/art/<n>`` / ``ai_act/proposal/annex/<roman>``.

Obligations (the colleague's rule-based records) are kept for the proposal and the adopted text;
the consolidated text has none of its own. Dates are never emitted as current:

- Article 113's own records (entry into force and application) are left out entirely;
- ``applies_from`` is withheld for Chapter III Sections 1-3 (Articles 6-27, except Article 6(5)),
  for Articles 102-110, for every article 2026/1744 amends, and for every annex record (an annex
  applies through the articles that refer to it), because 2026/1744 moved or may have moved
  those dates;
- every remaining date carries the label "as adopted (2024)";
- proposal records carry no date (a proposal never applied).

``index.json`` has one row per unit and version: key, number, heading, delta kind against the
previous version, obligation counts by primary actor and text length. It never holds text. For
the consolidated version, the counts are those of the 2024 records an explore run may use: only
unamended units have any (amended units get no obligation view).

No explanatory memorandum and no impact-assessment material is written here (R2): those stay in
data/fixtures/ai_act/. Every download is pinned by sha256 in downloads.json and cached in
.cache/cellar/. Runtime code reads only these files.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from womm.config import REPO_ROOT
from womm.data import parse_consolidated, parse_units
from womm.data.cellar import CELLAR_BASE, XHTML, CellarError, celex_url, fetch
from womm.data.fixtures import DEFAULT_FIXTURE_DIR, Crosswalk, FixtureError, load_crosswalk
from womm.data.parse_consolidated import ConsolidatedParseError
from womm.data.parse_proposal import Article
from womm.models.regulation import Provision, Regulation, RegulationVersion

# The pinned pipeline and the proposal/adopted version specs are shared with the fixture build.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_fixture import (  # noqa: E402
    ANY,
    FINAL,
    PIPELINE_BASE,
    PIPELINE_CONTAINERS,
    PIPELINE_REPO_RAW,
    PIPELINE_UNITS,
    PROPOSAL,
    VersionSpec,
    check_downloads,
)

DEFAULT_CORPUS_DIR = REPO_ROOT / "data" / "corpus" / "ai_act"
REGULATION_ID = "ai_act"
REGULATION_TITLE = "Artificial Intelligence Act"

CONSOLIDATED = VersionSpec(
    "reg2024_1689_c20260727",
    "Regulation (EU) 2024/1689 as consolidated on 27 July 2026",
    "02024R1689-20260727",
    dt.date(2026, 7, 27),
    "consolidated",
    "consolidated.json",
)
VERSIONS = (PROPOSAL, FINAL, CONSOLIDATED)
# womm.data.cellar.celex_url rejects the '-' of a consolidated CELEX number.
CONSOLIDATED_URL = f"{CELLAR_BASE}/celex/{CONSOLIDATED.celex}"
AMENDING_CELEX = "32026R1744"
AMENDING_LABEL = "Regulation (EU) 2026/1744"
AMENDING_URL = celex_url(AMENDING_CELEX)

PIPELINE_OBLIGATIONS = {
    PROPOSAL.version_id: f"{PIPELINE_BASE}/obligations/{PROPOSAL.celex}.jsonl",
    FINAL.version_id: f"{PIPELINE_BASE}/obligations/{FINAL.celex}.jsonl",
}

# --- obligation dates (P0: no stale date reaches an agent) ------------------------------------
AS_ADOPTED_2024 = "as adopted (2024)"
DATES_ARTICLE = "113"
# Chapter III Sections 1-3 (Articles 6-27) and Articles 102-110: the amended Article 113
# moved their application dates. Article 6(5) keeps its date.
DATE_MOVED_ARTICLES = frozenset(str(n) for n in [*range(6, 28), *range(102, 111)])
DATE_MOVED_DIVISIONS = frozenset({"chIII.sec1", "chIII.sec2", "chIII.sec3"})
DATE_KEPT_PARAGRAPHS = {"6": frozenset({"5"})}
WITHHELD_AMENDED = "amended_2026"
WITHHELD_MOVED = "date_moved_2026"
WITHHELD_ANNEX = "annex"
# Obligation fields kept in the corpus (the rest are pipeline bookkeeping or delta scores).
OBLIGATION_FIELDS = (
    "obligation_id",
    "unit_id",
    "article",
    "division",
    "statement_type",
    "modal",
    "primary_actor",
    "actors",
    "addressee_text",
    "condition",
    "action",
    "timing",
    "public_sector",
    "span",
)

_ARTICLE_NUMBER = re.compile(r"\d+[a-z]*")
_ROMAN = re.compile(r"[IVXLC]+")
_PARAGRAPH = re.compile(r":art\d+[a-z]*\.par(\d+[a-z]*)")
_ANNEX_UNIT = re.compile(r":anx([IVXLC]+)\b")
_WS = re.compile(r"\s+")
# Same words: the consolidated XHTML differs from the 2024 text by digit grouping, spacing
# around footnote calls and dash bullets only.
_NOISE = re.compile(r"[\s\-‐-―]")


@dataclass(frozen=True)
class Unit:
    """An article or annex of one version."""

    kind: str  # "art" | "annex"
    number: str
    title: str
    text: str

    @property
    def container(self) -> str:
        """The colleague's container id: ``art99``, ``anxIII``."""
        return f"art{self.number}" if self.kind == "art" else f"anx{self.number}"

    @property
    def label(self) -> str:
        return f"Article {self.number}" if self.kind == "art" else f"Annex {self.number}"


def units_from(articles: Iterable[Article], annexes: Iterable[Article]) -> list[Unit]:
    return [Unit("art", a.number, a.title, a.text) for a in articles] + [
        Unit("annex", a.number, a.title, a.text) for a in annexes
    ]


# --- keys ---------------------------------------------------------------------------------------


def generated_key(kind: str, number: str, *, proposal_only: bool = False) -> str:
    """``ai_act/art/4a``, ``ai_act/annex/III``, ``ai_act/proposal/art/57``."""
    pattern = _ARTICLE_NUMBER if kind == "art" else _ROMAN
    if not pattern.fullmatch(number):
        raise FixtureError(f"not a valid {kind} number: {number!r}")
    prefix = f"{REGULATION_ID}/proposal" if proposal_only else REGULATION_ID
    return f"{prefix}/{kind}/{number}"


def assign_keys(
    units: Mapping[str, list[Unit]],
    pairs: set[tuple[str, str]],
    crosswalk: Crosswalk,
) -> dict[str, dict[str, str]]:
    """version_id -> container -> provision key; fails when two units of one version resolve to
    one key, naming both."""
    final_containers = {u.container for u in units[FINAL.version_id]}
    proposal_containers = {u.container for u in units[PROPOSAL.version_id]}
    for old, new in sorted(pairs):
        if old not in proposal_containers or new not in final_containers:
            raise FixtureError(f"container alignment pairs unknown units {old!r} -> {new!r}")
    partner = {old: new for old, new in pairs}

    def semantic(version_id: str, unit: Unit) -> str | None:
        if unit.kind != "art":
            return None
        for e in crosswalk.entries:
            if e.articles.get(version_id) == unit.number:
                return e.provision_key
        return None

    final_keys = {
        u.container: semantic(FINAL.version_id, u) or generated_key(u.kind, u.number)
        for u in units[FINAL.version_id]
    }
    keys: dict[str, dict[str, str]] = {FINAL.version_id: final_keys}
    keys[PROPOSAL.version_id] = {
        u.container: (
            final_keys[partner[u.container]]
            if u.container in partner
            else semantic(PROPOSAL.version_id, u)
            or generated_key(u.kind, u.number, proposal_only=True)
        )
        for u in units[PROPOSAL.version_id]
    }
    # The consolidated text keeps the adopted numbering; inserted units get generated keys.
    keys[CONSOLIDATED.version_id] = {
        u.container: final_keys.get(u.container) or generated_key(u.kind, u.number)
        for u in units.get(CONSOLIDATED.version_id, [])
    }
    for version_id, by_container in keys.items():
        seen: dict[str, str] = {}
        for container, key in by_container.items():
            other = seen.setdefault(key, container)
            if other != container:
                raise FixtureError(
                    f"{version_id}: {other} and {container} both resolve to key {key!r}"
                )
    return keys


# --- versions -----------------------------------------------------------------------------------


def source_id(version_id: str, unit: Unit) -> str:
    return f"{version_id}/{'art' if unit.kind == 'art' else 'annex'}_{unit.number}"


def build_version(
    spec: VersionSpec, units: list[Unit], keys: Mapping[str, str]
) -> RegulationVersion:
    provisions = [
        Provision(
            provision_key=keys[u.container],
            article=u.number if u.kind == "art" else u.label,
            paragraph=None,
            text=u.text,
            source_id=source_id(spec.version_id, u),
        )
        for u in units
    ]
    return RegulationVersion(
        version_id=spec.version_id,
        date=spec.date,
        status=spec.status,
        source=spec.celex,
        provisions=provisions,
    )


def same_words(a: str, b: str) -> bool:
    return _NOISE.sub("", a) == _NOISE.sub("", b)


def consolidated_units(
    final_units: list[Unit],
    parsed: list[Unit],
    amended: set[tuple[str, str]],
) -> list[Unit]:
    """The consolidated version in document order: amended or inserted units as parsed, the
    others with the adopted text, which must have the same words as the consolidated one."""
    adopted = {(u.kind, u.number): u for u in final_units}
    out = []
    for u in parsed:
        ident = (u.kind, u.number)
        if ident in amended:
            out.append(u)
            continue
        if ident not in adopted:
            raise FixtureError(f"consolidated {u.label} is new but carries no amendment marker")
        old = adopted[ident]
        if not same_words(old.text, u.text):
            raise FixtureError(
                f"consolidated {u.label} differs from the adopted text but carries no "
                "amendment marker"
            )
        out.append(old)
    missing = sorted(set(adopted) - {(u.kind, u.number) for u in parsed})
    if missing:
        raise FixtureError(f"adopted units missing from the consolidated text: {missing}")
    return out


def check_amended(
    marked: tuple[list[str], list[str]], amending: tuple[list[str], list[str]]
) -> None:
    """The ▼Mn markers and the amending act's Article 1 must name the same units."""
    for kind, ours, theirs in (
        ("articles", marked[0], amending[0]),
        ("annexes", marked[1], amending[1]),
    ):
        if set(ours) != set(theirs):
            raise FixtureError(
                f"amended {kind} disagree: markers only {sorted(set(ours) - set(theirs))}, "
                f"{AMENDING_LABEL} only {sorted(set(theirs) - set(ours))}"
            )


# --- obligations --------------------------------------------------------------------------------


def parse_obligations(data: bytes, name: str) -> list[dict[str, Any]]:
    rows = []
    for lineno, line in enumerate(data.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            missing = [f for f in (*OBLIGATION_FIELDS, "applies_from") if f not in row]
            if missing:
                raise KeyError(", ".join(missing))
        except (json.JSONDecodeError, KeyError) as exc:
            raise FixtureError(f"{name} line {lineno}: malformed obligation ({exc})") from None
        rows.append(row)
    return rows


def _container(row: Mapping[str, Any]) -> str:
    if row.get("article"):
        return f"art{row['article']}"
    m = _ANNEX_UNIT.search(row.get("unit_id") or "")
    if not m:
        raise FixtureError(f"obligation {row['obligation_id']} names no article or annex")
    return f"anx{m.group(1)}"


def withheld_reason(row: Mapping[str, Any], amended_articles: set[str]) -> str | None:
    """Why an adopted record's ``applies_from`` must not be shown, or None."""
    art = row.get("article")
    if not art:
        return WITHHELD_ANNEX
    if art in amended_articles:
        return WITHHELD_AMENDED
    m = _PARAGRAPH.search(row.get("unit_id") or "")
    paragraph = m.group(1) if m else None
    if paragraph in DATE_KEPT_PARAGRAPHS.get(art, frozenset()):
        return None
    if art in DATE_MOVED_ARTICLES or row.get("division") in DATE_MOVED_DIVISIONS:
        return WITHHELD_MOVED
    return None


@dataclass
class ObligationStats:
    records: int = 0
    excluded_dates_article: int = 0
    dates_withheld: Counter = field(default_factory=Counter)
    dates_labelled: int = 0
    no_date: int = 0

    def line(self) -> str:
        withheld = dict(sorted(self.dates_withheld.items()))
        return (
            f"{self.records} records kept, {self.excluded_dates_article} Article "
            f"{DATES_ARTICLE} records left out, dates withheld {withheld}, "
            f"{self.dates_labelled} labelled {AS_ADOPTED_2024!r}, {self.no_date} without a date"
        )


def build_obligations(
    rows: list[dict[str, Any]],
    version_id: str,
    keys: Mapping[str, str],
    amended_articles: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], ObligationStats]:
    """provision key -> records, with the date rules applied (see the module docstring)."""
    stats = ObligationStats()
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        container = _container(row)
        if container not in keys:
            raise FixtureError(
                f"obligation {row['obligation_id']}: {container} is not a {version_id} unit"
            )
        record = {f: row[f] for f in OBLIGATION_FIELDS}
        date = row.get("applies_from")
        if version_id == FINAL.version_id:
            if row.get("article") == DATES_ARTICLE:
                stats.excluded_dates_article += 1
                continue
            reason = withheld_reason(row, amended_articles) if date else None
        else:
            reason = "proposal" if date else None
        if reason:
            stats.dates_withheld[reason] += 1
            record.update(applies_from=None, date_label=None, date_withheld=reason)
        elif date:
            stats.dates_labelled += 1
            record.update(applies_from=date, date_label=AS_ADOPTED_2024, date_withheld=None)
        else:
            stats.no_date += 1
            record.update(applies_from=None, date_label=None, date_withheld=None)
        stats.records += 1
        out.setdefault(keys[container], []).append(record)
    return out, stats


# --- index --------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def delta_kinds(
    units: Mapping[str, list[Unit]],
    keys: Mapping[str, Mapping[str, str]],
    amended: set[tuple[str, str]],
) -> dict[str, dict[str, str]]:
    """version_id -> key -> change kind against the neighbouring version.

    Proposal and adopted rows describe the proposal -> adopted change (``removed`` on the
    proposal side, ``added`` on the adopted side); consolidated rows describe the adopted ->
    consolidated change, by the amendment markers."""
    text = {
        vid: {keys[vid][u.container]: u.text for u in us}
        for vid, us in units.items()
        if vid != CONSOLIDATED.version_id
    }
    old, new = text[PROPOSAL.version_id], text[FINAL.version_id]
    out: dict[str, dict[str, str]] = {PROPOSAL.version_id: {}, FINAL.version_id: {}}
    for key in old.keys() | new.keys():
        if key not in new:
            kind = "removed"
        elif key not in old:
            kind = "added"
        else:
            kind = "modified" if _norm(old[key]) != _norm(new[key]) else "unchanged"
        for vid, present in ((PROPOSAL.version_id, old), (FINAL.version_id, new)):
            if key in present:
                out[vid][key] = kind
    final_keys = set(new)
    out[CONSOLIDATED.version_id] = {}
    for u in units.get(CONSOLIDATED.version_id, []):
        key = keys[CONSOLIDATED.version_id][u.container]
        if key not in final_keys:
            kind = "added"
        else:
            kind = "modified" if (u.kind, u.number) in amended else "unchanged"
        out[CONSOLIDATED.version_id][key] = kind
    return out


def obligation_counts(records: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts = Counter(r["primary_actor"] or "unspecified" for r in records)
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def build_index(
    units: Mapping[str, list[Unit]],
    keys: Mapping[str, Mapping[str, str]],
    deltas: Mapping[str, Mapping[str, str]],
    obligations: Mapping[str, Mapping[str, list[dict[str, Any]]]],
    amended: set[tuple[str, str]],
) -> dict[str, Any]:
    versions = []
    rows = []
    for spec in VERSIONS:
        vid = spec.version_id
        if vid == CONSOLIDATED.version_id:
            records_from = FINAL.version_id
            basis = f"{FINAL.version_id} -> {vid}"
        else:
            records_from = vid
            basis = f"{PROPOSAL.version_id} -> {FINAL.version_id}"
        versions.append(
            {
                "version_id": vid,
                "label": spec.label,
                "celex": spec.celex,
                "date": spec.date.isoformat(),
                "status": spec.status,
                "file": spec.out_file,
                "delta_basis": basis,
                "obligations_from": records_from,
            }
        )
        for u in units[vid]:
            key = keys[vid][u.container]
            no_view = vid == CONSOLIDATED.version_id and (u.kind, u.number) in amended
            records = [] if no_view else obligations[records_from].get(key, [])
            rows.append(
                {
                    "key": key,
                    "version": vid,
                    "kind": "article" if u.kind == "art" else "annex",
                    "number": u.number,
                    "heading": u.title,
                    "delta": deltas[vid][key],
                    "obligations": obligation_counts(records),
                    "chars": len(u.text),
                }
            )
    return {"regulation_id": REGULATION_ID, "versions": versions, "rows": rows}


def index_line(row: Mapping[str, Any]) -> str:
    """One index row as a Planner reads it: key | [number] heading | delta | actor counts.

    The printed number is shown only where the key does not already end with it (crosswalk
    keys, renumbered proposal articles)."""
    label = "Art" if row["kind"] == "article" else "Annex"
    number = "" if row["key"].endswith(f"/{row['number']}") else f"{label} {row['number']} "
    parts = [row["key"], f"{number}{row['heading']}".strip(), row["delta"]]
    if row["obligations"]:
        parts.append(", ".join(f"{a} {n}" for a, n in row["obligations"].items()))
    return " | ".join(parts)


def index_sizes(index: Mapping[str, Any]) -> dict[str, int]:
    """Characters per version of the index as ``index_line`` renders it, one row per line."""
    sizes: Counter = Counter()
    for row in index["rows"]:
        sizes[row["version"]] += len(index_line(row)) + 1
    return dict(sizes)


def validate_corpus(
    versions: Mapping[str, RegulationVersion],
    index: Mapping[str, Any],
    obligations: Mapping[str, Mapping[str, list]],
) -> None:
    """Every index key and obligation key resolves to a provision of its version, and the index
    carries no text."""
    by_key = {vid: v.by_key() for vid, v in versions.items()}
    for row in index["rows"]:
        if "text" in row:
            raise FixtureError(f"index row {row['key']} carries text")
        if row["key"] not in by_key.get(row["version"], {}):
            raise FixtureError(f"index key {row['key']!r} is not a provision of {row['version']}")
    for vid, by in obligations.items():
        unknown = sorted(set(by) - set(by_key[vid]))
        if unknown:
            raise FixtureError(f"obligations of {vid} name unknown keys {unknown[:5]}")


# --- I/O ----------------------------------------------------------------------------------------


def fetch_upstream(url: str, *, accept: str = XHTML, refresh: bool) -> bytes:
    """``fetch`` with every network or HTTP failure reported as a FixtureError naming the URL."""
    try:
        return fetch(url, accept=accept, refresh=refresh)
    except (CellarError, httpx.HTTPError) as exc:
        raise FixtureError(f"download failed for {url}: {exc}") from None


def download_all(refresh: bool) -> dict[str, bytes]:
    bodies = {}
    for url in (*PIPELINE_UNITS.values(), PIPELINE_CONTAINERS, *PIPELINE_OBLIGATIONS.values()):
        bodies[url] = fetch_upstream(url, accept=ANY, refresh=refresh)
    for url in (CONSOLIDATED_URL, AMENDING_URL):
        bodies[url] = fetch_upstream(url, refresh=refresh)
    return bodies


def _dump_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_corpus(
    out_dir: Path,
    versions: Mapping[str, RegulationVersion],
    obligations: Mapping[str, Any],
    index: Mapping[str, Any],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for spec in VERSIONS:
        reg = Regulation(
            regulation_id=REGULATION_ID,
            title=REGULATION_TITLE,
            versions=[versions[spec.version_id]],
        )
        _dump_json(out_dir / spec.out_file, reg.model_dump(mode="json"))
    # One record (and one index row) per line: compact, with reviewable diffs.
    blocks = []
    for vid, by_key in obligations.items():
        keyed = [
            f"  {_compact(key)}: [\n   " + ",\n   ".join(_compact(r) for r in records) + "\n  ]"
            for key, records in by_key.items()
        ]
        blocks.append(f" {_compact(vid)}: {{\n" + ",\n".join(keyed) + "\n }")
    (out_dir / "obligations.json").write_text(
        "{\n" + ",\n".join(blocks) + "\n}\n", encoding="utf-8"
    )
    lines = [_compact(r) for r in index["rows"]]
    head = json.dumps(
        {"regulation_id": index["regulation_id"], "versions": index["versions"]},
        ensure_ascii=False,
        indent=1,
    )
    body = head[:-2] + ',\n "rows": [\n  ' + ",\n  ".join(lines) + "\n ]\n}\n"
    (out_dir / "index.json").write_text(body, encoding="utf-8")


@dataclass(frozen=True)
class Built:
    units: dict[str, list[Unit]]
    keys: dict[str, dict[str, str]]
    versions: dict[str, RegulationVersion]
    obligations: dict[str, dict[str, list[dict[str, Any]]]]
    stats: dict[str, ObligationStats]
    index: dict[str, Any]
    amended: tuple[list[str], list[str]]


def build(bodies: Mapping[str, bytes], crosswalk: Crosswalk) -> Built:
    units: dict[str, list[Unit]] = {}
    for version_id, url in PIPELINE_UNITS.items():
        parsed = parse_units.parse_units(bodies[url], url.rsplit("/", 1)[1])
        units[version_id] = units_from(
            parse_units.parse_articles(parsed), parse_units.parse_annexes(parsed)
        )
    pairs = parse_units.parse_containers(bodies[PIPELINE_CONTAINERS], PIPELINE_CONTAINERS)
    parse_units.check_crosswalk(crosswalk.entries, pairs, PROPOSAL.version_id, FINAL.version_id)

    try:
        consolidated = bodies[CONSOLIDATED_URL]
        marked = (
            parse_consolidated.amended_articles(consolidated, source=CONSOLIDATED_URL),
            parse_consolidated.amended_annexes(consolidated, source=CONSOLIDATED_URL),
        )
        amending = parse_consolidated.amending_act_targets(
            bodies[AMENDING_URL], source=AMENDING_URL
        )
        parsed_consolidated = units_from(
            parse_consolidated.parse_consolidated(consolidated, source=CONSOLIDATED_URL),
            parse_consolidated.parse_annexes(consolidated, source=CONSOLIDATED_URL),
        )
    except ConsolidatedParseError as exc:
        raise FixtureError(str(exc)) from None
    check_amended(marked, amending)
    amended = {("art", n) for n in marked[0]} | {("annex", n) for n in marked[1]}
    units[CONSOLIDATED.version_id] = consolidated_units(
        units[FINAL.version_id], parsed_consolidated, amended
    )

    keys = assign_keys(units, pairs, crosswalk)
    specs = {s.version_id: s for s in VERSIONS}
    versions = {vid: build_version(specs[vid], us, keys[vid]) for vid, us in units.items()}

    obligations: dict[str, dict[str, list[dict[str, Any]]]] = {}
    stats: dict[str, ObligationStats] = {}
    for version_id, url in PIPELINE_OBLIGATIONS.items():
        rows = parse_obligations(bodies[url], url.rsplit("/", 1)[1])
        obligations[version_id], stats[version_id] = build_obligations(
            rows, version_id, keys[version_id], set(marked[0])
        )

    deltas = delta_kinds(units, keys, amended)
    index = build_index(units, keys, deltas, obligations, amended)
    validate_corpus(versions, index, obligations)
    return Built(units, keys, versions, obligations, stats, index, marked)


def report(built: Built) -> list[str]:
    lines = []
    sizes = index_sizes(built.index)
    for spec in VERSIONS:
        us = built.units[spec.version_id]
        n_art = sum(u.kind == "art" for u in us)
        lines.append(
            f"{spec.version_id}: {n_art} articles + {len(us) - n_art} annexes, "
            f"{sum(len(u.text) for u in us)} chars, index {sizes[spec.version_id]} chars"
        )
    arts, anxs = built.amended
    lines.append(
        f"amended by {AMENDING_LABEL}: {len(arts)} articles, annexes {anxs} "
        "(markers agree with its Article 1)"
    )
    for vid, s in built.stats.items():
        lines.append(f"obligations {vid}: {s.line()}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_CORPUS_DIR)
    parser.add_argument("--crosswalk", type=Path, default=DEFAULT_FIXTURE_DIR / "crosswalk.yaml")
    parser.add_argument("--refresh", action="store_true", help="ignore the download cache")
    parser.add_argument(
        "--accept-upstream-changes",
        action="store_true",
        help="accept downloads whose sha256 differs from downloads.json",
    )
    args = parser.parse_args(argv)

    crosswalk = load_crosswalk(args.crosswalk)
    bodies = download_all(args.refresh)
    args.out.mkdir(parents=True, exist_ok=True)
    check_downloads(
        args.out / "downloads.json",
        bodies,
        accept=args.accept_upstream_changes,
        supersedes=PIPELINE_REPO_RAW,
    )
    built = build(bodies, crosswalk)
    write_corpus(args.out, built.versions, built.obligations, built.index)
    print("\n".join(report(built)))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FixtureError as exc:
        sys.exit(f"corpus build failed: {exc}")
