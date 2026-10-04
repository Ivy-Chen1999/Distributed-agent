import importlib.util
import json
import sys
from pathlib import Path

import pytest

from womm.config import REPO_ROOT
from womm.data.cellar import cached
from womm.data.memorandum import (
    Allow,
    MemorandumError,
    Redaction,
    leak_check_text,
    leak_guard,
    memorandum_sources,
    normalise,
    redact_sentences,
    sentence_starts,
    strip_topics,
)
from womm.data.parse_proposal import (
    MemorandumSection,
    parse_document,
    parse_memorandum,
    strip_sections,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
AI_ACT_DOC_1 = (
    "https://publications.europa.eu/resource/cellar/"
    "e0649735-a372-11eb-9585-01aa75ed71a1.0001.03/DOC_1"
)


def _build_fixture_module():
    spec = importlib.util.spec_from_file_location(
        "build_fixture", REPO_ROOT / "scripts/build_fixture.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["build_fixture"] = module
    spec.loader.exec_module(module)
    return module


def _sections(name: str) -> list[MemorandumSection]:
    return parse_memorandum(parse_document((FIXTURES / name).read_bytes()))


def _template(*extra: MemorandumSection) -> list[MemorandumSection]:
    return [
        MemorandumSection("1.", "CONTEXT OF THE PROPOSAL", 1, ["Context."]),
        MemorandumSection("2.", "LEGAL BASIS, SUBSIDIARITY AND PROPORTIONALITY", 1),
        MemorandumSection("2.1.", "Legal basis", 2, ["Article 114 TFEU."]),
        MemorandumSection("2.2.", "Proportionality", 2, ["Costs are proportionate."]),
        *extra,
        MemorandumSection("4.", "BUDGETARY IMPLICATIONS", 1, ["5 FTE."]),
    ]


def test_ai_act_strip_list_equals_v0() -> None:
    sections = _sections("com2021_206_sample.xhtml")
    v0 = _build_fixture_module()
    _, v0_removed = strip_sections(sections, v0.MEMORANDUM_STRIP)
    result = strip_topics(sections)
    assert result.removed == v0_removed
    assert set(result.topics) == {"ia_results", "proportionality", "budget"}


def test_ai_act_sources_equal_v0_path() -> None:
    sections = _sections("com2021_206_sample.xhtml")
    v0 = _build_fixture_module()
    result = strip_topics(sections)
    ours = memorandum_sources(
        result.kept, result.removed, version_id="com2021_206", label="COM(2021) 206"
    )
    assert ours == v0.memorandum_sources(sections)


def test_ai_act_sources_equal_committed_fixture() -> None:
    body = cached(AI_ACT_DOC_1)
    if body is None:
        pytest.skip("COM(2021) 206 DOC_1 is not in .cache/cellar")
    result = strip_topics(parse_memorandum(parse_document(body)))
    leak_guard(result.kept)
    ours = memorandum_sources(
        result.kept, result.removed, version_id="com2021_206", label="COM(2021) 206"
    )
    committed = json.loads((REPO_ROOT / "data/fixtures/ai_act/sources.json").read_text())
    memo = [s for s in committed if s["kind"] == "memorandum"]
    assert [s.model_dump(mode="json") for s in ours] == memo


@pytest.mark.parametrize(
    ("number", "heading"),
    [
        ("3.", "RESULTS OF EX-POST EVALUATIONS, STAKEHOLDER CONSULTATIONS AND IMPACT ASSESSMENTS"),
        ("III.", "Results of ex-post evaluations, stakeholder consultations and impact assessment"),
        ("•.", "results of ex post evaluations, stakeholder consultations and impact assessments"),
        ("3.", "Results of Ex-Post Evaluations, Stakeholder Consultations and Impact Assessment"),
    ],
)
def test_ia_section_stripped_whatever_its_case_or_numbering(number: str, heading: str) -> None:
    ia = MemorandumSection(number, heading, 1, ["Summary."])
    sub = MemorandumSection(f"{number}1.", "Something else entirely", 2, ["The preferred option."])
    result = strip_topics(_template(ia, sub))
    assert f"{number} {heading}" in result.removed
    assert f"{number}1. Something else entirely" in result.removed
    leak_guard(result.kept)


def test_legal_basis_parent_is_kept_but_proportionality_goes() -> None:
    ia = MemorandumSection("3.", "Impact assessment", 1, ["x"])
    result = strip_topics(_template(ia))
    kept = [s.full_heading for s in result.kept]
    assert "2. LEGAL BASIS, SUBSIDIARITY AND PROPORTIONALITY" in kept
    assert "2.1. Legal basis" in kept
    assert "2.2. Proportionality" in result.removed


def test_missing_required_topic_fails_listing_headings() -> None:
    with pytest.raises(MemorandumError, match=r"52099PC0001: .*\['ia_results'\].*Legal basis"):
        strip_topics(_template(), label="52099PC0001")


def test_extra_pattern_strips_a_named_section() -> None:
    ia = MemorandumSection("3.", "Impact assessment", 1, ["x"])
    result = strip_topics(_template(ia), extra_patterns={"context": r"^context of the proposal$"})
    assert "1. CONTEXT OF THE PROPOSAL" in result.removed
    assert result.topics["context"] == ["1. CONTEXT OF THE PROPOSAL"]


@pytest.mark.parametrize(
    "text",
    [
        "The preferred option is option 3.",
        "As the Impact Assessment shows, costs are low.",
        "The Regulatory Scrutiny Board gave a positive opinion.",
        "See SWD(2022) 34 for details.",
        "The preferred policy option saves EUR 22 million per year.",
        "As shown in the Commission staff working document, the proposal is consistent.",
        "This was confirmed in the public consultation.",
        "The objectives respond to the challenges identified in the problem definition.",
    ],
)
def test_leak_guard_names_the_section(text: str) -> None:
    kept = [MemorandumSection("1.1.", "Reasons for and objectives", 2, ["Fine.", text])]
    with pytest.raises(MemorandumError, match=r"(?s)52099PC0001: 1 IA marker.*'1\.1\. Reasons"):
        leak_guard(kept, label="52099PC0001")


def test_leak_guard_allow_list_masks_exact_citations_only() -> None:
    kept = [
        MemorandumSection(
            "5.1.", "Monitoring", 2, ["Indicators are listed in SWD(2022) 34, section 9."]
        )
    ]
    allow = [Allow("5.1.", "SWD(2022) 34")]
    leak_guard(kept, allow=allow)
    kept[0].blocks.append("The preferred option is cheap.")
    with pytest.raises(MemorandumError, match="preferred option"):
        leak_guard(kept, allow=allow)


def test_data_act_sample_strips_and_passes_the_guard() -> None:
    result = strip_topics(_sections("com2022_68_sample.xhtml"), label="52022PC0068")
    assert result.removed == [
        "2.1. Proportionality",
        "3. RESULTS OF EX-POST EVALUATIONS, STAKEHOLDER CONSULTATIONS AND IMPACT ASSESSMENTS",
        "3.1. Stakeholder consultations",
        "3.2. Impact assessment",
        "4. BUDGETARY IMPLICATIONS",
    ]
    leak_guard(result.kept, label="52022PC0068")
    sources = memorandum_sources(
        result.kept, result.removed, version_id="com2022_68", label="COM(2022) 68"
    )
    assert [s.source_id for s in sources] == [
        "com2022_68/memorandum/context",
        "com2022_68/memorandum/legal_basis",
        "com2022_68/memorandum/other_elements",
    ]


def _policies(*blocks: str) -> list[MemorandumSection]:
    return [MemorandumSection("1.3.", "Consistency with other Union policies", 2, list(blocks))]


LEAKY = (
    "The proposal complements Directive X. The existing rules do not cover all the gaps "
    "evidenced in the impact assessment report, e.g. notice and action. It also builds on the "
    "Recommendation of 2018."
)


def test_redaction_removes_exactly_the_listed_sentence_by_prefix() -> None:
    result = redact_sentences(
        _policies(LEAKY, "Second paragraph."),
        [Redaction("1.3.", "The existing rules do not cover", "cites the IA's gap analysis")],
        label="52099PC0001",
    )
    assert result.kept[0].blocks == [
        "The proposal complements Directive X. It also builds on the Recommendation of 2018.",
        "Second paragraph.",
    ]
    assert result.records == {
        "1.3. Consistency with other Union policies": ["cites the IA's gap analysis"]
    }
    leak_guard(result.kept, label="52099PC0001")


def test_redaction_by_full_heading_and_exact_sentence_drops_an_emptied_block() -> None:
    sections = _policies("The impact assessment found option 2 cheapest.", "Kept.")
    result = redact_sentences(
        sections,
        [
            Redaction(
                "1.3. Consistency with other Union policies",
                "The impact assessment found option 2 cheapest.",
                "states the IA's option ranking",
            )
        ],
    )
    assert result.kept[0].blocks == ["Kept."]
    assert sections[0].blocks[0].startswith("The impact assessment")  # input untouched


@pytest.mark.parametrize(
    ("redaction", "message"),
    [
        (Redaction("1.3.", "This sentence is not there", "r"), "not found"),
        (Redaction("1.3.", "evidenced in the impact assessment", "r"), "not found"),  # mid-sentence
        (Redaction("1.3.", "The", "r"), "starts 2 sentences"),
        (Redaction("3.3.", "The proposal", "r"), r"'3\.3\.' matches 0 kept sections"),
        (Redaction("1.3.", "The proposal", " "), "empty sentence or reason"),
    ],
)
def test_stale_or_ambiguous_redaction_fails_naming_the_celex(
    redaction: Redaction, message: str
) -> None:
    with pytest.raises(MemorandumError, match=f"(?s)52099PC0001: redaction failed.*{message}"):
        redact_sentences(_policies(LEAKY), [redaction], label="52099PC0001")


def test_guard_still_fails_on_a_marker_no_redaction_covers() -> None:
    sections = _policies(LEAKY, "As the impact assessment shows, costs are low.")
    result = redact_sentences(
        sections, [Redaction("1.3.", "The existing rules do not cover", "cites the IA")]
    )
    with pytest.raises(MemorandumError, match=r"(?s)1 IA marker.*as the impact assessment shows"):
        leak_guard(result.kept, label="52099PC0001")


def test_allow_list_masks_the_exact_phrase_only() -> None:
    other = "Obligations under Union law such as environmental impact assessment apply."
    allow = [Allow("1.3.", "environmental impact assessment")]
    leak_guard(_policies(other), allow=allow)
    leaky = _policies(other, "The impact assessment shows low costs.")
    with pytest.raises(MemorandumError, match="1 IA marker"):
        leak_guard(leaky, allow=allow)


@pytest.mark.parametrize(
    "phrase",
    [
        "impact assessment",
        "Impact  Assessment",
        "the impact assessment",
        "The Impact Assessments",
        "this impact assessment",
        "an IA",
        "the SWD(",
        "assessment",
        " ",
    ],
)
def test_allow_list_cannot_mask_a_bare_marker(phrase: str) -> None:
    text = f"Nothing to see in {phrase} here."
    with pytest.raises(MemorandumError, match="would mask a bare marker"):
        leak_guard(_policies(text), allow=[Allow("1.3.", phrase)])


def test_allow_entry_must_name_a_kept_section() -> None:
    other = "Union law such as environmental impact assessment applies."
    with pytest.raises(MemorandumError, match=r"'9\.9\.' matches 0 kept sections"):
        leak_guard(_policies(other), allow=[Allow("9.9.", "environmental impact assessment")])


def test_allow_entry_is_scoped_to_its_section() -> None:
    other = "Union law such as environmental impact assessment applies."
    sections = [
        *_policies(other),
        MemorandumSection("5.1.", "Monitoring", 2, [other]),
    ]
    with pytest.raises(MemorandumError, match=r"(?s)1 IA marker.*'5\.1\. Monitoring'"):
        leak_guard(sections, allow=[Allow("1.3.", "environmental impact assessment")])


def test_allow_entry_must_match_exactly_one_occurrence() -> None:
    other = "Union law such as environmental impact assessment applies."
    with pytest.raises(MemorandumError, match="occurs 2 times"):
        leak_guard(
            _policies(other, other), allow=[Allow("1.3.", "environmental impact assessment")]
        )


def test_stale_allow_list_entry_fails() -> None:
    with pytest.raises(MemorandumError, match="occurs 0 times"):
        leak_guard(
            _policies("Nothing to see."), allow=[Allow("1.3.", "environmental impact assessment")]
        )


@pytest.mark.parametrize(
    "text",
    [
        "As the impact assess\u00adment shows, costs are low.",  # soft hyphen
        "As the impact-assessment shows, costs are low.",
        "As the impact\u2011assessment shows, costs are low.",  # non-breaking hyphen
        "As the Impact\n  Assessment shows, costs are low.",
        "See SWD (2022) 34 for details.",
        "See swd(2022) 34 for details.",
        "The Board's opinion SEC (2022) 164 was positive.",
        "The Board's opinion SEC(2022)164 was positive.",
        "The IA found low costs.",
        "As the IAs show, costs are low.",
        "The impact analysis demonstrates low costs.",
        "The impact-analysis demonstrates low costs.",
        "The preferred\u2013option is option 3.",
    ],
)
def test_leak_guard_catches_variants(text: str) -> None:
    kept = [MemorandumSection("1.1.", "Reasons for and objectives", 2, ["Fine.", text])]
    with pytest.raises(MemorandumError, match="1 IA marker"):
        leak_guard(kept, label="52099PC0001")


@pytest.mark.parametrize(
    "text",
    [
        "Media literacy is a priority.",  # "ia" inside a word
        "The agencies of the Union cooperate.",
        "Section (2022) is not a reference.",
        "Ex-post evaluations are kept.",
    ],
)
def test_leak_guard_ignores_lookalikes(text: str) -> None:
    leak_guard([MemorandumSection("1.1.", "Reasons", 2, [text])])


def test_ia_false_positive_is_handled_by_a_scoped_allow_entry() -> None:
    text = "Annex IA lists the product categories."
    with pytest.raises(MemorandumError, match="1 IA marker"):
        leak_guard(_policies(text))
    leak_guard(_policies(text), allow=[Allow("1.3.", "Annex IA lists")])


def test_normalise_removes_soft_hyphens_and_dashes() -> None:
    assert normalise("Impact\u00adAssess\u00adment \u2013 a\u2014b  -c") == "impactassessment a b c"


def test_leak_check_text_names_the_field() -> None:
    leak_check_text("Obligations of providers of AI systems.", what="scenario eval_x")
    with pytest.raises(
        MemorandumError, match=r"(?s)52099PC0001: scenario eval_x.*impact assessment"
    ):
        leak_check_text(
            "Costs as estimated in the impact assessment.",
            what="scenario eval_x",
            label="52099PC0001",
        )


@pytest.mark.parametrize(
    "text",
    [
        "Some Member States, e.g. Germany and France, have rules.",
        "That is, i.e. In practice, nothing changes.",
        "See cf. Directive 2001/95/EC on safety.",
        "As set out in Art. 5 of the Directive, rules apply.",
        "Regulation (EU) No. 2019/1020 applies here.",
    ],
)
def test_sentence_starts_ignore_abbreviations(text: str) -> None:
    assert sentence_starts(text) == {0}


def test_redaction_does_not_cut_at_an_abbreviation() -> None:
    block = (
        "Rules exist. The impact assessment found that some States, e.g. Germany, already "
        "comply. Other text."
    )
    result = redact_sentences(
        _policies(block), [Redaction("1.3.", "The impact assessment found", "IA finding")]
    )
    assert result.kept[0].blocks == ["Rules exist. Other text."]


def test_redactions_recorded_on_the_owning_source_only() -> None:
    sections = [
        MemorandumSection("1.", "CONTEXT OF THE PROPOSAL", 1),
        *_policies(LEAKY),
        MemorandumSection("2.", "LEGAL BASIS, SUBSIDIARITY AND PROPORTIONALITY", 1, ["Art 114."]),
    ]
    result = redact_sentences(
        sections, [Redaction("1.3.", "The existing rules", "cites the IA's gap analysis")]
    )
    sources = memorandum_sources(
        result.kept,
        ["3. Impact assessment"],
        version_id="com2099_1",
        label="COM(2099) 1",
        redactions=result.records,
    )
    context, legal = sources
    assert context.redactions == [
        "redacted: 1 sentence in 1.3. Consistency with other Union policies"
    ]
    assert "gap analysis" not in context.model_dump_json()  # the reason never reaches a source
    assert "impact assessment" not in context.text
    assert legal.redactions == []
    assert "redactions" not in legal.model_dump() and "redactions" in context.model_dump()
