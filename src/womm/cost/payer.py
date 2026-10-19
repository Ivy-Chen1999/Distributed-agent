"""Who pays: payer resolution for a cost record, and the payer's sector.

Order (never a silent guess):

1. ``rule_field``: the colleague's ``primary_actor``, when it names one;
2. ``rule_table``: our small table below, for passive duties whose payer the Act names elsewhere
   (every row cites that provision);
3. otherwise ``unknown``: the cost step may infer a payer, but only with a verbatim quote from
   the record's span (``inferred``, stored as agent output, never written back to the corpus).

An actor outside ``SECTOR`` (a new upstream value) resolves to ``unknown`` with a note, so a
run never fails on it; ``tests/cost/test_payer.py`` still fails until the map names it.

Stage A's LLM enrichment of unspecified actors (v1.1) replaces the table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from womm.data.corpus import Obligation
from womm.models.cost import PayerBasis, Sector

PROPOSAL, FINAL = "com2021_206", "reg2024_1689"

# Every actor category of the colleague's records, in both corpus versions. A new upstream value
# fails ``tests/cost/test_payer.py`` (and ``sector_for``), so it cannot slip through.
SECTOR: dict[str, Sector] = {
    "provider": "private",
    "gpai_provider": "private",
    "deployer": "private",
    "importer": "private",
    "distributor": "private",
    "authorised_representative": "private",
    "product_manufacturer": "private",
    "notified_body": "private",
    "operator": "private",
    "commission": "public",
    "ai_office": "public",
    "board": "public",
    "advisory_forum": "public",
    "scientific_panel": "public",
    "member_state": "public",
    "national_competent_authority": "public",
    "market_surveillance_authority": "public",
    "notifying_authority": "public",
    "public_authority": "public",
    "union_institutions": "public",
    "edps": "public",
}

_ANNEX = re.compile(r":anx([IVXLC]+)\b")
_UNSPECIFIED = (None, "", "unspecified")


@dataclass(frozen=True)
class PayerRule:
    version_id: str
    articles: frozenset[str]
    annexes: frozenset[str]
    payer: str
    legal_basis: str


def _arts(*numbers: int | str) -> frozenset[str]:
    return frozenset(str(n) for n in numbers)


PAYER_RULES: tuple[PayerRule, ...] = (
    PayerRule(
        PROPOSAL, _arts(*range(8, 16)), frozenset(), "provider",
        "Art 16(a) COM(2021) 206: providers ensure their high-risk AI systems comply with the "
        "requirements of Title III, Chapter 2 (Articles 8-15)",
    ),
    PayerRule(
        PROPOSAL, _arts(17), frozenset(), "provider",
        "Art 16(b) COM(2021) 206: providers have a quality management system complying with "
        "Article 17",
    ),
    PayerRule(
        PROPOSAL, frozenset(), frozenset({"IV"}), "provider",
        "Art 16(c) and Art 18(1) COM(2021) 206: providers draw up the technical documentation "
        "(Annex IV)",
    ),
    PayerRule(
        PROPOSAL, _arts(48), frozenset({"V"}), "provider",
        "Art 48(1) COM(2021) 206: the provider draws up the EU declaration of conformity "
        "(Annex V)",
    ),
    PayerRule(
        FINAL, _arts(*range(8, 16)), frozenset(), "provider",
        "Art 16(a) Regulation (EU) 2024/1689: providers ensure their high-risk AI systems comply "
        "with the requirements of Chapter III, Section 2 (Articles 8-15)",
    ),
    PayerRule(
        FINAL, _arts(17), frozenset(), "provider",
        "Art 16(c) Regulation (EU) 2024/1689: providers have a quality management system "
        "complying with Article 17",
    ),
    PayerRule(
        FINAL, frozenset(), frozenset({"IV"}), "provider",
        "Art 16(d) and Art 18(1)(a) Regulation (EU) 2024/1689: providers keep the technical "
        "documentation (Article 11, Annex IV)",
    ),
    PayerRule(
        FINAL, _arts(47), frozenset({"V"}), "provider",
        "Art 16(g) and Art 47(1) Regulation (EU) 2024/1689: the provider draws up the EU "
        "declaration of conformity (Annex V)",
    ),
)  # fmt: skip


@dataclass(frozen=True)
class PayerResolution:
    payer: str | None
    basis: PayerBasis
    legal_basis: str | None = None
    note: str | None = None


def payer_annex(record: Obligation) -> str | None:
    """The annex (roman numeral) an annex record belongs to, or None for an article record."""
    if record.article:
        return None
    m = _ANNEX.search(record.unit_id or "")
    return m.group(1) if m else None


def resolve_payer(record: Obligation, records_version: str) -> PayerResolution:
    """``records_version`` is the version the record belongs to (a consolidated run borrows the
    adopted records, so it passes ``reg2024_1689``)."""
    if record.primary_actor not in _UNSPECIFIED:
        if record.primary_actor not in SECTOR:
            return PayerResolution(
                None,
                "unknown",
                note=f"cost step: actor {record.primary_actor!r} of {record.obligation_id} is "
                "not in the payer map; payer left unknown",
            )
        return PayerResolution(record.primary_actor, "rule_field")
    annex = payer_annex(record)
    for rule in PAYER_RULES:
        if rule.version_id != records_version:
            continue
        if (record.article and record.article in rule.articles) or (
            annex is not None and annex in rule.annexes
        ):
            return PayerResolution(rule.payer, "rule_table", rule.legal_basis)
    return PayerResolution(None, "unknown")


def sector_for(payer: str | None, public_sector: bool | None) -> Sector | None:
    """``public`` for a public body, or for any payer whose record sets the public-sector flag
    (for example a public-body deployer). KeyError for an actor outside the map."""
    if payer is None:
        return None
    sector = SECTOR[payer]
    return "public" if public_sector else sector
