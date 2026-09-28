import shutil
from pathlib import Path

import pytest

from womm.data.fixtures import DEFAULT_FIXTURE_DIR, FixtureError, load_fixture, validate_fixture
from womm.models.regulation import Scenario


@pytest.fixture(scope="module")
def fixture():
    return load_fixture()


def test_committed_fixture_validates(fixture):
    proposal = fixture.version("com2021_206")
    assert proposal.status == "proposal"
    keys = set(proposal.by_key())
    for s in fixture.scenarios.values():
        assert set(s.provision_keys) <= keys, s.scenario_id
    # Only provisions used by scenarios are shipped.
    used = {k for s in fixture.scenarios.values() for k in s.provision_keys}
    assert keys == used


def test_evaluation_scenarios(fixture):
    evals = [s for s in fixture.scenarios.values() if s.kind == "evaluation"]
    assert {s.scenario_id for s in evals} >= {"eval_provider_compliance_costs", "eval_sme_impacts"}
    for s in evals:
        before, after = fixture.scenario_versions(s.scenario_id)
        assert before is None
        assert [p.provision_key for p in after.provisions] == s.provision_keys
        assert sum(len(p.text) for p in after.provisions) < 40_000
        assert s.ia_reference and s.ia_reference.startswith("SWD(2021) 84")


def test_readable_keys_and_source_ids(fixture):
    by_key = fixture.version("com2021_206").by_key()
    risk = by_key["ai_act/high_risk/risk_management"]
    assert (risk.article, risk.source_id) == ("9", "com2021_206/art_9")
    assert by_key["ai_act/penalties/penalties"].article == "71"


def test_provision_text_is_its_source_text(fixture):
    for p in fixture.version("com2021_206").provisions:
        src = fixture.sources[p.source_id]
        assert src.kind == "provision"
        assert src.text == p.text


def test_text_continuing_across_page_blocks_is_kept(fixture):
    text = fixture.version("com2021_206").by_key()["ai_act/high_risk/risk_management"].text
    para4 = text.index("\n\n4. The risk management measures")
    cont = text.index("In eliminating or reducing risks related to the use")
    assert para4 < cont < text.index("\n\n5. High-risk AI systems shall be tested")


def test_sources_contain_no_stripped_sections(fixture):
    memos = [s for s in fixture.sources.values() if s.kind == "memorandum"]
    assert memos
    stripped = memos[0].stripped_sections
    assert "3.3. Impact assessment" in stripped
    assert "2.3. Proportionality" in stripped
    assert "4. BUDGETARY IMPLICATIONS" in stripped
    for src in fixture.sources.values():
        lines = {line.strip().casefold() for line in src.text.splitlines()}
        for heading in stripped:
            assert heading not in src.text, (src.source_id, heading)
            title = heading.split(" ", 1)[1].casefold()
            assert title not in lines, (src.source_id, heading)
        assert "impact assessment" not in src.text.casefold(), src.source_id


def test_scenario_sources_include_provisions_and_memorandum(fixture):
    ids = [s.source_id for s in fixture.scenario_sources("eval_sme_impacts")]
    assert ids[:4] == [
        "com2021_206/art_53",
        "com2021_206/art_54",
        "com2021_206/art_55",
        "com2021_206/art_71",
    ]
    assert "com2021_206/memorandum/other_elements" in ids


def test_scenario_with_unknown_key_fails_and_names_it(fixture):
    bad = Scenario(
        scenario_id="eval_bad",
        kind="evaluation",
        description="x",
        before_version=None,
        after_version="com2021_206",
        provision_keys=["ai_act/high_risk/risk_management", "ai_act/no_such_article"],
    )
    broken = type(fixture)(fixture.regulation, fixture.sources, {"eval_bad": bad})
    with pytest.raises(FixtureError, match=r"eval_bad.*ai_act/no_such_article"):
        validate_fixture(broken)


def test_unknown_scenario_lookup(fixture):
    with pytest.raises(FixtureError, match="unknown scenario"):
        fixture.scenario("nope")


def test_broken_scenarios_file_fails_load(tmp_path: Path):
    for name in ("proposal.json", "sources.json"):
        shutil.copy(DEFAULT_FIXTURE_DIR / name, tmp_path / name)
    (tmp_path / "scenarios.yaml").write_text(
        "scenarios:\n- scenario_id: s\n  kind: evaluation\n  description: d\n"
        "  before_version: null\n  after_version: com2021_206\n"
        "  provision_keys: [ai_act/missing]\n"
    )
    with pytest.raises(FixtureError, match="ai_act/missing"):
        load_fixture(tmp_path)


def test_missing_fixture_dir_fails(tmp_path: Path):
    with pytest.raises(FixtureError, match="no proposal.json"):
        load_fixture(tmp_path)
