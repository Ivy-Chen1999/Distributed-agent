"""Golden-case drafts under review (U5): the CI gate plus unit tests of the review rules.

The gate runs over the real drafts in evals/golden/drafts/. It fails while an item that needs a
human (judge disagree/uncertain, a flag, the audit sample, a possibly_missing candidate, or an
escalated proposal) is undecided or has no GitHub-username reviewer. Only a draft PR may carry
undecided items: CI sets ``WOMM_DRAFT_PR=true`` from ``github.event.pull_request.draft``; pushes
to main and ready PRs run strictly. Edits must stay schema-valid with valid provision keys in
both modes, and a holdout case anywhere under evals/ always fails.
"""

import os
import re
import subprocess

import pytest
import yaml
from pydantic import ValidationError

from womm.config import REPO_ROOT
from womm.eval.drafting import DRAFTS_DIR, EVALS_DIR, load_draft
from womm.eval.golden_review import (
    audit_result,
    check_draft,
    escalated_fixtures,
    human_reasons,
    scenario_keys_for,
)

from .draft_factory import KEYS, decide, draft_dict, fully_decided, to_draft

DRAFT_PR = os.environ.get("WOMM_DRAFT_PR", "").strip().lower() == "true"
DRAFT_FILES = sorted(DRAFTS_DIR.glob("*.yaml"))
HOLDOUT_SPLIT = re.compile(r"""["']?\bsplit["']?\s*:\s*["']?holdout\b""", re.I)


def _evals_files() -> list:
    """Tracked files under evals/ plus everything under evals/golden/ (new local drafts)."""
    files = set(p for p in (EVALS_DIR / "golden").rglob("*") if p.is_file())
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "evals"], cwd=REPO_ROOT, capture_output=True, check=True
        ).stdout.decode()
        files |= {REPO_ROOT / f for f in out.split("\0") if f}
    except (OSError, subprocess.CalledProcessError):
        pass
    return sorted(p for p in files if p.is_file())


# ----------------------------------------------------------------------------- the CI gate


@pytest.mark.parametrize("path", DRAFT_FILES, ids=[p.stem for p in DRAFT_FILES])
def test_draft_is_reviewed(path):
    """Every item needing a human is decided by a GitHub user (draft PRs: not yet required)."""
    try:
        draft = load_draft(path)
    except (ValidationError, yaml.YAMLError) as exc:
        pytest.fail(f"{path.name}: the draft is not schema-valid after editing:\n{exc}")
    others = [load_draft(p) for p in DRAFT_FILES]
    escalated = draft.fixture in escalated_fixtures(others)
    problems = check_draft(
        draft, scenario_keys_for(draft), allow_pending=DRAFT_PR, escalated=escalated
    )
    hint = (
        "\nDecide these items following docs/eval/golden-review-guide.md, or keep the PR in "
        "draft (WOMM_DRAFT_PR=true) while the review is in progress."
    )
    assert not problems, "\n".join(problems) + hint


def test_drafts_live_only_in_the_drafts_folder():
    stray = [p.name for p in DRAFTS_DIR.rglob("*") if p.is_file() and p.suffix != ".yaml"]
    assert not stray, f"unexpected files in {DRAFTS_DIR}: {stray}"


def test_no_holdout_anywhere_under_evals():
    hits = []
    for path in _evals_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if HOLDOUT_SPLIT.search(text):
            hits.append(str(path.relative_to(REPO_ROOT)))
    assert not hits, (
        f"holdout material under evals/: {hits}; holdout drafts are verified locally with "
        "scripts/verify_golden_case.py and never go through a PR"
    )


# ----------------------------------------------------------------------------- rules


def _check(data, **kw):
    return check_draft(to_draft(data), set(KEYS), **kw)


def test_fully_decided_draft_passes():
    assert _check(fully_decided()) == []


def test_auto_accepted_items_need_nothing():
    item = to_draft(draft_dict()).expected_impacts[0]
    assert item.review.decision == "auto_accepted" and human_reasons(item) == []


def test_pending_item_fails_with_case_and_item_id():
    data = decide(fully_decided(), "c90_e05", "pending", reviewer=None)
    problems = _check(data)
    assert len(problems) == 1 and problems[0].startswith("case_90_widget_switching/c90_e05:")
    assert "judge disagree" in problems[0]


def test_decided_item_without_reviewer_fails():
    data = decide(fully_decided(), "c90_e06", "verified", reviewer=None)
    assert _check(data) == ["case_90_widget_switching/c90_e06: decision 'verified' has no reviewer"]


def test_reviewer_must_be_a_github_username():
    data = decide(fully_decided(), "c90_e06", "verified", reviewer="Jane Doe")
    assert any("not a GitHub username" in p for p in _check(data))
    assert _check(decide(fully_decided(), "c90_e06", "verified", reviewer="@jane-doe")) == []


def test_candidate_needs_a_human_even_when_auto_accepted():
    data = decide(fully_decided(), "c90_m02", "auto_accepted", reviewer=None)
    problems = _check(data)
    assert len(problems) == 1 and "possibly_missing candidate" in problems[0]


@pytest.mark.parametrize("item_id", ["c90_e05", "c90_e06"])
def test_flipping_a_human_item_to_auto_accepted_fails(item_id):
    data = decide(fully_decided(), item_id, "auto_accepted", reviewer=None)
    assert any("not auto_accepted" in p for p in _check(data))


def test_flagged_and_uncertain_items_need_a_human():
    data = draft_dict()
    data["expected_impacts"][0]["flags"] = ["anchor_not_found"]
    data["expected_impacts"][1]["judge"]["category"]["verdict"] = "unknown"
    data["expected_impacts"][1]["judge"]["overall"] = "uncertain"
    problems = _check(data | {})
    assert any("c90_e01" in p and "flagged" in p for p in problems)
    assert any("c90_e02" in p and "judge uncertain" in p for p in problems)
    assert not any("does not match" in p for p in problems)


def test_draft_pr_allows_pending_but_not_bad_edits():
    data = draft_dict()
    assert _check(data, allow_pending=True) == []
    assert _check(data)  # strict mode blocks
    data = decide(data, "c90_e05", "edited", provision_keys=["data_act/proposal/art/1"])
    assert any("not in scenario" in p for p in _check(data, allow_pending=True))
    bad_reviewer = decide(draft_dict(), "c90_e06", "verified", reviewer="not valid!")
    assert any("GitHub username" in p for p in _check(bad_reviewer, allow_pending=True))


def test_edit_that_breaks_the_schema_is_refused():
    data = draft_dict()
    data["expected_impacts"][0]["category"] = "made_up"
    with pytest.raises(ValidationError):
        to_draft(data)
    data = draft_dict()
    data["expected_impacts"][0]["provision_keys"] = []
    assert any("provision_keys is empty" in p for p in _check(data, allow_pending=True))


def test_rejected_and_unclear_need_a_note():
    data = decide(fully_decided(), "c90_m02", "rejected", note=None)
    assert any("needs a short note" in p for p in _check(data))
    data = decide(fully_decided(), "c90_e05", "unclear", note="  ")
    assert any("needs a short note" in p for p in _check(data))


def test_rejected_item_may_keep_a_bad_key():
    data = decide(fully_decided(), "c90_e05", "rejected", note="wrong",
                  provision_keys=["data_act/proposal/art/1"])  # fmt: skip
    assert _check(data) == []


def test_holdout_draft_fails_the_pr_check():
    assert any("holdout" in p for p in _check(fully_decided("holdout")))


def test_too_few_kept_impacts_fails_once_decided():
    data = fully_decided()
    for n in (1, 2, 3, 4):
        data = decide(data, f"c90_e{n:02d}", "rejected", note="not in the IA")
    data = decide(data, "c90_m01", "rejected", note="duplicate")
    assert any("only 2 expected impacts" in p for p in _check(data))


def test_audit_errors_over_ten_percent_escalate_the_proposal():
    data = decide(fully_decided(), "c90_e06", "edited", impact="Impact 6, fixed")
    draft = to_draft(data)
    result = audit_result([draft])
    assert (result.sampled, result.decided, result.errors) == (1, 1, 1) and result.escalated
    assert escalated_fixtures([draft]) == {"data_act"}
    problems = check_draft(draft, set(KEYS), escalated=True)
    assert {p.split(":")[0].split("/")[1] for p in problems} == {
        "c90_e01", "c90_e02", "c90_e03", "c90_e04", "c90_o01",
    }  # fmt: skip
    assert escalated_fixtures([to_draft(fully_decided())]) == set()


def test_tampering_with_judge_or_audit_blocks_is_caught():
    data = draft_dict()
    data["expected_impacts"][4]["judge"]["overall"] = "agree"  # e05: verdicts say disagree
    data["expected_impacts"][5]["review"]["audit"] = False  # e06 is in the audit sample
    problems = _check(data, allow_pending=True)
    assert any("c90_e05: judge.overall does not match" in p for p in problems)
    assert any("c90_e06: review.audit does not match" in p for p in problems)


def test_duplicate_item_ids_fail():
    data = draft_dict()
    data["expected_impacts"][1]["expected_id"] = "c90_e01"
    assert any("duplicate item id" in p for p in _check(data, allow_pending=True))


def test_holdout_pattern_catches_yaml_and_json():
    assert HOLDOUT_SPLIT.search("split: holdout")
    assert HOLDOUT_SPLIT.search('{"split": "holdout"}')
    assert not HOLDOUT_SPLIT.search("split: train  # holdout is never here")
