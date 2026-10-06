"""Build data/fixtures/ai_act/ from the colleague's provision units and EUR-Lex Cellar.

    uv run python scripts/build_fixture.py [--articles-from pipeline|cellar] [--refresh]

Article texts come from the colleague's data pipeline (calderonsamuel/course-cs-project-fall-2026-
data, pinned to PIPELINE_COMMIT): the provision units of COM(2021) 206 and Regulation (EU)
2024/1689, rendered by ``womm.data.parse_units``. Their container alignment must pair every
article of the hand-maintained crosswalk.yaml, or the build fails. ``--articles-from cellar``
parses the articles from Cellar instead (the pre-2026-10-02 source), and either way the other
source's text is compared per article when it is cached.

The explanatory memorandum always comes from Cellar DOC_1 (the colleague's data has none); its
impact-assessment material is stripped. Only articles used by scenarios are kept, resolved to
stable provision keys through crosswalk.yaml. Writes proposal.json, final.json, sources.json and
scenarios.yaml; every download is pinned by sha256 in downloads.json and cached in .cache/cellar/.
Runtime code never reads the upstream files, only this fixture.

No text from the impact assessment SWD(2021) 84 is ever written here: scenarios only carry a
section reference (``ia_reference``) so the golden cases (evals/golden/) can be aligned.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

from womm.citations import normalize
from womm.data import parse_proposal, parse_regulation, parse_units
from womm.data.cellar import XHTML, CellarError, cached, celex_url, fetch
from womm.data.fixtures import (
    DEFAULT_FIXTURE_DIR,
    Crosswalk,
    Fixture,
    FixtureError,
    check_downloads,
    load_crosswalk,
    validate_fixture,
)
from womm.data.parse_proposal import Article, MemorandumSection, strip_sections
from womm.models.regulation import Provision, Regulation, RegulationVersion, Scenario, Source


@dataclass(frozen=True)
class VersionSpec:
    version_id: str
    label: str
    celex: str
    date: dt.date
    status: str
    out_file: str


PROPOSAL = VersionSpec(
    "com2021_206",
    "COM(2021) 206",
    "52021PC0206",
    dt.date(2021, 4, 21),
    "proposal",
    "proposal.json",
)
FINAL = VersionSpec(
    "reg2024_1689",
    "Regulation (EU) 2024/1689",
    "32024R1689",
    dt.date(2024, 6, 13),
    "adopted",
    "final.json",
)
# celex/52021PC0206 answers HTTP 300; DOC_1 is the proposal with its explanatory memorandum
# (DOC_3 holds the annexes, not used yet). celex/32024R1689 answers 200 directly.
PROPOSAL_URL = (
    "https://publications.europa.eu/resource/cellar/"
    "e0649735-a372-11eb-9585-01aa75ed71a1.0001.03/DOC_1"
)

# The colleague's data pipeline, pinned by commit (moving the pin changes downloads.json and
# needs --accept-upstream-changes).
PIPELINE_COMMIT = "16b5807927be1ade3802db7c59c26f7ca338ebf2"
PIPELINE_REPO_RAW = (
    "https://raw.githubusercontent.com/calderonsamuel/course-cs-project-fall-2026-data/"
)
PIPELINE_BASE = f"{PIPELINE_REPO_RAW}{PIPELINE_COMMIT}/data/processed"
PIPELINE_UNITS = {
    PROPOSAL.version_id: f"{PIPELINE_BASE}/provisions/{PROPOSAL.celex}.jsonl",
    FINAL.version_id: f"{PIPELINE_BASE}/provisions/{FINAL.celex}.jsonl",
}
PIPELINE_CONTAINERS = f"{PIPELINE_BASE}/alignment/{PROPOSAL.celex}__{FINAL.celex}_containers.csv"
ANY = "*/*"

# Explanatory memorandum sections removed before the text becomes a citable source (R9, R24).
# The golden cases are scored against SWD(2021) 84, so anything that restates its findings would
# leak the answer:
# - "Results of ex-post evaluations, stakeholder consultations and impact assessments" (section
#   3, with all subsections): 3.3 summarises the IA options and preferred option, 3.1/3.2
#   report consultation results feeding the IA, and 3.4 / 3.5 restate the IA's conclusions on
#   burden for companies/administrations and on fundamental-rights impacts.
# - "Proportionality" (2.3): asserts that operator costs are proportionate and minimised, i.e.
#   the IA's cost conclusion.
# - "Budgetary implications" (4): quotes the IA's public-sector estimate (1-25 FTE per Member
#   State) and points to the financial statement.
# Kept: context/objectives (1), legal basis, subsidiarity, choice of instrument (2.1, 2.2, 2.4)
# and the article-by-article explanation (5), which describe what the law does, not its
# assessed impacts. The legislative financial statement after the articles is never used.
MEMORANDUM_STRIP = {
    "Results of ex-post evaluations, stakeholder consultations and impact assessments",
    "Proportionality",
    "Budgetary implications",
}
# Top-level memorandum section number -> readable source_id suffix.
MEMORANDUM_SLUGS = {"1": "context", "2": "legal_basis", "5": "other_elements"}

# The AI Act as consolidated on 27 July 2026 (after Regulation (EU) 2026/1744). It lives only in
# the provision corpus (scripts/build_corpus.py), which explore scenarios read.
CONSOLIDATED_VERSION_ID = "reg2024_1689_c20260727"

# Preset scenarios: ``articles`` are article numbers in ``after_version``; they become provision
# keys through crosswalk.yaml. Explore scenarios (``mode: explore``) list no articles: they read
# whole versions from the provision corpus and the Planner chooses provisions from its index.
SCENARIOS: list[dict] = [
    {
        "scenario_id": "eval_provider_compliance_costs",
        "kind": "evaluation",
        "description": (
            "Proposal as a whole-new text: requirements for high-risk AI systems (Title III "
            "Chapter 2, Art 8-15) and the core provider obligations (Art 16, quality management "
            "system Art 17, conformity assessment Art 43). Who bears which compliance costs and "
            "administrative burdens?"
        ),
        "before_version": None,
        "after_version": PROPOSAL.version_id,
        "articles": ["8", "9", "10", "11", "12", "13", "14", "15", "16", "17", "43"],
        "ia_reference": "SWD(2021) 84 Part 1, section 6.1.3 (costs and administrative burdens)",
    },
    {
        "scenario_id": "eval_sme_impacts",
        "kind": "evaluation",
        "description": (
            "Proposal as a whole-new text: AI regulatory sandboxes (Art 53-54), measures for "
            "small-scale providers and users (Art 55) and penalties (Art 71). How are SMEs and "
            "start-ups affected?"
        ),
        "before_version": None,
        "after_version": PROPOSAL.version_id,
        "articles": ["53", "54", "55", "71"],
        "ia_reference": "SWD(2021) 84 Part 1, section 6.1.4 (SME test)",
    },
    {
        "scenario_id": "demo_penalties_amended",
        "kind": "demo",
        "description": (
            "Proposal -> adopted Regulation for provider obligations (Art 16 -> 16), SME "
            "measures (Art 55 -> 62) and penalties (Art 71 -> 99). Shows the provision-level "
            "diff across renumbering; not scored against the impact assessment."
        ),
        "before_version": PROPOSAL.version_id,
        "after_version": FINAL.version_id,
        "articles": ["16", "62", "99"],
        "ia_reference": None,
    },
    {
        "scenario_id": "eval_whole_proposal",
        "kind": "evaluation",
        "mode": "explore",
        "description": (
            "The whole proposal COM(2021) 206 as a new text. The Planner chooses the provisions "
            "to study from the index of every article and annex. Which actors bear which "
            "compliance costs and administrative burdens, and how are SMEs and start-ups "
            "affected?"
        ),
        "before_version": None,
        "after_version": PROPOSAL.version_id,
        "ia_reference": "SWD(2021) 84 Part 1, sections 6.1.3 and 6.1.4",
    },
    {
        "scenario_id": "consolidated_whole_act",
        "kind": "demo",
        "mode": "explore",
        "description": (
            "The AI Act as consolidated on 27 July 2026, the law in force, analysed as a whole. "
            "The Planner chooses the provisions to study from the index of every article and "
            "annex."
        ),
        "before_version": None,
        "after_version": CONSOLIDATED_VERSION_ID,
        "ia_reference": None,
    },
    {
        "scenario_id": "omnibus_2026",
        "kind": "demo",
        "mode": "explore",
        "description": (
            "Regulation (EU) 2024/1689 as adopted -> as consolidated on 27 July 2026: the "
            "changes made by Regulation (EU) 2026/1744. The Planner chooses the provisions to "
            "study from the index of the amended and inserted articles and annexes."
        ),
        "before_version": FINAL.version_id,
        "after_version": CONSOLIDATED_VERSION_ID,
        "ia_reference": None,
    },
]


def article_source_id(version_id: str, article: str) -> str:
    return f"{version_id}/art_{article}"


def build_scenarios(crosswalk: Crosswalk) -> list[Scenario]:
    out = []
    for raw in SCENARIOS:
        data = {k: v for k, v in raw.items() if k != "articles"}
        if raw.get("mode", "preset") == "preset":
            data["provision_keys"] = crosswalk.resolve(
                raw["scenario_id"], raw["after_version"], raw["articles"]
            )
        out.append(Scenario.model_validate(data))
    return out


def keys_needed(scenarios: list[Scenario], version_id: str) -> list[str]:
    """Keys of every scenario that reads ``version_id``, in first-use order."""
    keys: dict[str, None] = {}
    for s in scenarios:
        if version_id in (s.before_version, s.after_version):
            keys.update(dict.fromkeys(s.provision_keys))
    return list(keys)


def build_version(
    spec: VersionSpec,
    articles: list[Article],
    keys: list[str],
    crosswalk: Crosswalk,
) -> tuple[RegulationVersion, list[Source]]:
    by_number = {a.number: a for a in articles}
    provisions, sources = [], []
    for key in keys:
        number = crosswalk.article_for(key, spec.version_id)
        if number is None:
            continue  # provision absent from this version (added or deleted)
        if number not in by_number:
            raise FixtureError(f"{key!r}: Art {number} not found in {spec.label}")
        art = by_number[number]
        sid = article_source_id(spec.version_id, number)
        provisions.append(
            Provision(
                provision_key=key, article=number, paragraph=None, text=art.text, source_id=sid
            )
        )
        sources.append(
            Source(
                source_id=sid,
                title=f"{spec.label}, Article {number}: {art.title}",
                kind="provision",
                text=art.text,
            )
        )
    provisions.sort(key=lambda p: int(p.article))
    sources.sort(key=lambda s: int(s.source_id.rsplit("_", 1)[1]))
    version = RegulationVersion(
        version_id=spec.version_id,
        date=spec.date,
        status=spec.status,
        source=spec.celex,
        provisions=provisions,
    )
    return version, sources


def memorandum_sources(sections: list[MemorandumSection]) -> list[Source]:
    """One source per kept top-level memorandum section; each records the full strip list."""
    kept, removed = strip_sections(sections, MEMORANDUM_STRIP)
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
        slug = MEMORANDUM_SLUGS.get(number, f"section_{number}")
        sources.append(
            Source(
                source_id=f"{PROPOSAL.version_id}/memorandum/{slug}",
                title=(
                    f"{PROPOSAL.label}, Explanatory memorandum, "
                    f"{top.number} {top.heading.capitalize()}"
                ),
                kind="memorandum",
                text="\n\n".join(lines),
                stripped_sections=removed,
            )
        )
    return sources


def _dump_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_fixture(
    out_dir: Path,
    versions: list[tuple[VersionSpec, RegulationVersion]],
    sources: list[Source],
    scenarios: list[Scenario],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for spec, version in versions:
        reg = Regulation(
            regulation_id="ai_act", title="Artificial Intelligence Act", versions=[version]
        )
        _dump_json(out_dir / spec.out_file, reg.model_dump(mode="json"))
    _dump_json(out_dir / "sources.json", [s.model_dump(mode="json") for s in sources])
    header = (
        "# Generated by scripts/build_fixture.py; edit SCENARIOS there, then rebuild.\n"
        "# ia_reference only points at the SWD(2021) 84 section used by the golden case;\n"
        "# no impact-assessment text belongs in data/fixtures.\n"
    )
    body = yaml.safe_dump(
        {"scenarios": [s.model_dump(mode="json") for s in scenarios]},
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    (out_dir / "scenarios.yaml").write_text(header + body, encoding="utf-8")


def fetch_upstream(url: str, *, accept: str = XHTML, refresh: bool) -> bytes:
    """``fetch`` with every network or HTTP failure reported as a FixtureError naming the URL."""
    try:
        return fetch(url, accept=accept, refresh=refresh)
    except (CellarError, httpx.HTTPError) as exc:
        raise FixtureError(f"download failed for {url}: {exc}") from None


def cellar_articles(bodies: dict[str, bytes]) -> dict[str, list[Article]]:
    return {
        PROPOSAL.version_id: parse_proposal.parse_articles(
            parse_proposal.parse_document(bodies[PROPOSAL_URL])
        ),
        FINAL.version_id: parse_regulation.parse_articles(
            parse_regulation.parse_document(bodies[celex_url(FINAL.celex)])
        ),
    }


def pipeline_articles(bodies: dict[str, bytes]) -> dict[str, list[Article]]:
    out: dict[str, list[Article]] = {}
    for version_id, url in PIPELINE_UNITS.items():
        units = parse_units.parse_units(bodies[url], url.rsplit("/", 1)[1])
        out[version_id] = parse_units.parse_articles(units)
    return out


def fidelity_report(
    built: dict[str, RegulationVersion], other: dict[str, list[Article]], label: str
) -> list[str]:
    """One line per fixture article: byte-identical to the other source, identical only after
    citation normalization (layout drift), or the first differing context."""
    lines = []
    for version_id, version in built.items():
        by_number = {a.number: a.text for a in other.get(version_id, [])}
        for p in version.provisions:
            theirs = by_number.get(p.article)
            if theirs is None:
                verdict = f"missing in {label}"
            elif theirs == p.text:
                verdict = "byte-identical"
            elif normalize(theirs) == normalize(p.text):
                verdict = "same words, layout differs"
            else:
                a, b = normalize(p.text), normalize(theirs)
                i = next(
                    (i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y),
                    min(len(a), len(b)),
                )
                verdict = f"differs at {i}: {a[max(0, i - 30) : i + 30]!r} vs {b[max(0, i - 30) : i + 30]!r}"
            lines.append(f"  {version_id} Art {p.article}: {verdict}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument(
        "--articles-from",
        choices=("pipeline", "cellar"),
        default="pipeline",
        help="source of article texts (default: the colleague's pinned provision units)",
    )
    parser.add_argument("--refresh", action="store_true", help="ignore the download cache")
    parser.add_argument(
        "--accept-upstream-changes",
        action="store_true",
        help="accept downloads whose sha256 differs from downloads.json",
    )
    args = parser.parse_args(argv)

    crosswalk = load_crosswalk(args.out / "crosswalk.yaml")
    scenarios = build_scenarios(crosswalk)

    final_url = celex_url(FINAL.celex)
    bodies = {PROPOSAL_URL: fetch_upstream(PROPOSAL_URL, refresh=args.refresh)}
    if args.articles_from == "cellar":
        bodies[final_url] = fetch_upstream(final_url, refresh=args.refresh)
    else:
        for url in (*PIPELINE_UNITS.values(), PIPELINE_CONTAINERS):
            bodies[url] = fetch_upstream(url, accept=ANY, refresh=args.refresh)
    check_downloads(
        args.out / "downloads.json",
        bodies,
        accept=args.accept_upstream_changes,
        supersedes=PIPELINE_REPO_RAW if args.articles_from == "pipeline" else None,
    )

    if args.articles_from == "cellar":
        articles = cellar_articles(bodies)
    else:
        pairs = parse_units.parse_containers(bodies[PIPELINE_CONTAINERS], PIPELINE_CONTAINERS)
        parse_units.check_crosswalk(crosswalk.entries, pairs, PROPOSAL.version_id, FINAL.version_id)
        articles = pipeline_articles(bodies)

    built = []
    sources: list[Source] = []
    for spec in (PROPOSAL, FINAL):
        version, version_sources = build_version(
            spec, articles[spec.version_id], keys_needed(scenarios, spec.version_id), crosswalk
        )
        built.append((spec, version))
        sources.extend(version_sources)
    proposal_root = parse_proposal.parse_document(bodies[PROPOSAL_URL])
    sources.extend(memorandum_sources(parse_proposal.parse_memorandum(proposal_root)))

    regulation = Regulation(
        regulation_id="ai_act",
        title="Artificial Intelligence Act",
        versions=[v for _, v in built],
    )
    validate_fixture(
        Fixture(
            regulation, {s.source_id: s for s in sources}, {s.scenario_id: s for s in scenarios}
        )
    )
    write_fixture(args.out, built, sources, scenarios)

    by_version = {v.version_id: v.by_key() for _, v in built}
    for s in scenarios:
        if s.mode == "explore":
            print(f"{s.scenario_id}: explore, {s.before_version} -> {s.after_version} (corpus)")
            continue
        counts = []
        for vid in (s.before_version, s.after_version):
            if vid:
                chars = sum(len(by_version[vid][k].text) for k in s.provision_keys)
                counts.append(f"{vid}={chars}")
        print(f"{s.scenario_id}: {len(s.provision_keys)} provisions, chars {', '.join(counts)}")
    other = other_source_articles(args.articles_from, bodies)
    if other is None:
        print("fidelity: the other source is not cached; skipped")
    else:
        label = "cellar" if args.articles_from == "pipeline" else "pipeline"
        print(f"fidelity against {label}:")
        print("\n".join(fidelity_report({v.version_id: v for _, v in built}, other, label)))
    memo = [s for s in sources if s.kind == "memorandum"]
    print(f"memorandum sources: {[s.source_id for s in memo]}")
    print(f"stripped: {memo[0].stripped_sections if memo else []}")
    print(f"wrote {args.out}")
    return 0


def other_source_articles(
    current: str, bodies: dict[str, bytes]
) -> dict[str, list[Article]] | None:
    """Articles from the source not used for this build, from the cache only."""
    if current == "pipeline":
        url = celex_url(FINAL.celex)
        body = bodies.get(url) or cached(url)
        return cellar_articles({**bodies, url: body}) if body else None
    urls = (*PIPELINE_UNITS.values(),)
    found = {u: bodies.get(u) or cached(u, accept=ANY) for u in urls}
    if not all(found.values()):
        return None
    return pipeline_articles(found)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FixtureError as exc:
        sys.exit(f"fixture build failed: {exc}")
