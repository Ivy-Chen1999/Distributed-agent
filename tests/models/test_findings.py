import pytest
from pydantic import ValidationError

from womm.models.findings import FindingBatch, FindingDraft, ImpactFinding, Provenance


def _draft(**over) -> dict:
    data = {
        "provision_key": "ai_act/penalties",
        "affected_actor": "AI providers",
        "impact": "Exposure to administrative fines",
        "mechanism": "Penalty regime applies to non-compliance",
        "evidence": [{"source_id": "s1", "quote": "Member States shall lay down the rules"}],
        "confidence": 0.7,
    }
    data.update(over)
    return data


PROV = Provenance(agent="legal", system_version="sv_x", prompt_hash="p", backend="fake", model="m")


def test_llm_supplied_finding_id_rejected():
    with pytest.raises(ValidationError, match="finding_id"):
        FindingDraft.model_validate(_draft(finding_id="made-up"))


def test_empty_evidence_is_valid_draft():
    assert FindingDraft.model_validate(_draft(evidence=[])).evidence == []


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        FindingDraft.model_validate(_draft(confidence=1.5))


def test_ids_are_deterministic_and_distinct():
    d = FindingDraft.model_validate(_draft())
    a = ImpactFinding.from_draft(d, run_id="r1", index=0, provenance=PROV)
    b = ImpactFinding.from_draft(d, run_id="r1", index=0, provenance=PROV)
    c = ImpactFinding.from_draft(d, run_id="r1", index=1, provenance=PROV)
    assert a.finding_id == b.finding_id
    assert a.finding_id != c.finding_id
    assert a.evidence[0].evidence_id == b.evidence[0].evidence_id
    assert a.agent == "legal"


def test_batch_schema_has_no_optional_fields():
    """OpenAI strict mode needs every property required."""
    schema = FindingBatch.model_json_schema()
    draft = schema["$defs"]["FindingDraft"]
    assert set(draft["required"]) == set(draft["properties"])
