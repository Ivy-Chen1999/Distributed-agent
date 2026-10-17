"""Oracle check: each golden case's own reference answer, scored by a (fake) coverage judge."""

import importlib.util
import json
import shutil
import sys

import pytest
import yaml

from womm.config import REPO_ROOT
from womm.eval.drafting import GoldenDraft
from womm.eval.evaluators import JudgeOutput, judge_input
from womm.eval.golden import GOLDEN_DIR, GoldenError, load_all_golden
from womm.eval.oracle_check import case_from_draft, check_case, oracle_run
from womm.llm.fake import FakeBackend
from womm.models.system_version import RoleConfig

from .draft_factory import draft_dict

spec = importlib.util.spec_from_file_location("oracle_check", REPO_ROOT / "scripts/oracle_check.py")
oracle_script = importlib.util.module_from_spec(spec)
sys.modules["oracle_check"] = oracle_script
spec.loader.exec_module(oracle_script)

CASES = load_all_golden()
CASE = next(c for c in CASES if c.case_id.startswith("case_02"))
ROLE = RoleConfig(backend="fake", model="m", prompt="prompts/judge_coverage.md")


def fake_judge(uncovered: frozenset[str] = frozenset(), unaddressed=frozenset()) -> FakeBackend:
    """Judges every expected impact covered and every omission addressed, except the given ids.
    Reads the ids from the judge input, so it serves any number of cases."""

    def verdict(_system: str, user: str) -> JudgeOutput:
        rest = user.split("\n\nExpected impacts:\n", 1)[1]
        expected, omissions = rest.split("\n\nImportant omissions:\n", 1)
        return JudgeOutput(
            expected=[
                {"expected_id": e["expected_id"], "covered": e["expected_id"] not in uncovered,
                 "impact_id": None, "justification": f"why {e['expected_id']}"}
                for e in json.loads(expected)
            ],
            omissions=[
                {"omission_id": o["omission_id"], "addressed": o["omission_id"] not in unaddressed,
                 "impact_id": None, "justification": f"why {o['omission_id']}"}
                for o in json.loads(omissions)
            ],
        )  # fmt: skip

    return FakeBackend({"judge": [verdict] * 50})


def test_oracle_dossier_holds_every_expected_impact_and_omission():
    run = oracle_run(CASE)
    findings = [f for i in run.dossier.impacts for f in i.findings]
    got = {(f.affected_actor, f.mechanism, f.impact) for f in findings}
    assert got == {(e.affected_actor, e.mechanism, e.impact) for e in CASE.expected_impacts} | {
        ("", o.description, o.description) for o in CASE.important_omissions
    }  # an omission is a finding too: the judge discounts an impact without findings
    summaries = {i.summary for i in run.dossier.impacts}
    assert {o.description for o in CASE.important_omissions} <= summaries
    # Neutral impact ids: the judge must match on content, not on ids.
    shown = json.loads(judge_input(CASE, run).split("\n\nExpected impacts:\n")[0].split("\n", 1)[1])
    ids = {e.expected_id for e in CASE.expected_impacts} | {
        o.omission_id for o in CASE.important_omissions
    }
    assert not ids & {i["impact_id"] for i in shown}


async def test_all_covered_passes():
    result = await check_case(CASE, fake_judge(), ROLE, "p")
    assert result.passed and result.coverage == 1.0
    assert result.uncovered == {} and result.unaddressed == {}


async def test_one_uncovered_impact_fails_and_is_named():
    target = CASE.expected_impacts[1].expected_id
    result = await check_case(CASE, fake_judge(frozenset({target})), ROLE, "p")
    assert not result.passed and result.coverage < 1.0
    assert result.uncovered == {target: f"why {target}"}


async def test_one_unaddressed_omission_fails():
    if not CASE.important_omissions:
        pytest.skip("case has no omissions")
    target = CASE.important_omissions[0].omission_id
    result = await check_case(CASE, fake_judge(unaddressed=frozenset({target})), ROLE, "p")
    assert not result.passed and result.unaddressed == {target: f"why {target}"}


async def test_holdout_case_refused():
    holdout = CASE.model_copy(update={"split": "holdout"})
    with pytest.raises(GoldenError, match="holdout"):
        await check_case(holdout, fake_judge(), ROLE, "p")


def test_holdout_draft_refused():
    with pytest.raises(GoldenError, match="holdout"):
        case_from_draft(GoldenDraft.model_validate(draft_dict("holdout")))


def test_draft_case_keeps_pending_and_accepted_items_only():
    case = case_from_draft(GoldenDraft.model_validate(draft_dict("train")))
    ids = [e.expected_id for e in case.expected_impacts]
    # e05 is still pending (kept for the check); candidates count only once a person or the
    # tie-break accepted them, as in a publish.
    assert "c90_e05" in ids and not {"c90_m01", "c90_m02"} & set(ids)
    assert [o.omission_id for o in case.important_omissions] == ["c90_o01"]


# ------------------------------------------------------------------------------- the script


@pytest.fixture
def script(monkeypatch):
    def use(backend: FakeBackend):
        async def fake_backend(role, skip_self_check):
            return backend

        monkeypatch.setattr(oracle_script, "judge_backend", fake_backend)

    return use


def _golden_dir(tmp_path):
    golden = tmp_path / "golden"
    golden.mkdir()
    shutil.copy(GOLDEN_DIR / f"{CASE.case_id}.yaml", golden)
    return golden


def test_script_exit_0_when_every_case_passes(script, tmp_path, capsys):
    script(fake_judge())
    argv = ["--golden-dir", str(_golden_dir(tmp_path)), "--holdout-registry", str(tmp_path / "r")]
    assert oracle_script.main(argv) == 0
    assert f"PASS {CASE.case_id}" in capsys.readouterr().out


def test_script_exit_1_names_the_failed_id(script, tmp_path, capsys):
    target = CASE.expected_impacts[0].expected_id
    script(fake_judge(frozenset({target})))
    argv = ["--golden-dir", str(_golden_dir(tmp_path)), "--holdout-registry", str(tmp_path / "r")]
    assert oracle_script.main(argv) == 1
    out = capsys.readouterr().out
    assert f"FAIL {CASE.case_id}" in out and f"not covered {target}: why {target}" in out


def test_script_runs_drafts(script, tmp_path, capsys):
    script(fake_judge())
    path = tmp_path / "case_90_widget_switching.yaml"
    path.write_text(yaml.safe_dump(draft_dict("train"), sort_keys=False))
    assert oracle_script.main([str(path), "--holdout-registry", str(tmp_path / "r")]) == 0
    assert "PASS case_90_widget_switching" in capsys.readouterr().out


def test_script_exit_2_on_holdout_draft(script, tmp_path, capsys):
    script(fake_judge())
    path = tmp_path / "case_90_widget_switching.yaml"
    path.write_text(yaml.safe_dump(draft_dict("holdout"), sort_keys=False))
    assert oracle_script.main([str(path), "--holdout-registry", str(tmp_path / "r")]) == 2
    assert "holdout" in capsys.readouterr().err


def test_script_exit_2_on_fixture_registered_as_holdout(script, tmp_path, capsys):
    script(fake_judge())
    registry = tmp_path / "holdout_scenarios.yaml"
    registry.write_text(yaml.safe_dump({CASE.fixture: {}}))
    argv = ["--golden-dir", str(_golden_dir(tmp_path)), "--holdout-registry", str(registry)]
    assert oracle_script.main(argv) == 2
    assert "holdout" in capsys.readouterr().err


def test_script_exit_2_on_missing_draft(script, tmp_path):
    script(fake_judge())
    assert oracle_script.main([str(tmp_path / "nope.yaml")]) == 2
