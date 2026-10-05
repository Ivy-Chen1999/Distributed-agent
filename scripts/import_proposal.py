"""Import a Commission proposal from EUR-Lex Cellar into data/fixtures/<regulation_id>/ (R33, R24).

    uv run python scripts/import_proposal.py <COM CELEX> [--id <regulation_id>] [--title <name>]
        [--refresh] [--accept-upstream-changes] [--ia-celex <CELEX> --ia-date <YYYY-MM-DD>
        --rsb-ref <SEC ref>]

The first import of a CELEX needs ``--id`` (e.g. ``52022PC0068 --id data_act``); later runs find
the fixture through its ``import.yaml``. The main document is resolved from Cellar (the CELEX
resource itself, or its ``DOC_n`` act item when Cellar answers HTTP 300), pinned by sha256 in
``downloads.json`` and cached in .cache/cellar/.

Every article becomes a provision with key ``<regulation_id>/proposal/art/<n>`` and its own
source. The explanatory memorandum is stripped of impact-assessment material by topic pattern
and must then pass the leak guard and the quantitative guard (``womm.data.memorandum``): every
kept sentence with an estimate, a percentage, a EUR amount or an "N out of ten" ratio must be
redacted or allow-listed in ``import.yaml``. When the accompanying IA is cached locally
(.cache/ia/<regulation_id>/), the memorandum must also not restate it (12-word n-gram overlap
with the IA's impact cut, ``womm.eval.ia_sources.ngram_overlap``). The import fails, naming the
CELEX, on zero articles, gaps or duplicate article numbers, an unrecognised memorandum or a
leak, and then writes nothing: a proposal is imported whole or not at all.

Per-proposal settings live in ``import.yaml`` (hand-editable, reviewed in PRs): extra strip
patterns, sentence redactions, leak-guard allow-list entries and the evaluation scenarios
(article sets) used by golden cases. No impact-assessment text is ever written to the
agent-visible fixture files; sources record only how many sentences were redacted in which
section. The reasons stay in ``import.yaml`` and the import log.

The accompanying IA's identifiers (``--ia-celex``, ``--ia-date``, ``--rsb-ref``) are written only
to the gitignored local index ``evals/private/ia_index.yaml`` (``womm.data.ia_index``), never to
``import.yaml``: they would reveal which proposals back the holdout.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml
from lxml import etree
from pydantic import Field, ValidationError

from womm.data import parse_proposal
from womm.data.cellar import CellarError, fetch_document, fetch_main_document
from womm.data.fixtures import (
    FIXTURES_ROOT,
    Fixture,
    FixtureError,
    check_downloads,
    validate_fixture,
)
from womm.data.ia_index import IA_FIELDS, IA_INDEX_PATH, IaRecord, load_ia_index, update_ia_index
from womm.data.memorandum import (
    Allow,
    QuantAllow,
    Redaction,
    leak_check_text,
    leak_guard,
    memorandum_sources,
    quant_guard,
    redact_sentences,
    strip_topics,
)
from womm.eval import ia_sources
from womm.models.base import StrictModel
from womm.models.regulation import Provision, Regulation, RegulationVersion, Scenario, Source

CONFIG_FILE = "import.yaml"
_COM_CELEX = re.compile(r"5(\d{4})PC(\d{4})")


class ImportFailed(RuntimeError):
    """The proposal cannot be imported; nothing was written."""


class ScenarioSpec(StrictModel):
    """No ``ia_reference``: the golden case names its IA; the public fixture must not."""

    scenario_id: str = Field(pattern=r"^eval_[a-z0-9_]+$")
    description: str = Field(description="Public text; checked by the leak guard.")
    articles: list[str] = Field(min_length=1)


class RedactionSpec(StrictModel):
    section: str = Field(min_length=1, description="Full heading or number of a kept section.")
    sentence: str = Field(min_length=1, description="Exact sentence or a unique prefix of it.")
    reason: str = Field(
        min_length=1,
        description="Why the sentence states or cites the IA; kept here and in the import log, "
        "never in sources.json.",
    )


class AllowSpec(StrictModel):
    section: str = Field(min_length=1, description="Full heading or number of a kept section.")
    text: str = Field(
        min_length=1,
        description="Exact phrase, occurring once in that section, with a qualifying word "
        "beyond the marker (e.g. 'results of a dedicated impact assessment').",
    )


class QuantAllowSpec(StrictModel):
    section: str = Field(min_length=1, description="Full heading or number of a kept section.")
    sentence: str = Field(min_length=1, description="Exact sentence or a unique prefix of it.")
    reason: str = Field(
        min_length=1,
        description="Why the figure is not an IA finding (e.g. a threshold the proposal sets); "
        "kept here only, never in sources.json. Must not restate an IA conclusion.",
    )


class ImportConfig(StrictModel):
    celex: str = Field(pattern=_COM_CELEX.pattern)
    regulation_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_]*$")
    title: str = Field(default="", description="Default: the act's short name on the cover.")
    document_url: str | None = Field(
        default=None, description="Resolved main document (Cellar CELEX or DOC_n item URL)."
    )
    date: dt.date | None = Field(default=None, description="Overrides the cover-page date.")
    strip_patterns: dict[str, str] = Field(
        default_factory=dict, description="Extra memorandum topics to strip: name -> regex."
    )
    redactions: list[RedactionSpec] = Field(
        default_factory=list, description="Sentences removed from kept memorandum sections."
    )
    leak_allow: list[AllowSpec] = Field(
        default_factory=list,
        description="Section-scoped phrases (not about this IA) the leak guard ignores.",
    )
    quant_allow: list[QuantAllowSpec] = Field(
        default_factory=list,
        description="Kept sentences with an estimate, percentage, EUR amount or ratio that were "
        "reviewed as not restating the IA.",
    )
    scenarios: list[ScenarioSpec] = Field(default_factory=list)


@dataclass(frozen=True)
class Imported:
    regulation: Regulation
    sources: list[Source]
    scenarios: list[Scenario]
    stripped: list[str]
    topics: dict[str, list[str]]
    redactions: dict[str, list[str]]


def version_ids(celex: str) -> tuple[str, str]:
    """('com2022_68', 'COM(2022) 68') for CELEX 52022PC0068."""
    m = _COM_CELEX.fullmatch(celex)
    if not m:
        raise ImportFailed(f"{celex}: not a COM proposal CELEX (5YYYYPCNNNN)")
    year, number = m.group(1), int(m.group(2))
    return f"com{year}_{number}", f"COM({year}) {number}"


def provision_key(regulation_id: str, article: str) -> str:
    return f"{regulation_id}/proposal/art/{article}"


def build(config: ImportConfig, body: bytes) -> Imported:
    """Everything the fixture holds, from the document alone; raises ImportFailed."""
    celex = config.celex
    version_id, label = version_ids(celex)
    try:
        root = parse_proposal.parse_document(body)
        articles = parse_proposal.parse_articles(root)
        parse_proposal.check_article_sequence(articles, celex)
        stripped = strip_topics(
            parse_proposal.parse_memorandum(root),
            extra_patterns=config.strip_patterns,
            label=celex,
        )
        redacted = redact_sentences(
            stripped.kept,
            [Redaction(r.section, r.sentence, r.reason) for r in config.redactions],
            label=celex,
        )
        leak_guard(
            redacted.kept, allow=[Allow(a.section, a.text) for a in config.leak_allow], label=celex
        )
        quant_guard(
            redacted.kept,
            allow=[QuantAllow(q.section, q.sentence, q.reason) for q in config.quant_allow],
            label=celex,
        )
        for spec in config.scenarios:
            leak_check_text(spec.description, what=f"scenario {spec.scenario_id!r}", label=celex)
    except (ValueError, etree.LxmlError) as exc:  # incl. MemorandumError
        raise ImportFailed(str(exc) if celex in str(exc) else f"{celex}: {exc}") from None

    cover = parse_proposal.parse_cover(root)
    issued = config.date or cover.issued
    if issued is None:
        raise ImportFailed(f"{celex}: no issue date on the cover page; set 'date' in import.yaml")
    title = config.title.strip() or cover.short_title or cover.subject
    if not title:
        raise ImportFailed(f"{celex}: no title on the cover page; pass --title")

    provisions, sources = [], []
    for a in articles:
        sid = f"{version_id}/art_{a.number}"
        provisions.append(
            Provision(
                provision_key=provision_key(config.regulation_id, a.number),
                article=a.number,
                paragraph=None,
                text=a.text,
                source_id=sid,
            )
        )
        heading = f"{label}, Article {a.number}" + (f": {a.title}" if a.title else "")
        sources.append(Source(source_id=sid, title=heading, kind="provision", text=a.text))
    sources.extend(
        memorandum_sources(
            redacted.kept,
            stripped.removed,
            version_id=version_id,
            label=label,
            redactions=redacted.records,
        )
    )
    version = RegulationVersion(
        version_id=version_id, date=issued, status="proposal", source=celex, provisions=provisions
    )
    regulation = Regulation(regulation_id=config.regulation_id, title=title, versions=[version])

    numbers = {a.number for a in articles}
    scenarios = []
    for spec in config.scenarios:
        unknown = [n for n in spec.articles if n not in numbers]
        if unknown:
            raise ImportFailed(f"{celex}: scenario {spec.scenario_id!r} cites missing {unknown}")
        scenarios.append(
            Scenario(
                scenario_id=spec.scenario_id,
                kind="evaluation",
                description=spec.description,
                before_version=None,
                after_version=version_id,
                provision_keys=[provision_key(config.regulation_id, n) for n in spec.articles],
            )
        )
    try:
        validate_fixture(
            Fixture(
                regulation,
                {s.source_id: s for s in sources},
                {s.scenario_id: s for s in scenarios},
            )
        )
    except FixtureError as exc:
        raise ImportFailed(f"{celex}: {exc}") from None
    return Imported(
        regulation, sources, scenarios, stripped.removed, stripped.topics, redacted.records
    )


def check_ia_overlap(imported: Imported, regulation_id: str, ia_root: Path | None = None) -> bool:
    """When the accompanying IA is cached locally, fail if a memorandum source restates its
    impact cut (12-word n-grams). Returns whether the check ran (False: no cached IA)."""
    try:
        cached = ia_sources.load_cached_ia(regulation_id, ia_root)
    except ia_sources.IaSourceError as exc:
        raise ImportFailed(f"{regulation_id}: cannot read the cached IA: {exc}") from None
    if cached is None:
        return False
    for source in imported.sources:
        if source.kind != "memorandum":
            continue
        hit = ia_sources.restates_ia(source.text, cached.cut_text)
        if hit:
            raise ImportFailed(
                f"{imported.regulation.versions[0].source}: {source.source_id} restates the "
                f"cached IA ({hit[0]:.1%} of its 12-grams, a {hit[1]}-word run); strip or "
                "redact the passage in import.yaml"
            )
    return True


def _dump_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_fixture(
    out_dir: Path,
    config: ImportConfig,
    imported: Imported,
    bodies: dict[str, bytes],
    *,
    accept: bool,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    check_downloads(out_dir / "downloads.json", bodies, accept=accept)
    _dump_json(out_dir / "proposal.json", imported.regulation.model_dump(mode="json"))
    _dump_json(out_dir / "sources.json", [s.model_dump(mode="json") for s in imported.sources])
    header = (
        f"# Generated by scripts/import_proposal.py from {CONFIG_FILE}; edit the scenarios\n"
        "# there, then re-import. No impact-assessment text belongs in data/fixtures.\n"
    )
    body = yaml.safe_dump(
        {
            "scenarios": [
                s.model_dump(mode="json", exclude={"ia_reference"}) for s in imported.scenarios
            ]
        },
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    (out_dir / "scenarios.yaml").write_text(header + body, encoding="utf-8")
    config_header = (
        f"# Import settings for {config.celex}; rerun scripts/import_proposal.py after editing.\n"
        "# strip_patterns / redactions / leak_allow / quant_allow are reviewed exceptions to the\n"
        "# memorandum leak guards (reasons stay here, never in sources.json).\n"
        "# IA identifiers live only in the gitignored evals/private/ia_index.yaml.\n"
    )
    config_body = yaml.safe_dump(
        config.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100
    )
    (out_dir / CONFIG_FILE).write_text(config_header + config_body, encoding="utf-8")


def find_config(celex: str, root: Path) -> tuple[Path, ImportConfig] | None:
    for path in sorted(root.glob(f"*/{CONFIG_FILE}")):
        config = load_config(path)
        if config.celex == celex:
            return path.parent, config
    return None


def load_config(path: Path) -> ImportConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and (public := [k for k in IA_FIELDS if k in raw]):
            raise ImportFailed(
                f"cannot load {path}: {public} must not be in a public import.yaml; "
                "pass them as --ia-celex/--ia-date/--rsb-ref (evals/private/ia_index.yaml)"
            )
        return ImportConfig.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise ImportFailed(f"cannot load {path}: {exc}") from None


def ia_record(args: argparse.Namespace, config: ImportConfig) -> IaRecord | None:
    """The IA entry to store for this import, merged over the existing one; None if no flags."""
    given = {k: getattr(args, k) for k in IA_FIELDS if getattr(args, k) is not None}
    if not given:
        return None
    try:
        old = load_ia_index(args.ia_index).get(config.regulation_id)
        base = old.model_dump() if old and old.celex == config.celex else {}
        return IaRecord.model_validate({**base, **given, "celex": config.celex})
    except ValueError as exc:  # IaIndexError, ValidationError
        raise ImportFailed(f"{config.celex}: bad IA identifiers: {exc}") from None


def resolve_config(args: argparse.Namespace) -> tuple[Path, ImportConfig]:
    version_ids(args.celex)  # fail early on anything but a COM proposal
    found = find_config(args.celex, args.root)
    if found:
        out_dir, config = found
        if args.id and args.id != config.regulation_id:
            raise ImportFailed(
                f"{args.celex} is already imported as {config.regulation_id!r} ({out_dir})"
            )
        if args.title:
            config = config.model_copy(update={"title": args.title})
        return out_dir, config
    if not args.id:
        raise ImportFailed(f"{args.celex}: first import needs --id <regulation_id>")
    out_dir = args.root / args.id
    if out_dir.exists():
        raise ImportFailed(f"{out_dir} exists but has no {CONFIG_FILE}; refusing to overwrite it")
    try:
        config = ImportConfig(celex=args.celex, regulation_id=args.id, title=args.title or "")
    except ValidationError as exc:
        raise ImportFailed(f"{args.celex}: {exc}") from None
    return out_dir, config


def pinned_hash(out_dir: Path, url: str) -> str | None:
    """The sha256 pinned for ``url`` in ``downloads.json``, if any."""
    path = out_dir / "downloads.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(url) if path.exists() else None
    except (OSError, json.JSONDecodeError, AttributeError) as exc:
        raise ImportFailed(f"cannot read {path}: {exc}") from None


def download(config: ImportConfig, out_dir: Path, *, refresh: bool) -> tuple[str, bytes]:
    try:
        if config.document_url:
            # Read the cached manifestation the fixture was built from, not XHTML blindly.
            body = fetch_document(
                config.document_url,
                refresh=refresh,
                expected_sha256=pinned_hash(out_dir, config.document_url),
            )
            return config.document_url, body
        return fetch_main_document(config.celex, refresh=refresh)
    except (CellarError, httpx.HTTPError) as exc:
        raise ImportFailed(f"{config.celex}: download failed: {exc}") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("celex", help="CELEX of the COM proposal, e.g. 52022PC0068")
    parser.add_argument("--id", help="regulation_id / fixture directory (first import only)")
    parser.add_argument("--title", help="regulation title (default: the act's short name)")
    parser.add_argument("--root", type=Path, default=FIXTURES_ROOT, help=argparse.SUPPRESS)
    parser.add_argument("--refresh", action="store_true", help="ignore the download cache")
    parser.add_argument(
        "--accept-upstream-changes",
        action="store_true",
        help="accept a download whose sha256 differs from downloads.json",
    )
    parser.add_argument("--ia-celex", help="CELEX of the accompanying IA SWD (local index only)")
    parser.add_argument("--ia-date", help="publication date of the IA, YYYY-MM-DD (local only)")
    parser.add_argument("--rsb-ref", help="SEC reference of the RSB opinion (local index only)")
    parser.add_argument("--ia-index", type=Path, default=IA_INDEX_PATH, help=argparse.SUPPRESS)
    parser.add_argument("--ia-root", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    out_dir, config = resolve_config(args)
    ia = ia_record(args, config)  # validated before anything is downloaded or written
    url, body = download(config, out_dir, refresh=args.refresh)
    imported = build(config, body)
    overlap_checked = check_ia_overlap(imported, config.regulation_id, args.ia_root)
    config = config.model_copy(update={"document_url": url, "title": imported.regulation.title})
    try:
        write_fixture(out_dir, config, imported, {url: body}, accept=args.accept_upstream_changes)
    except FixtureError as exc:
        raise ImportFailed(f"{config.celex}: {exc}") from None
    if ia is not None:
        update_ia_index(config.regulation_id, ia, args.ia_index)

    version = imported.regulation.versions[0]
    memo = [s for s in imported.sources if s.kind == "memorandum"]
    print(f"{config.celex} -> {out_dir} ({config.regulation_id}, {config.title})")
    print(f"document: {url}")
    print(f"articles: {len(version.provisions)} (1..{version.provisions[-1].article})")
    print(f"memorandum sources: {[s.source_id for s in memo]}")
    print(f"stripped: {imported.stripped}")
    redacted = sum(len(v) for v in imported.redactions.values())
    print(f"redacted sentences: {redacted}")
    for heading, reasons in imported.redactions.items():  # log only; never in sources.json
        for reason in reasons:
            print(f"  {heading}: {reason}")
    print("leak guard: clean; quantitative guard: clean")
    print(
        "IA overlap: clean (12-grams vs the cached IA)"
        if overlap_checked
        else "IA overlap: not checked (no IA cached under .cache/ia/)"
    )
    if ia is not None:
        print(f"IA identifiers: written to {args.ia_index} (local only)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ImportFailed as exc:
        sys.exit(f"import failed: {exc}")
