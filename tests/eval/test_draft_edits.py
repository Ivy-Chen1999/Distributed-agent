"""In-place edits of golden-case draft files: review links and review decisions.

The edits must leave every line they do not change as it was (a reviewer's comments survive),
keep the tool digests valid, and refuse a file that changed since it was read."""

import re

import pytest
import yaml
from pydantic import ValidationError

from womm.data.ia_index import IaRecord
from womm.eval.draft_edits import (
    DraftEditError,
    apply_decisions_in_place,
    build_review_links,
    set_review_links_in_place,
)
from womm.eval.drafting import (
    DRAFT_HEADER,
    LEGACY_DRAFT_HEADER,
    ReviewLinks,
    eurlex_url,
    load_draft,
    write_draft,
)
from womm.eval.golden_review import ReviewError, check_draft, decide_item

from .draft_factory import KEYS, draft_dict, to_draft

RECORD = IaRecord(celex="52099PC0001", ia_celex="52099SC0001(01)")


def _written(tmp_path, split="train"):
    return write_draft(to_draft(draft_dict(split)), tmp_path)


def test_eurlex_url_percent_encodes_the_celex():
    assert eurlex_url("52099SC0001(01)") == (
        "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:52099SC0001%2801%29"
    )


def test_review_links_must_be_eurlex_links():
    with pytest.raises(ValidationError):
        ReviewLinks(ia="https://example.com/x", proposal=eurlex_url("52099PC0001"))


def test_build_review_links_records_the_anchor_parts():
    draft = to_draft(draft_dict())
    parts = ["intro text", "widget providers would face cost number 1 under the option"]
    links = build_review_links(draft, RECORD, parts)
    assert links.ia.endswith("CELEX:52099SC0001%2801%29")
    assert links.proposal.endswith("CELEX:52099PC0001")
    assert links.ia_parts == 2 and links.anchor_parts["c90_e01"] == 2
    # One part: no part map, nothing to jump between.
    assert build_review_links(draft, RECORD, parts[1:]).anchor_parts == {}


def test_holdout_drafts_never_get_links():
    with pytest.raises(DraftEditError, match="holdout"):
        build_review_links(to_draft(draft_dict("holdout")), RECORD, None)
    data = draft_dict("holdout") | {
        "review_links": {"ia": eurlex_url("52099SC0001"), "proposal": eurlex_url("52099PC0001")}
    }
    with pytest.raises(ValidationError, match="holdout"):
        to_draft(data)


def test_links_are_inserted_in_place_and_keep_digests(tmp_path):
    path = _written(tmp_path)
    original = path.read_text()
    path.write_text(original.replace("expected_impacts:\n", "expected_impacts:\n# my note\n", 1))
    links = build_review_links(load_draft(path), RECORD, None)
    set_review_links_in_place(path, links)
    text = path.read_text()
    assert "# my note\n" in text and "review_links:\n  ia: https://eur-lex" in text
    assert text.index("review_links:") < text.index("provenance:")
    draft = load_draft(path)
    assert draft.review_links == links
    assert check_draft(draft, set(KEYS), allow_pending=True) == []
    # Idempotent: a second run gives the same file.
    set_review_links_in_place(path, links)
    assert path.read_text() == text


def test_legacy_header_is_replaced(tmp_path):
    path = _written(tmp_path)
    path.write_text(LEGACY_DRAFT_HEADER + path.read_text().removeprefix(DRAFT_HEADER))
    set_review_links_in_place(path, build_review_links(load_draft(path), RECORD, None))
    assert path.read_text().startswith(DRAFT_HEADER)


def test_decisions_are_written_in_place(tmp_path):
    path = _written(tmp_path)
    text = path.read_text().replace("possibly_missing:\n", "possibly_missing:\n# keep me\n", 1)
    path.write_text(text)
    decisions = {
        "c90_e05": {"decision": "edited", "note": "fixed", "edit": {"impact": "Impact 5, fixed"}},
        "c90_e06": {"decision": "verified"},
        "c90_m02": {"decision": "rejected", "note": "duplicate of c90_e01"},
    }
    apply_decisions_in_place(path, decisions, "octo-cat")
    out = path.read_text()
    assert "# keep me\n" in out
    draft = load_draft(path)
    by_id = {i.item_id: i for i in draft.items()}
    assert by_id["c90_e05"].impact == "Impact 5, fixed"
    assert by_id["c90_e05"].review.decision == "edited"
    assert by_id["c90_e06"].review.reviewer == "octo-cat"
    assert by_id["c90_e06"].provenance.status == "human_verified"
    assert check_draft(draft, set(KEYS), allow_pending=True) == []
    # Only the decided items' lines changed.
    import difflib

    diff = difflib.ndiff(text.splitlines(), out.splitlines())
    changed = [ln[2:] for ln in diff if ln[:2] in ("- ", "+ ")]
    allowed = re.compile(r"^(  impact|    (status|decision|reviewer|note)): ")
    assert changed and all(allowed.match(ln) for ln in changed), changed


def test_decisions_refuse_a_file_changed_meanwhile(tmp_path, monkeypatch):
    path = _written(tmp_path)
    from womm.eval import draft_edits

    real = draft_edits._replace_atomically

    def racing(p, read, out):
        p.write_text(p.read_text() + "\n# someone saved\n")
        return real(p, read, out)

    monkeypatch.setattr(draft_edits, "_replace_atomically", racing)
    before = path.read_text()
    with pytest.raises(DraftEditError, match="changed while"):
        apply_decisions_in_place(path, {"c90_e06": {"decision": "verified"}}, "octo-cat")
    assert path.read_text() == before + "\n# someone saved\n"


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"decision": "verifed"}, "one of verified, edited, rejected, unclear"),
        ({"decision": "rejected"}, "needs a note"),
        ({"decision": "edited"}, "'edit' mapping"),
        ({"decision": "verified", "edit": {"impact": "x"}}, "needs decision 'edited'"),
        ({"decision": "edited", "edit": {"judge": "x"}}, "cannot be edited"),
        ({"decision": "edited", "edit": {"category": "made_up"}}, "not schema-valid"),
    ],
)
def test_bad_decisions_are_explained(entry, message):
    item = to_draft(draft_dict()).expected_impacts[5]
    with pytest.raises(ReviewError, match=message):
        decide_item(item, entry, "octo-cat")


def test_unknown_item_and_bad_reviewer_are_refused(tmp_path):
    path = _written(tmp_path)
    with pytest.raises(DraftEditError, match="no item c90_e99"):
        apply_decisions_in_place(path, {"c90_e99": {"decision": "verified"}}, "octo-cat")
    with pytest.raises(DraftEditError, match="GitHub username"):
        apply_decisions_in_place(path, {"c90_e06": {"decision": "verified"}}, "Jane Doe")
    with pytest.raises(DraftEditError, match="drafted this case"):
        apply_decisions_in_place(path, {"c90_e06": {"decision": "verified"}}, "case-owner")


def test_edited_item_with_inner_comments_is_refused(tmp_path):
    path = _written(tmp_path)
    text = path.read_text()
    text = text.replace("  affected_actor: Actor 5\n", "  affected_actor: Actor 5\n  # hmm\n", 1)
    path.write_text(text)
    with pytest.raises(DraftEditError, match="comment"):
        apply_decisions_in_place(
            path, {"c90_e05": {"decision": "edited", "edit": {"impact": "x"}}}, "octo-cat"
        )
    # A decision without edits keeps the comment.
    apply_decisions_in_place(path, {"c90_e05": {"decision": "verified"}}, "octo-cat")
    assert "  # hmm\n" in path.read_text()


def test_yaml_written_by_the_tool_round_trips(tmp_path):
    path = _written(tmp_path)
    assert yaml.safe_load(path.read_text())["case_id"] == "case_90_widget_switching"


def test_every_bad_decision_is_reported_at_once(tmp_path):
    path = _written(tmp_path)
    before = path.read_text()
    decisions = {"c90_e06": {"decision": "verifed"}, "c90_m02": {"decision": "rejected"}}
    with pytest.raises(DraftEditError) as info:
        apply_decisions_in_place(path, decisions, "octo-cat")
    assert "c90_e06: decision must be one of" in str(info.value)
    assert "c90_m02: decision 'rejected' needs a note" in str(info.value)
    assert path.read_text() == before


# ----------------------------------------------------------------------------- human_added items

ANCHOR = "widget buyers would pay twice for the same switching service under the option"
FILLED = {"ia_section": "6.2.3. Intervention on widget services", "ia_anchor": ANCHOR,
          "affected_actor": "Widget buyers", "mechanism": "Double charging on switching",
          "category": "consumers_users"}  # fmt: skip


def staged_draft(tmp_path):
    """A fully decided train draft with an analyst's candidate c90_fb01 staged (raised by ana)."""
    from womm.evolve.feedback import stage_candidates

    from . import draft_factory as df

    path = tmp_path / "drafts" / "case_90_widget_switching.yaml"
    df.write(path, df.fully_decided("train"))
    row = {"feedback_id": "fb1", "case_id": "case_90_widget_switching", "fixture": "data_act",
           "run_id": "r1", "system_version": "sv", "analyst": "ana", "note": "seen twice",
           "payload": {"impact": "Widget buyers pay twice", "affected_actor": "Buyers",
                       "provision_keys": [KEYS[0]]}}  # fmt: skip
    staged, problems = stage_candidates([row], path.parent, registry=tmp_path / "none.yaml")
    assert staged == {"fb1": path} and problems == []
    return path


def test_a_human_added_item_is_decided_edited_with_its_anchor_and_keeps_its_analyst(tmp_path):
    path = staged_draft(tmp_path)
    before = load_draft(path).possibly_missing[-1].provenance
    entry = {"decision": "edited", "note": "anchored in 6.2.3", "edit": FILLED}
    draft = apply_decisions_in_place(path, {"c90_fb01": entry}, "octo-cat")
    cand = draft.possibly_missing[-1]
    assert cand.ia_anchor == ANCHOR and cand.review.decision == "edited"
    assert (cand.provenance.raised_by, cand.provenance.feedback_id) == ("ana", "fb1")
    assert cand.provenance.analyst_digest == before.analyst_digest
    assert load_draft(path) == draft
    assert check_draft(draft, set(KEYS)) == []


def test_a_completed_human_added_item_may_be_edited_without_changes(tmp_path):
    """The reviewer filled the fields in by hand; 'edited' then needs no edit mapping, and only
    the status and review lines change."""
    path = staged_draft(tmp_path)
    text = path.read_text()
    item = load_draft(path).possibly_missing[-1]
    filled = decide_item(item, {"decision": "edited", "edit": FILLED}, "octo-cat")
    assert filled.ia_anchor == ANCHOR
    with pytest.raises(ReviewError, match="ia_anchor is empty"):
        decide_item(item, {"decision": "edited"}, "octo-cat")
    head = text.index("candidate_id: c90_fb01")
    tail = text[head:]
    for field, value in FILLED.items():
        tail = re.sub(
            rf"(?m)^  {field}: .*$",
            f"  {field}: {yaml.safe_dump(value).splitlines()[0]}",
            tail,
            count=1,
        )
    path.write_text(text[:head] + tail)
    draft = apply_decisions_in_place(path, {"c90_fb01": {"decision": "edited"}}, "octo-cat")
    assert draft.possibly_missing[-1].review.decision == "edited"
    assert check_draft(draft, set(KEYS)) == []


@pytest.mark.parametrize(
    ("entry", "reviewer", "message"),
    [
        ({"decision": "verified"}, "octo-cat", "kept only as 'edited'"),
        ({"decision": "edited", "edit": FILLED}, "Ana", "raised this human_added item"),
        ({"decision": "rejected", "note": "dup"}, "ana", "raised this human_added item"),
        ({"decision": "edited", "edit": FILLED | {"mechanism": " "}}, "octo-cat", "mechanism"),
        ({"decision": "edited", "edit": {"raised_by": "bob"}}, "octo-cat", "cannot be edited"),
        ({"decision": "edited", "edit": {"feedback_id": "fb9"}}, "octo-cat", "cannot be edited"),
    ],
)
def test_a_human_added_item_is_refused_unless_edited_complete_and_by_someone_else(
    tmp_path, entry, reviewer, message
):
    path = staged_draft(tmp_path)
    text = path.read_text()
    with pytest.raises(DraftEditError, match=re.escape(message)):
        apply_decisions_in_place(path, {"c90_fb01": entry}, reviewer)
    assert path.read_text() == text


def test_a_human_added_item_may_be_rejected_by_another_reviewer(tmp_path):
    path = staged_draft(tmp_path)
    entry = {"decision": "rejected", "note": "covered by c90_e01"}
    draft = apply_decisions_in_place(path, {"c90_fb01": entry}, "octo-cat")
    assert draft.possibly_missing[-1].provenance.analyst_digest
    assert check_draft(draft, set(KEYS)) == []


def test_ia_anchor_is_editable_only_on_human_added_items():
    draft = to_draft(draft_dict())
    item = next(i for i in draft.items() if i.item_id == "c90_e05")
    with pytest.raises(ReviewError, match="cannot be edited"):
        decide_item(item, {"decision": "edited", "edit": {"ia_anchor": ANCHOR}}, "octo-cat")
