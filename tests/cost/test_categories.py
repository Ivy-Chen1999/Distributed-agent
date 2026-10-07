"""The fixed effort-type -> IA cost category map and the EUR band edges."""

import pytest

from womm.cost.categories import (
    BAND_EDGES_EUR,
    FTE_EUR,
    IA_CATEGORY,
    SCM_RATE_EUR_PER_HOUR,
    band_for_eur,
    ia_category,
)
from womm.eval.drafting import CATEGORY_GUIDE
from womm.models.cost import BANDS, EFFORT_TYPES


@pytest.mark.parametrize(
    ("effort", "category"),
    [
        ("documentation", "administrative_burden"),
        ("registration", "administrative_burden"),
        ("notification", "administrative_burden"),
        ("new_process", "compliance_cost"),
        ("human_oversight", "compliance_cost"),
        ("training", "compliance_cost"),
        ("assessment", "compliance_cost"),
    ],
)
def test_each_effort_type_maps_to_its_decision_context_category(effort, category):
    assert IA_CATEGORY[effort] == category
    assert ia_category(effort, "private") == category


def test_the_map_covers_exactly_the_seven_effort_types():
    assert set(IA_CATEGORY) == set(EFFORT_TYPES) and len(EFFORT_TYPES) == 7


@pytest.mark.parametrize("effort", EFFORT_TYPES)
def test_a_public_payer_overrides_to_public_enforcement_cost(effort):
    assert ia_category(effort, "public") == "public_enforcement_cost"


def test_no_effort_type_has_no_category():
    assert ia_category(None, "public") is None


def test_categories_are_golden_category_guide_keys():
    """The cost path does not import womm.eval (it reaches the graph); this pins the names."""
    assert {*IA_CATEGORY.values(), "public_enforcement_cost"} <= set(CATEGORY_GUIDE)


@pytest.mark.parametrize(
    ("eur", "band"),
    [
        (0, "negligible"),
        (999, "negligible"),
        (1000, "low"),
        (4999, "low"),
        (5000, "medium"),
        (24_999, "medium"),
        (25_000, "high"),
        (275_000, "high"),
    ],
)
def test_band_for_eur_uses_the_decision_context_edges(eur, band):
    assert band_for_eur(eur) == band


def test_band_edges_are_ordered_and_cover_every_band():
    assert list(BAND_EDGES_EUR) == list(BANDS)
    lows = [lo for lo, _ in BAND_EDGES_EUR.values()]
    assert lows == sorted(lows) and lows[0] == 0
    assert BAND_EDGES_EUR["high"][1] is None


def test_fte_is_the_scm_rate_times_1720_hours():
    assert SCM_RATE_EUR_PER_HOUR == 32
    assert FTE_EUR == 32 * 1720


def test_negative_eur_is_refused():
    with pytest.raises(ValueError):
        band_for_eur(-1)
