"""`womm eval --split train` then `womm evolve failures` on the fake backend (U1 integration)."""

import json

from womm import cli
from womm.eval.evaluators import JudgeOutput

from ..eval.test_run_eval_fake import CASE, _script
from ..test_cli import _args, fake_version, use_script  # noqa: F401


def test_train_eval_then_failure_patterns(fake_version, tmp_path, capsys, use_script):  # noqa: F811
    script = _script(reps=2)
    judge = JudgeOutput(
        expected=[{"expected_id": e.expected_id, "covered": False, "impact_id": None,
                   "justification": "j"} for e in CASE.expected_impacts],
        omissions=[{"omission_id": o.omission_id, "addressed": True, "impact_id": None,
                    "justification": "j"} for o in CASE.important_omissions],
    )  # fmt: skip
    script["judge"] = [judge, judge]
    use_script(script)
    code = cli.main(_args(fake_version, tmp_path, "eval", "--split", "train", "--case",
                          CASE.case_id, "--repetitions", "2", "--local", "--json"))  # fmt: skip
    version = json.loads(capsys.readouterr().out)["system_version"]
    assert code == cli.EXIT_OK

    code = cli.main(_args(fake_version, tmp_path, "evolve", "failures", "--json"))
    data = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_OK and data["system_version"] == version
    missed = [p for p in data["patterns"] if p["kind"] == "missed_impact"]
    assert missed and sum(p["persistent_misses"] for p in missed) == len(CASE.expected_impacts)
    assert all(p["cases"] == [CASE.case_id] for p in missed)
