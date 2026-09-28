"""Build data/fixtures/ai_act/ from EUR-Lex Cellar.

    uv run python scripts/build_fixture.py [--refresh]

Downloads COM(2021) 206 (proposal + explanatory memorandum, Cellar DOC_1), keeps only the
articles used by scenarios, strips impact-assessment material from the memorandum and writes
proposal.json, sources.json and scenarios.yaml. Downloads are cached in .cache/cellar/.

No text from the impact assessment SWD(2021) 84 is ever written here: scenarios only carry a
section reference (``ia_reference``) so the golden cases (evals/golden/) can be aligned.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import yaml

from womm.data.cellar import fetch
from womm.data.fixtures import DEFAULT_FIXTURE_DIR, Fixture, validate_fixture
from womm.data.parse_proposal import (
    Article,
    MemorandumSection,
    parse_articles,
    parse_document,
    parse_memorandum,
    strip_sections,
)
from womm.models.regulation import Provision, Regulation, RegulationVersion, Scenario, Source

# celex/52021PC0206 answers HTTP 300; DOC_1 is the proposal with its explanatory memorandum
# (DOC_3 holds the annexes, not used yet).
PROPOSAL_CELEX = "52021PC0206"
PROPOSAL_URL = (
    "http://publications.europa.eu/resource/cellar/"
    "e0649735-a372-11eb-9585-01aa75ed71a1.0001.03/DOC_1"
)
PROPOSAL_VERSION = "com2021_206"
PROPOSAL_DATE = dt.date(2021, 4, 21)
PROPOSAL_LABEL = "COM(2021) 206"

# Proposal article -> provision key. Keys name the legal concept, not the number, so the final
# Regulation (EU) 2024/1689 maps onto them through crosswalk.yaml (e.g. Art 71 -> Art 99).
PROPOSAL_KEYS: dict[str, str] = {
    "8": "ai_act/high_risk/compliance_with_requirements",
    "9": "ai_act/high_risk/risk_management",
    "10": "ai_act/high_risk/data_governance",
    "11": "ai_act/high_risk/technical_documentation",
    "12": "ai_act/high_risk/record_keeping",
    "13": "ai_act/high_risk/transparency_to_users",
    "14": "ai_act/high_risk/human_oversight",
    "15": "ai_act/high_risk/accuracy_robustness_cybersecurity",
    "16": "ai_act/high_risk/provider_obligations",
    "17": "ai_act/high_risk/quality_management_system",
    "43": "ai_act/high_risk/conformity_assessment",
    "53": "ai_act/innovation/regulatory_sandboxes",
    "54": "ai_act/innovation/sandbox_personal_data",
    "55": "ai_act/innovation/sme_measures",
    "71": "ai_act/penalties/penalties",
}

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
        "after_version": PROPOSAL_VERSION,
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
        "after_version": PROPOSAL_VERSION,
        "articles": ["53", "54", "55", "71"],
        "ia_reference": "SWD(2021) 84 Part 1, section 6.1.4 (SME test)",
    },
]


def article_source_id(version_id: str, article: str) -> str:
    return f"{version_id}/art_{article}"


def build_proposal(doc: bytes) -> tuple[RegulationVersion, list[Source]]:
    root = parse_document(doc)
    articles: dict[str, Article] = {a.number: a for a in parse_articles(root)}
    used = sorted({a for s in SCENARIOS for a in s["articles"]}, key=int)

    unknown = [a for a in used if a not in PROPOSAL_KEYS]
    if unknown:
        raise SystemExit(f"scenario articles without a provision key: {unknown}")
    missing = [a for a in used if a not in articles]
    if missing:
        raise SystemExit(f"articles not found in {PROPOSAL_LABEL}: {missing}")

    provisions, sources = [], []
    for number in used:
        art = articles[number]
        sid = article_source_id(PROPOSAL_VERSION, number)
        provisions.append(
            Provision(
                provision_key=PROPOSAL_KEYS[number],
                article=number,
                paragraph=None,
                text=art.text,
                source_id=sid,
            )
        )
        sources.append(
            Source(
                source_id=sid,
                title=f"{PROPOSAL_LABEL}, Article {number}: {art.title}",
                kind="provision",
                text=art.text,
            )
        )

    sources.extend(memorandum_sources(parse_memorandum(root)))
    version = RegulationVersion(
        version_id=PROPOSAL_VERSION,
        date=PROPOSAL_DATE,
        status="proposal",
        source=PROPOSAL_CELEX,
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
                source_id=f"{PROPOSAL_VERSION}/memorandum/{slug}",
                title=(
                    f"{PROPOSAL_LABEL}, Explanatory memorandum, "
                    f"{top.number} {top.heading.capitalize()}"
                ),
                kind="memorandum",
                text="\n\n".join(lines),
                stripped_sections=removed,
            )
        )
    return sources


def build_scenarios(version: RegulationVersion) -> list[Scenario]:
    key_of = {p.article: p.provision_key for p in version.provisions}
    out = []
    for raw in SCENARIOS:
        data = {k: v for k, v in raw.items() if k != "articles"}
        data["provision_keys"] = [key_of[a] for a in raw["articles"]]
        out.append(Scenario.model_validate(data))
    return out


def write_fixture(
    out_dir: Path, regulation: Regulation, sources: list[Source], scenarios: list[Scenario]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    def dump_json(name: str, payload) -> None:
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        (out_dir / name).write_text(text, encoding="utf-8")

    dump_json("proposal.json", regulation.model_dump(mode="json"))
    dump_json("sources.json", [s.model_dump(mode="json") for s in sources])
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--refresh", action="store_true", help="ignore the download cache")
    args = parser.parse_args(argv)

    version, sources = build_proposal(fetch(PROPOSAL_URL, refresh=args.refresh))
    regulation = Regulation(
        regulation_id="ai_act",
        title="Artificial Intelligence Act",
        versions=[version],
    )
    scenarios = build_scenarios(version)
    validate_fixture(
        Fixture(
            regulation,
            {s.source_id: s for s in sources},
            {s.scenario_id: s for s in scenarios},
        )
    )
    write_fixture(args.out, regulation, sources, scenarios)

    by_key = version.by_key()
    for s in scenarios:
        chars = sum(len(by_key[k].text) for k in s.provision_keys)
        print(f"{s.scenario_id}: {len(s.provision_keys)} provisions, {chars} chars")
    memo = [s for s in sources if s.kind == "memorandum"]
    print(f"memorandum sources: {[s.source_id for s in memo]}")
    print(f"stripped: {memo[0].stripped_sections if memo else []}")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
