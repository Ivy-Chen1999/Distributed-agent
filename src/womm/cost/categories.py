"""The fixed maps of a cost record: effort type -> IA cost category, and the EUR band edges.

The category names are the golden ``CATEGORY_GUIDE`` keys (``womm.eval.drafting``); they are
written out here, not imported, so the cost path never imports ``womm.eval`` (a test pins that
they match). The mapping follows the Better Regulation Standard Cost Model distinction:
information obligations are administrative burden; substantive measures are compliance costs; a
public payer's cost is public enforcement cost, whatever the effort.

Band edges are per affected entity and per regulated item (one-off), or per year (recurring).
Agents see these edges in EUR and hours; they never see which provision lies where.
"""

from __future__ import annotations

from womm.models.cost import Band, EffortType, IaCategory, Sector

IA_CATEGORY: dict[str, IaCategory] = {
    "documentation": "administrative_burden",
    "registration": "administrative_burden",
    "notification": "administrative_burden",
    "new_process": "compliance_cost",
    "human_oversight": "compliance_cost",
    "training": "compliance_cost",
    "assessment": "compliance_cost",
}
PUBLIC_CATEGORY: IaCategory = "public_enforcement_cost"

# Standard Cost Model rate used by the AI Act IA, and the full-time-equivalent derived from it.
SCM_RATE_EUR_PER_HOUR = 32
FTE_HOURS = 1720
FTE_EUR = SCM_RATE_EUR_PER_HOUR * FTE_HOURS

# band -> [low, high) in EUR; ``None`` is open-ended.
BAND_EDGES_EUR: dict[Band, tuple[int, int | None]] = {
    "negligible": (0, 1_000),
    "low": (1_000, 5_000),
    "medium": (5_000, 25_000),
    "high": (25_000, None),
}


def ia_category(effort: EffortType | str | None, sector: Sector | None) -> IaCategory | None:
    """The IA cost category of a record's primary effort type; a public payer overrides."""
    if effort is None:
        return None
    if sector == "public":
        return PUBLIC_CATEGORY
    return IA_CATEGORY[effort]


def band_for_eur(eur: float) -> Band:
    """The band a EUR figure falls in (scoring side only: the IA cost reference)."""
    if eur < 0:
        raise ValueError(f"negative EUR figure {eur}")
    for band, (low, high) in BAND_EDGES_EUR.items():
        if eur >= low and (high is None or eur < high):
            return band
    raise AssertionError("band edges must cover every non-negative figure")  # pragma: no cover
