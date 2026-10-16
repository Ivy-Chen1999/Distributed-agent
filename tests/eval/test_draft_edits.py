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
