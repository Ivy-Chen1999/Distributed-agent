"""scripts/publish_golden_cases.py (U5): fully decided drafts become scored golden cases."""

import importlib.util
import sys

import yaml

from womm.config import REPO_ROOT
from womm.eval.golden import check_against_fixture, load_all_golden, load_golden
from womm.eval.golden_review import AuditResult, load_audit_tally, write_audit_tally

from .eval.draft_factory import decide, draft_dict, fully_decided, seal, write

spec = importlib.util.spec_from_file_location(
    "publish_golden_cases", REPO_ROOT / "scripts/publish_golden_cases.py"
)
publish = importlib.util.module_from_spec(spec)
sys.modules["publish_golden_cases"] = publish
spec.loader.exec_module(publish)

CASE = "case_90_widget_switching"


def _run(tmp_path, *extra):
    argv = ["--drafts-dir", str(tmp_path / "drafts"), "--golden-dir", str(tmp_path / "golden"),
            "--today", "2026-10-20", "--tally", str(tmp_path / "golden" / "audit_tally.yaml"),
            "--holdout-registry", str(tmp_path / "private" / "holdout_scenarios.yaml"),
            *extra]  # fmt: skip
    return publish.main(argv)


def test_publishes_a_fully_decided_draft(tmp_path, capsys):
    draft = tmp_path / "drafts" / f"{CASE}.yaml"
    write(draft, fully_decided())
    assert _run(tmp_path) == 0
    out = tmp_path / "golden" / f"{CASE}.yaml"
    assert f"published {CASE}" in capsys.readouterr().out
    assert not draft.exists(), "the draft is deleted after publishing"
    case = load_golden(out)
    check_against_fixture(case)
    assert [c.case_id for c in load_all_golden(tmp_path / "golden")] == [CASE]
    by_id = {e.expected_id: e for e in case.expected_impacts}
    # rejected candidate m02 dropped; accepted candidate m01 kept with its provenance
    assert sorted(by_id) == [f"c90_e0{n}" for n in range(1, 7)] + ["c90_m01"]
    assert by_id["c90_m01"].provenance == "human_confirmed_candidate"
    assert by_id["c90_e01"].provenance == "llm_judged"
    assert by_id["c90_e05"].provenance == "human_edited"
    assert by_id["c90_e05"].impact == "Impact 5, corrected"
    assert by_id["c90_e06"].provenance == "human_verified"
    assert case.important_omissions[0].source.startswith("Impact assessment, 6.2.3.")
    assert case.split == "train" and case.fixture == "data_act"
    assert "octo-cat" in case.notes and "2026-10-04" in case.notes and "2026-10-20" in case.notes
    assert "c90_m02" in case.notes  # declined candidate recorded


def test_rejected_and_unclear_items_are_dropped(tmp_path):
    data = decide(fully_decided(), "c90_e05", "unclear", note="two readings possible")
    write(tmp_path / "drafts" / f"{CASE}.yaml", data)
    assert _run(tmp_path) == 0
    case = load_golden(tmp_path / "golden" / f"{CASE}.yaml")
    ids = {e.expected_id for e in case.expected_impacts}
    assert "c90_e05" not in ids and "c90_m02" not in ids
    assert "unclear (ambiguous, not kept) ['c90_e05']" in case.notes


def test_audit_error_escalates_until_every_item_is_reviewed(tmp_path, capsys):
    data = decide(fully_decided(), "c90_e06", "rejected", note="not what the IA says")
    write(tmp_path / "drafts" / f"{CASE}.yaml", data)
    assert _run(tmp_path) == 2
    assert "c90_e01: needs a human decision (proposal escalated" in capsys.readouterr().err
    for item in ("c90_e01", "c90_e02", "c90_e03", "c90_e04", "c90_o01"):
        data = decide(data, item, "verified")
    write(tmp_path / "drafts" / f"{CASE}.yaml", data)
    assert _run(tmp_path) == 0
    case = load_golden(tmp_path / "golden" / f"{CASE}.yaml")
    assert "escalated" in case.notes
    assert all(e.provenance != "llm_judged" for e in case.expected_impacts)


def test_undecided_draft_is_refused_and_kept(tmp_path, capsys):
    draft = tmp_path / "drafts" / f"{CASE}.yaml"
    write(draft, draft_dict())
    assert _run(tmp_path) == 2
    err = capsys.readouterr().err
    assert "c90_e05: pending" in err and draft.exists()
    assert not (tmp_path / "golden").exists()


def test_fewer_than_three_kept_impacts_is_refused(tmp_path, capsys):
    data = fully_decided()
    for n in (1, 2, 3, 4):
        data = decide(data, f"c90_e0{n}", "rejected", note="not in the IA")
    data = decide(data, "c90_m01", "rejected", note="duplicate")
    write(tmp_path / "drafts" / f"{CASE}.yaml", data)
    assert _run(tmp_path) == 2
    assert "only 2 expected impacts" in capsys.readouterr().err


def test_holdout_draft_is_never_published_to_evals(tmp_path, capsys):
    write(tmp_path / "drafts" / f"{CASE}.yaml", fully_decided("holdout"))
    assert _run(tmp_path) == 2
    assert "verify_golden_case.py" in capsys.readouterr().err
    assert not (tmp_path / "golden").exists()


def test_existing_case_is_not_overwritten(tmp_path, capsys):
    write(tmp_path / "drafts" / f"{CASE}.yaml", fully_decided())
    (tmp_path / "golden").mkdir()
    (tmp_path / "golden" / f"{CASE}.yaml").write_text("keep: me\n")
    assert _run(tmp_path) == 2
    assert "already exists" in capsys.readouterr().err
    assert yaml.safe_load((tmp_path / "golden" / f"{CASE}.yaml").read_text()) == {"keep": "me"}


def test_dry_run_writes_nothing(tmp_path, capsys):
    draft = tmp_path / "drafts" / f"{CASE}.yaml"
    write(draft, fully_decided())
    assert _run(tmp_path, "--dry-run") == 0
    assert "would publish" in capsys.readouterr().out
    assert draft.exists() and not (tmp_path / "golden").exists()


def test_unknown_case_is_refused(tmp_path, capsys):
    write(tmp_path / "drafts" / f"{CASE}.yaml", fully_decided())
    assert _run(tmp_path, "--case", "case_91_nothing") == 2
    assert "no draft" in capsys.readouterr().err


# ----------------------------------------------------------------------------- audit tally (P2-2)


def test_publishing_adds_the_audit_counts_to_the_tally(tmp_path):
    write(tmp_path / "drafts" / f"{CASE}.yaml", fully_decided())
    assert _run(tmp_path) == 0
    tally_path = tmp_path / "golden" / "audit_tally.yaml"
    assert load_audit_tally(tally_path) == {"data_act": AuditResult(2, 2, 0)}
    assert CASE not in tally_path.read_text()  # counts per fixture only
    # A second case of the same proposal adds to it.
    other = "case_91_widget_switching"
    write(tmp_path / "drafts" / f"{other}.yaml", _fully_decided_as(other))
    assert _run(tmp_path) == 0
    assert load_audit_tally(tally_path)["data_act"].sampled > 2


def test_dry_run_leaves_the_tally_alone(tmp_path):
    write(tmp_path / "drafts" / f"{CASE}.yaml", fully_decided())
    assert _run(tmp_path, "--dry-run") == 0
    assert not (tmp_path / "golden" / "audit_tally.yaml").exists()


def test_tallied_errors_escalate_a_later_draft_of_the_same_proposal(tmp_path, capsys):
    write_audit_tally({"data_act": AuditResult(5, 5, 1)}, tmp_path / "golden" / "audit_tally.yaml")
    write(tmp_path / "drafts" / f"{CASE}.yaml", fully_decided())
    assert _run(tmp_path) == 2
    assert "proposal escalated" in capsys.readouterr().err


# ----------------------------------------------------------------------------- holdout registry


def test_a_proposal_sealed_as_holdout_is_never_published(tmp_path, capsys):
    registry = tmp_path / "private" / "holdout_scenarios.yaml"
    registry.parent.mkdir()
    registry.write_text(
        "data_act:\n  - {scenario_id: eval_secret_case_x, description: d, articles: ['1']}\n"
    )
    write(tmp_path / "drafts" / f"{CASE}.yaml", fully_decided())
    assert _run(tmp_path) == 2
    err = capsys.readouterr().err
    assert "'data_act' is registered as a holdout proposal" in err
    assert "eval_secret_case_x" not in err
    assert not (tmp_path / "golden" / f"{CASE}.yaml").exists()


def _fully_decided_as(case_id):
    data = draft_dict(case_id=case_id)
    for section in ("expected_impacts", "important_omissions", "possibly_missing"):
        for item in data[section]:
            for key in ("expected_id", "omission_id", "candidate_id"):
                if key in item:
                    item[key] = item[key].replace("c90_", "c91_")
    data = seal(data)
    for item_id, decision in _human_items(data):
        data = decide(data, item_id, decision, note="dup" if decision == "rejected" else None)
    return data


def _human_items(data):
    for section in ("expected_impacts", "important_omissions", "possibly_missing"):
        for item in data[section]:
            ident = item.get("expected_id") or item.get("omission_id") or item.get("candidate_id")
            if item["review"]["decision"] == "pending" or section == "possibly_missing":
                yield ident, "verified"
