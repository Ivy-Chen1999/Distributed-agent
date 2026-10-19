"""Cost record models: the LLM draft is strict; the record exposes its bands."""

import pytest
from pydantic import ValidationError

from womm.models.cost import BANDS, CostBatch, CostDraft, CostRecord, band_rank

BASE = {
    "obligation_id": "o1",
    "unit_id": "u1",
    "provision_key": "k",
    "source_id": "com2021_206/obligations/art_10",
    "records_version": "com2021_206",
}


def test_draft_rejects_an_unknown_effort_type_and_band():
    with pytest.raises(ValidationError):
        CostDraft(obligation_id="o1", effort_type="lobbying")
    with pytest.raises(ValidationError):
        CostDraft(obligation_id="o1", effort_type="training", one_off="huge")


def test_draft_rejects_extra_fields():
    with pytest.raises(ValidationError):
        CostBatch.model_validate({"records": [{"obligation_id": "o1", "eur": 4000}]})


def test_bands_are_ordinal():
    assert BANDS == ("negligible", "low", "medium", "high")
    assert [band_rank(b) for b in (None, *BANDS)] == [0, 1, 2, 3, 4]


def test_record_top_band_takes_the_higher_of_one_off_and_recurring():
    rec = CostRecord(**BASE, status="estimated", one_off="low", recurring="medium")
    assert rec.top_band == "medium"
    assert rec.band("one_off") == "low" and rec.band("recurring") == "medium"
    assert CostRecord(**BASE, status="not_costed").top_band is None


def test_record_has_no_money_field():
    """Bands only: nothing on a record can be summed into euros."""
    fields = set(CostRecord.model_fields)
    assert not {f for f in fields if "eur" in f.lower() or "amount" in f.lower()}
