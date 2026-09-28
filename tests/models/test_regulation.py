import datetime as dt

import pytest
from pydantic import ValidationError

from womm.models.regulation import Provision, RegulationVersion, contract_json_schema


def _prov(key: str = "ai_act/penalties", article: str = "71") -> dict:
    return {
        "provision_key": key,
        "article": article,
        "paragraph": "1",
        "text": "Member States shall lay down the rules on penalties.",
        "source_id": "com2021_206/art_71",
    }


def test_valid_provision_and_schema_export():
    p = Provision.model_validate(_prov())
    assert p.provision_key == "ai_act/penalties"
    schema = contract_json_schema()
    assert "Provision" in schema["$defs"]
    assert "provision_key" in schema["$defs"]["Provision"]["required"]


def test_missing_provision_key_rejected():
    data = _prov()
    del data["provision_key"]
    with pytest.raises(ValidationError, match="provision_key"):
        Provision.model_validate(data)


def test_empty_provision_key_rejected():
    with pytest.raises(ValidationError):
        Provision.model_validate(_prov(key=""))


def test_duplicate_keys_in_version_rejected():
    with pytest.raises(ValidationError, match="duplicate provision_key"):
        RegulationVersion(
            version_id="v1",
            date=dt.date(2021, 4, 21),
            status="proposal",
            source="52021PC0206",
            provisions=[Provision(**_prov()), Provision(**_prov(article="72"))],
        )
