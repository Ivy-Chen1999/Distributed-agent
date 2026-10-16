"""scripts/review_draft.py: the reviewer helper for golden-case drafts in PRs."""

import importlib.util
import sys

import pytest
import yaml

from womm.config import REPO_ROOT
from womm.data.ia_index import IaRecord
from womm.eval.draft_edits import build_review_links, set_review_links_in_place
from womm.eval.drafting import load_draft, write_draft

from .eval.draft_factory import draft_dict, to_draft

spec = importlib.util.spec_from_file_location("review_draft", REPO_ROOT / "scripts/review_draft.py")
review = importlib.util.module_from_spec(spec)
sys.modules["review_draft"] = review
spec.loader.exec_module(review)

TODO = ["c90_e05", "c90_e06", "c90_o01", "c90_m01", "c90_m02"]


@pytest.fixture
def drafts(tmp_path, monkeypatch):
    """A drafts folder holding one train draft with review links; no git identity."""
    directory = tmp_path / "drafts"
    path = write_draft(to_draft(draft_dict()), directory)
    record = IaRecord(celex="52099PC0001", ia_celex="52099SC0001")
    set_review_links_in_place(path, build_review_links(load_draft(path), record, None))
    monkeypatch.setattr(review, "git_user", lambda: None)
    monkeypatch.setattr(review, "TEMPLATE_DIR", tmp_path / "cache" / "review")
    return directory


def _run(drafts, *argv, answers=()):
    out: list[str] = []
    replies = iter(answers)

    def ask(prompt):
        out.append(prompt)
        return next(replies)

    code = review.main([*argv, "--drafts-dir", str(drafts)], ask=ask, say=out.append)
    return code, "\n".join(out)


def test_lists_only_the_items_to_decide_with_links_and_judges(drafts):
    code, out = _run(drafts)
    assert code == 0
    assert "decisions needed: 5 (0 made, 5 to go)" in out
    assert "IA:       https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:52099SC0001" in out
    for item_id in TODO:
        assert f"{item_id}  [" in out
    assert "c90_e01  [" not in out
    assert 'search the IA for: "widget providers would face cost number 5' in out
    assert "needs you because: judge disagree" in out and "category disagree: r" in out
    # A legacy pending audit item (drafted while the audit blocked) is still listed.
    assert "needs you because: left pending by the drafting tool" in out
    assert "possibly_missing candidate" in out


def test_resolves_a_case_prefix_and_refuses_ambiguity(drafts, tmp_path):
    assert review.resolve_draft("case_90", drafts).name == "case_90_widget_switching.yaml"
    write_draft(to_draft(draft_dict(case_id="case_91_other")), drafts)
    with pytest.raises(review.HelperError, match="expected one draft"):
        review.resolve_draft(None, drafts)


def test_template_then_apply_then_check(drafts, tmp_path):
    out_file = tmp_path / "decisions.yaml"
    code, out = _run(drafts, "--template", str(out_file))
    assert code == 0
    data = yaml.safe_load(out_file.read_text())
    assert list(data["decisions"]) == TODO and data["reviewer"] == review.PLACEHOLDER
    # Applying the untouched template asks who the reviewer is.
    data["decisions"]["c90_e06"]["decision"] = "verified"
    out_file.write_text(yaml.safe_dump(data))
    code, out = _run(drafts, "--apply", str(out_file))
    assert code == 2
    data["reviewer"] = "octo-cat"
    data["decisions"].update(
        c90_e05={"decision": "edited", "note": "narrowed", "edit": {"impact": "Impact 5, fixed"}},
        c90_o01={"decision": "verified"},
        c90_m01={"decision": "rejected", "note": "covered by c90_e02"},
    )
    out_file.write_text(yaml.safe_dump(data))
    code, out = _run(drafts, "--apply", str(out_file))
    assert code == 0 and "wrote 4 decision(s) by octo-cat" in out
    assert "CI on the draft PR: passes" in out
    assert "Before you mark the PR ready (1 to do):" in out
    assert "c90_m02: needs a human decision (possibly_missing candidate)" in out
    code, out = _run(drafts, "--check")
    assert code == 1
    data["decisions"]["c90_m02"] = {"decision": "unclear", "note": "hedged in the IA"}
    out_file.write_text(yaml.safe_dump(data))
    _run(drafts, "--apply", str(out_file))
    code, out = _run(drafts, "--check")
    assert code == 0 and "Ready: every decision is made" in out


def test_template_is_not_written_into_the_drafts_folder(drafts):
    code, _ = _run(drafts, "--template", str(drafts / "x.yaml"))
    assert code == 2 and not (drafts / "x.yaml").exists()
    code, out = _run(drafts, "--template")
    assert code == 0 and "case_90_widget_switching.decisions.yaml" in out


def test_interactive_session_writes_in_place(drafts):
    path = drafts / "case_90_widget_switching.yaml"
    before = path.read_text()
    # e05 verified; e06 rejected (an empty note is asked again); o01 edited (description, then
    # Enter keeps keys, section and category); m01 skipped; quit at m02.
    ans = ["v", "", "r", "", "r", "covered by c90_e01", "e", "x", "", "", "", "fixed", "s", "q"]
    code, out = _run(drafts, "--interactive", "--reviewer", "octo-cat", answers=ans)
    assert code == 0, out
    draft = load_draft(path)
    by_id = {i.item_id: i for i in draft.items()}
    assert by_id["c90_e05"].review.decision == "verified"
    assert by_id["c90_e06"].review.note == "covered by c90_e01"
    assert by_id["c90_o01"].description == "x" and by_id["c90_o01"].review.decision == "edited"
    assert by_id["c90_m01"].review.decision == "pending"
    assert path.read_text() != before and "wrote 3 decision(s)" in out


def test_interactive_refuses_a_bad_reviewer_before_asking(drafts):
    code, out = _run(drafts, "--interactive", "--reviewer", "Jane Doe")
    assert code == 2 and "[v]erified" not in out
    code, out = _run(drafts, "--interactive", "--reviewer", "case-owner")
    assert code == 2


def test_check_explains_a_broken_file(drafts, capsys):
    path = drafts / "case_90_widget_switching.yaml"
    path.write_text(path.read_text().replace("decision: pending", "decision: verifed", 1))
    code, out = _run(drafts, "--check")
    assert code == 1 and "review.decision is 'verifed'" in out


def test_holdout_drafts_are_refused(tmp_path, monkeypatch):
    directory = tmp_path / "drafts"
    directory.mkdir()
    (directory / "case_90_widget_switching.yaml").write_text(
        yaml.safe_dump(draft_dict("holdout"), sort_keys=False)
    )
    code, _ = _run(directory)
    assert code == 2
