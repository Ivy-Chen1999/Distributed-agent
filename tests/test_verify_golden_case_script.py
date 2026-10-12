"""scripts/verify_golden_case.py (U5): local, untraced verification of holdout drafts."""

import hashlib
import importlib.util
import json
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from langsmith.run_helpers import get_tracing_context

from womm.config import REPO_ROOT
from womm.data.ia_index import IaRecord
from womm.eval import drafting as drafting_module
from womm.eval import reference_check as rc
from womm.eval.golden import GoldenCase
from womm.eval.holdout import HoldoutScenarioSpec, prepare_import

from .eval.draft_factory import KEYS, decide, draft_dict, impact, seal, to_draft, write
from .eval.test_tiebreak import run_holdout, verdict

spec = importlib.util.spec_from_file_location(
    "verify_golden_case", REPO_ROOT / "scripts/verify_golden_case.py"
)
verify = importlib.util.module_from_spec(spec)
sys.modules["verify_golden_case"] = verify
spec.loader.exec_module(verify)
_spec = importlib.util.spec_from_file_location(
    "import_holdout_case", REPO_ROOT / "scripts/import_holdout_case.py"
)
importer = importlib.util.module_from_spec(_spec)
sys.modules["import_holdout_case"] = importer
_spec.loader.exec_module(importer)

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


# ----------------------------------------------------------------------------- spot check

SPOT_SCENARIO = "eval_spot_sealed"


def _tiebroken_holdout(extra_impacts: int = 8) -> dict:
    """A holdout draft on a local scenario, with ``extra_impacts`` more judge-agreed impacts,
    settled by the tie-break (e05 kept, m01 and m02 dropped)."""
    data = draft_dict("holdout")
    data["scenario_id"] = SPOT_SCENARIO
    data["expected_impacts"] += [impact(n) for n in range(7, 7 + extra_impacts)]
    ruled, _ = run_holdout(
        to_draft(seal(data)),
        [verdict("c90_e05"), verdict("c90_m01", "drop", reason="repeats c90_e01")],
    )
    return ruled.model_dump(mode="json")


@pytest.fixture
def settled(tmp_path):
    draft = tmp_path / "cache" / "drafts" / f"{CASE}.yaml"
    write(draft, _tiebroken_holdout())
    return draft


def _reference_round(tmp_path, wrong: int = 0, name: str = "round") -> Path:
    """A scored reference-check round of 20 items, ``wrong`` of them labelled against the tool."""
    directory = tmp_path / name
    directory.mkdir()
    items = {f"r{n:02d}": {"case_id": "case_10_x", "fixture": "f", "item_id": f"i{n}",
                           "stratum": "judges_accepted", "tool_kept": True, "tool_reason": "",
                           "annotator": "ana"} for n in range(1, 21)}  # fmt: skip
    key = {"sample": "2026-10-01-abcd1234", "annotators": ["ana"], "items": items}
    (directory / rc.KEY_FILE).write_text(json.dumps(key))
    answers = [{"item": iid, "label": "drop" if n < wrong else "keep"}
               for n, iid in enumerate(items)]  # fmt: skip
    (directory / "answers_ana.yaml").write_text(
        yaml.safe_dump({"sample": key["sample"], "annotator": "ana", "answers": answers})
    )
    return directory


def _spot_argv(tmp_path, draft, round_dir, *extra):
    return _argv(tmp_path, draft, "--reviewer", "jdoe", "--spot-check",
                 "--reference-check", str(round_dir), *extra)  # fmt: skip


def _session(answers_for):
    """``ask`` answering each shown item with ``answers_for(item_id)``; records the items."""
    shown, current = [], {}

    def say(line):
        if line.startswith("=== ["):
            current["id"] = line.split()[2]
            shown.append(current["id"])

    queue: list[str] = []

    def ask(prompt):
        if not queue:
            queue.extend(answers_for(current["id"]))
        return queue.pop(0)

    return ask, say, shown


DROPPED = {"c90_m01", "c90_m02"}  # by the tie-break in ``_tiebroken_holdout``


def _agree(iid):
    """The owner agreeing with the tool: keep what it kept, reject what it dropped."""
    return ["r", "rightly dropped"] if iid in DROPPED else ["v", ""]


def test_spot_check_refuses_without_a_robust_reference_check(tmp_path, settled, capsys):
    argv = _argv(tmp_path, settled, "--reviewer", "jdoe", "--spot-check")
    assert verify.main(argv, interactive=True, ask=_session(_agree)[0]) == 2
    assert "needs --reference-check" in capsys.readouterr().err
    bad = _reference_round(tmp_path, wrong=3)  # 15% > 10%
    ask, say, shown = _session(_agree)
    assert verify.main(_spot_argv(tmp_path, settled, bad), interactive=True, ask=ask, say=say) == 2
    assert "is not ROBUST" in capsys.readouterr().err and shown == []
    (bad / "answers_ana.yaml").unlink()
    assert verify.main(_spot_argv(tmp_path, settled, bad), interactive=True, ask=ask, say=say) == 2
    assert "no answers yet from ana" in capsys.readouterr().err
    empty = tmp_path / "empty"
    empty.mkdir()
    assert verify.main(_spot_argv(tmp_path, settled, empty), interactive=True, ask=ask) == 2
    assert "cannot score the reference check" in capsys.readouterr().err
    assert not (tmp_path / "cache" / "handoff").exists()


def test_spot_check_refuses_a_draft_the_tool_has_not_settled(tmp_path, holdout, capsys):
    round_dir = _reference_round(tmp_path)
    ask, say, shown = _session(_agree)
    argv = _spot_argv(tmp_path, holdout, round_dir)
    assert verify.main(argv, interactive=True, ask=ask, say=say) == 2
    assert "not tie-broken" in capsys.readouterr().err and shown == []
    # Tie-broken, but one item decided by hand since: not the tool's.
    data = decide(_tiebroken_holdout(), "c90_e01", "verified", reviewer="jdoe")
    write(holdout, data)
    assert verify.main(argv, interactive=True, ask=ask, say=say) == 2
    assert "not settled by the tool: ['c90_e01']" in capsys.readouterr().err and shown == []


def test_spot_check_is_interactive_only(tmp_path, settled, capsys):
    argv = _spot_argv(tmp_path, settled, _reference_round(tmp_path))
    assert verify.main(argv, interactive=False) == 2
    assert "interactive only" in capsys.readouterr().err


def test_spot_check_asks_only_the_seeded_stratified_sample(tmp_path, settled):
    ask, say, shown = _session(_agree)
    argv = _spot_argv(tmp_path, settled, _reference_round(tmp_path))
    assert verify.main(argv, interactive=True, ask=ask, say=say) == 0
    draft = verify.load_holdout_draft(settled)
    expected = [i.item_id for i in verify.spot_sample(draft)]
    assert shown == expected and len(shown) == verify.SPOT_SAMPLE < len(draft.items())
    strata = {rc.stratum(i) for i in draft.items() if i.item_id in shown}
    assert strata == set(rc.STRATA), "kept by judges, kept and dropped by the tie-break"
    assert expected == [i.item_id for i in verify.spot_sample(draft)], "the sample is seeded"


def test_spot_check_with_any_disagreement_is_refused(tmp_path, settled, capsys):
    draft = verify.load_holdout_draft(settled)
    kept = [i.item_id for i in verify.spot_sample(draft) if i.review.decision != "llm_dropped"]
    ask, say, _ = _session(lambda iid: ["r", "wrong"] if iid in kept[:1] else _agree(iid))
    argv = _spot_argv(tmp_path, settled, _reference_round(tmp_path))
    assert verify.main(argv, interactive=True, ask=ask, say=say) == 2
    err = capsys.readouterr().err
    n = verify.SPOT_SAMPLE
    assert f"disagreed with the tool on 1 of {n} sampled items {sorted(kept[:1])}" in err
    assert "without --spot-check" in err
    assert not (tmp_path / "cache" / "handoff").exists()


def test_spot_check_writes_a_handoff_the_importer_accepts(tmp_path, settled, monkeypatch):
    draft = verify.load_holdout_draft(settled)
    sample = verify.spot_sample(draft)
    dropped = next(i.item_id for i in sample if i.review.decision == "llm_dropped")
    kept = next(i.item_id for i in sample if i.review.decision == "auto_accepted")
    # No disagreement is allowed: the kept item is verified; rejecting a dropped item agrees.
    answers = {kept: ["v", ""], dropped: ["r", "duplicate"]}
    ask, say, shown = _session(lambda iid: answers.get(iid) or _agree(iid))
    round_dir = _reference_round(tmp_path, wrong=2)  # 10%: at the limit, still robust
    assert verify.main(_spot_argv(tmp_path, settled, round_dir), interactive=True, ask=ask,
                       say=say) == 0  # fmt: skip
    path = tmp_path / "cache" / "handoff" / f"{CASE}.yaml"
    handoff = yaml.safe_load(path.read_text())
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert handoff["spot_check"] == {
        "sample_size": verify.SPOT_SAMPLE, "seed": verify.spot_seed_for(CASE),
        "items": sorted(shown),
        "disagreements": [], "reference_check": "2026-10-01-abcd1234",
    }  # fmt: skip
    case = GoldenCase.model_validate(handoff["case"])
    by_id = {e.expected_id: e for e in case.expected_impacts}
    assert by_id[kept].provenance == "human_verified"
    e05 = "human_verified" if "c90_e05" in shown else "llm_tiebroken"
    assert by_id["c90_e05"].provenance == e05
    unsampled = next(i.item_id for i in draft.expected_impacts if i.item_id not in shown)
    assert by_id[unsampled].provenance == "llm_judged", "the tool's decision stands"
    assert f"Holdout spot check by jdoe: {verify.SPOT_SAMPLE} tool-decided items" in case.notes
    assert "spot-checked on 2026-10-21" in case.notes and "2026-10-01-abcd1234" in case.notes

    # The importer takes it unchanged.
    monkeypatch.setattr(importer, "EVALS_DIR", REPO_ROOT / "evals")
    spec_ = HoldoutScenarioSpec(scenario_id=SPOT_SCENARIO, description="Sealed switching",
                                articles=[k.rsplit("/", 1)[1] for k in KEYS])  # fmt: skip
    record = IaRecord(celex="52099PC0009", ia_celex="52099SC0009", ia_date="2026-01-15")
    golden = tmp_path / "golden"
    golden.mkdir()
    bundle = prepare_import(importer.read_handoff(path), {"data_act": [spec_]},
                            {"data_act": record}, golden_dir=golden)  # fmt: skip
    assert bundle.case == case and bundle.verified_by == "jdoe"
    assert bundle.draft_sha256 == hashlib.sha256(settled.read_bytes()).hexdigest()


def test_reference_check_without_spot_check_is_refused(tmp_path, holdout, capsys):
    argv = _argv(tmp_path, holdout, "--reference-check", str(tmp_path))
    assert verify.main(argv, interactive=True, ask=lambda _p: "q") == 2
    assert "goes with --spot-check" in capsys.readouterr().err
