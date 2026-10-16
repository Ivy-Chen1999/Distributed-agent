"""scripts/verify_golden_case.py (U5): local, untraced verification of holdout drafts."""

import importlib.util
import stat
import sys
from unittest.mock import MagicMock

import pytest
import yaml
from langsmith.run_helpers import get_tracing_context

from womm.config import REPO_ROOT
from womm.eval import drafting as drafting_module
from womm.eval.golden import GoldenCase

from .eval.draft_factory import draft_dict, write

spec = importlib.util.spec_from_file_location(
    "verify_golden_case", REPO_ROOT / "scripts/verify_golden_case.py"
)
verify = importlib.util.module_from_spec(spec)
sys.modules["verify_golden_case"] = verify
spec.loader.exec_module(verify)

CASE = "case_90_widget_switching"
ALL_IDS = [f"c90_e0{n}" for n in range(1, 7)] + ["c90_o01", "c90_m01", "c90_m02"]


def _decisions(**overrides):
    d = {i: {"decision": "verified"} for i in ALL_IDS}
    d["c90_m02"] = {"decision": "rejected", "note": "duplicate of c90_e01"}
    d.update(overrides)
    return d


@pytest.fixture(autouse=True)
def cache_root(tmp_path, monkeypatch):
    """tmp_path/cache stands in for the repository's gitignored .cache/."""
    monkeypatch.setattr(drafting_module, "CACHE_DIR", tmp_path / "cache")
    return tmp_path / "cache"


@pytest.fixture
def holdout(tmp_path):
    draft = tmp_path / "cache" / "drafts" / f"{CASE}.yaml"
    write(draft, draft_dict("holdout"))
    return draft


def _argv(tmp_path, draft, *extra):
    return ["--draft", str(draft), "--out-dir", str(tmp_path / "cache" / "handoff"),
            "--ia-root", str(tmp_path / "ia"), "--today", "2026-10-21", *extra]  # fmt: skip


def _decisions_file(tmp_path, decisions):
    path = tmp_path / "decisions.yaml"
    path.write_text(yaml.safe_dump(decisions))
    return path


def test_decisions_file_writes_a_private_handoff(tmp_path, holdout, monkeypatch):
    seen = []
    real_verify = verify.verify

    def spy(*a, **k):
        seen.append(get_tracing_context()["enabled"])
        return real_verify(*a, **k)

    monkeypatch.setattr(verify, "verify", spy)
    dec = _decisions(c90_e05={"decision": "edited", "note": "fixed", "edit": {"impact": "New"}})
    argv = _argv(tmp_path, holdout, "--reviewer", "jdoe",
                 "--decisions", str(_decisions_file(tmp_path, dec)))  # fmt: skip
    assert verify.main(argv, interactive=False) == 0
    assert seen == [False], "verification runs with LangSmith tracing disabled"
    out = tmp_path / "cache" / "handoff" / f"{CASE}.yaml"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    data = yaml.safe_load(out.read_text())
    assert data["format"] == verify.HANDOFF_FORMAT and data["verified_by"] == "jdoe"
    case = GoldenCase.model_validate(data["case"])
    assert case.split == "holdout"
    by_id = {e.expected_id: e for e in case.expected_impacts}
    assert by_id["c90_e05"].impact == "New" and by_id["c90_e05"].provenance == "human_edited"
    assert by_id["c90_e01"].provenance == "human_verified"  # holdout: no llm_judged items
    assert by_id["c90_m01"].provenance == "human_confirmed_candidate"
    assert "c90_m02" not in by_id
    assert holdout.exists(), "the draft stays until the holdout importer seals it"


def test_refuses_non_interactive_without_reviewer_and_decisions(tmp_path, holdout, capsys):
    assert verify.main(_argv(tmp_path, holdout), interactive=False) == 2
    assert "--reviewer and --decisions" in capsys.readouterr().err
    path = _decisions_file(tmp_path, _decisions())
    assert verify.main(_argv(tmp_path, holdout, "--decisions", str(path)), interactive=False) == 2
    assert not (tmp_path / "cache" / "handoff").exists()


def test_every_item_must_be_decided(tmp_path, holdout, capsys):
    dec = _decisions()
    del dec["c90_e01"]  # an auto-accepted item: still needs a human in the holdout
    argv = _argv(tmp_path, holdout, "--reviewer", "jdoe",
                 "--decisions", str(_decisions_file(tmp_path, dec)))  # fmt: skip
    assert verify.main(argv, interactive=False) == 2
    assert "missing ['c90_e01']" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({"decision": "maybe"}, "decision must be one of"),
        ({"decision": "rejected"}, "needs a note"),
        ({"decision": "edited"}, "needs an 'edit'"),
        ({"decision": "verified", "edit": {"impact": "x"}}, "needs decision 'edited'"),
        ({"decision": "edited", "edit": {"expected_id": "x"}}, "cannot be edited"),
        ({"decision": "edited", "edit": {"category": "nope"}}, "not schema-valid"),
        ({"decision": "edited", "edit": {"provision_keys": ["data_act/nope"]}}, "not in scenario"),
    ],
)
def test_bad_decisions_are_refused(tmp_path, holdout, capsys, entry, message):
    path = _decisions_file(tmp_path, _decisions(c90_e02=entry))
    argv = _argv(tmp_path, holdout, "--reviewer", "jdoe", "--decisions", str(path))
    assert verify.main(argv, interactive=False) == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "cache" / "handoff").exists()


def test_too_few_kept_impacts_is_refused(tmp_path, holdout, capsys):
    rejected = {"decision": "rejected", "note": "no"}
    dec = _decisions(**{f"c90_e0{n}": rejected for n in range(1, 6)}, c90_m01=rejected)
    argv = _argv(tmp_path, holdout, "--reviewer", "jdoe",
                 "--decisions", str(_decisions_file(tmp_path, dec)))  # fmt: skip
    assert verify.main(argv, interactive=False) == 2
    assert "only 1 expected impacts" in capsys.readouterr().err


def test_train_draft_is_refused(tmp_path, capsys):
    draft = tmp_path / "cache" / f"{CASE}.yaml"
    write(draft, draft_dict("train"))
    argv = _argv(tmp_path, draft, "--reviewer", "jdoe",
                 "--decisions", str(_decisions_file(tmp_path, _decisions())))  # fmt: skip
    assert verify.main(argv, interactive=False) == 2
    assert "reviewed in a PR" in capsys.readouterr().err


def test_output_under_evals_is_refused(tmp_path, holdout, capsys):
    path = _decisions_file(tmp_path, _decisions())
    argv = ["--draft", str(holdout), "--out-dir", str(REPO_ROOT / "evals" / "golden"),
            "--reviewer", "jdoe", "--decisions", str(path)]  # fmt: skip
    assert verify.main(argv, interactive=False) == 2
    assert "under evals/" in capsys.readouterr().err


def test_interactive_session(tmp_path, holdout, monkeypatch):
    cached = MagicMock(full_text="x widget providers would face cost number 2 under the option y",
                       rsb_text="")  # fmt: skip
    monkeypatch.setattr(verify.ia_sources, "load_cached_ia", lambda fixture, root: cached)
    answers = iter(
        ["jdoe"]  # reviewer
        + ["v", ""] * 1  # e01
        + ["x", "e", "Actor 2b", "", "", "", "", "", "renamed"]  # e02: bad key, then edit
        + ["v", ""] * 4  # e03-e06
        + ["u", "", "u", "ambiguous"]  # o01: note required, then given
        + ["v", ""]  # m01
        + ["r", "duplicate"]  # m02
    )
    shown = []
    code = verify.main(_argv(tmp_path, holdout), interactive=True,
                       ask=lambda _p: next(answers), say=shown.append)  # fmt: skip
    assert code == 0
    assert any("in context" in s and "cost number 2" in s for s in shown)
    case = GoldenCase.model_validate(
        yaml.safe_load((tmp_path / "cache" / "handoff" / f"{CASE}.yaml").read_text())["case"]
    )
    by_id = {e.expected_id: e for e in case.expected_impacts}
    assert by_id["c90_e02"].affected_actor == "Actor 2b"
    assert case.important_omissions == []  # unclear: dropped


def test_interactive_quit_writes_nothing(tmp_path, holdout):
    answers = iter(["jdoe", "q"])
    code = verify.main(_argv(tmp_path, holdout), interactive=True,
                       ask=lambda _p: next(answers), say=lambda _s: None)  # fmt: skip
    assert code == 2 and not (tmp_path / "cache" / "handoff").exists()


def test_draft_and_handoff_must_be_under_the_cache(tmp_path, holdout, capsys):
    """P3-1: holdout plaintext lives only under .cache/ (gitignored)."""
    path = _decisions_file(tmp_path, _decisions())
    outside = tmp_path / "elsewhere" / f"{CASE}.yaml"
    write(outside, draft_dict("holdout"))
    argv = _argv(tmp_path, outside, "--reviewer", "jdoe", "--decisions", str(path))
    assert verify.main(argv, interactive=False) == 2
    assert "only under .cache/" in capsys.readouterr().err
    argv = ["--draft", str(holdout), "--out-dir", str(tmp_path / "handoff"),
            "--reviewer", "jdoe", "--decisions", str(path)]  # fmt: skip
    assert verify.main(argv, interactive=False) == 2
    assert "only under .cache/" in capsys.readouterr().err
    assert not (tmp_path / "handoff").exists()


def test_the_drafter_cannot_verify_a_holdout_draft(tmp_path, holdout, capsys):
    path = _decisions_file(tmp_path, _decisions())
    argv = _argv(tmp_path, holdout, "--reviewer", "case-owner", "--decisions", str(path))
    assert verify.main(argv, interactive=False) == 2
    assert "the drafter cannot be the reviewer" in capsys.readouterr().err
