"""The R34 formal noise chain end to end through the real CLI (U7; R34): `womm eval --split val
--repetitions 3 --formal` on the api twin writes a real report and logs itself in
`formal_runs`; `womm noise report --record` applies the stop rule to that report; on "extend",
the extension run and the pooled `--report first --report second --record` follow the human
steps (step 7) and the runbook (Path C, step 3) command for command; the gate's mode choice and
preflight then read the records.

Every LLM call is a scripted fake on the api backend; no network, no sealed holdout material, no
LLM spend. The seams: the backend factory, the golden loader (synthetic val copies of a public
case), the LangSmith experiment recorder (a name, no network), and a fake git: ``code_identity``
reports the records file dirty until the test "commits" it, and ``same_code`` sees the
records-only commits in between."""

import asyncio
import json
import shutil

import pytest

from womm import cli
from womm.config import REPO_ROOT, load_settings
from womm.eval import noise_report as nr
from womm.eval.evaluators import judge_version
from womm.eval.golden import load_all_golden
from womm.evolve import promotion as pm
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity
from womm.models.system_version import load_system_version

from ..graph.conftest import good_script
from . import holdout_factory as hf

INCUMBENT = REPO_ROOT / "system_versions/v1.0-unscoped-api.yaml"
CANDIDATE = REPO_ROOT / "system_versions/v1.0-scoped-api.yaml"
BASE = next(c for c in load_all_golden() if c.scenario_id == "eval_sme_impacts")  # 6 impacts
N_VAL = 4
STEPS = 1000
JUDGE_USD, ROLE_USD = 0.01, 0.02
# Holdout counts as the human steps pass them (step 7: 8 cases over 8 proposals).
HOLDOUT = ["--holdout-cases", "8", "--holdout-proposals", "8"]


# ----------------------------------------------------------------------------- seams


class Git:
    """A fake repository for the records file: dirty until committed; each commit moves HEAD,
    and the commits in between change only the records file (``same_code``)."""

    def __init__(self, records):
        self.records = records
        self.shas = ["sha_000"]
        self.committed = records.read_text()

    @property
    def head(self) -> str:
        return self.shas[-1]

    def code_identity(self, *_a, **_k) -> CodeIdentity:
        return CodeIdentity(git_sha=self.head, dirty=self.records.read_text() != self.committed)

    def commit(self) -> None:
        assert self.records.read_text() != self.committed, "nothing to commit"
        self.committed = self.records.read_text()
        self.shas.append(f"sha_{len(self.shas):03d}")

    def changed_files(self, a, b):
        return [nr.RECORDS_FILE] if a in self.shas and b in self.shas else None


class CostedFake(FakeBackend):
    """A scripted backend that reports a list price per call, as the api backend does."""

    async def _invoke(self, role_name, *args, **kwargs):
        step, usage = await super()._invoke(role_name, *args, **kwargs)
        usage.cost_usd = JUDGE_USD if role_name == "judge" else ROLE_USD
        return step, usage


def _judge(counts):
    """The coverage judge: its n-th call covers the first ``counts[n]`` of the 6 expected
    impacts. Cases and repetitions run in order, so ``counts`` lists case by case."""
    calls = iter(counts)

    def judge(_system, user):
        k = next(calls)
        expected = json.loads(user.split("Expected impacts:\n", 1)[1].split("\n\nImportant")[0])
        omissions = json.loads(user.split("Important omissions:\n", 1)[1])
        return {
            "expected": [{"expected_id": e["expected_id"], "covered": i < k, "impact_id": "I1",
                          "justification": "j"} for i, e in enumerate(expected)],
            "omissions": [{"omission_id": o["omission_id"], "addressed": False,
                           "impact_id": None, "justification": "j"} for o in omissions],
        }  # fmt: skip

    return judge


def _api_backend(counts) -> CostedFake:
    s = good_script()
    script = {k: v * STEPS for k, v in s.items() if k != "synthesis"}
    script |= {"synthesis": [hf._synth("BASELINE")] * STEPS, "judge": [_judge(counts)] * STEPS}
    return CostedFake(script)


class Backends:
    """The CLI's backend factory: every api role gets ``self.api``."""

    def __init__(self):
        self.api = None
        self.prepared = 0

    async def __call__(self, sv, *, skip_self_check=False, settings=None):
        assert {r.backend for r in sv.spec.roles().values()} == {"api"}
        self.prepared += 1
        return {"api": self.api}, None, None


@pytest.fixture
def env(monkeypatch, tmp_path):
    policy, records = tmp_path / "promotion_policy.yaml", tmp_path / "promotion_records.yaml"
    shutil.copy(pm.POLICY_PATH, policy)
    shutil.copy(pm.RECORDS_PATH, records)
    monkeypatch.setattr(pm, "RECORDS_PATH", records)  # the committed file is never written
    monkeypatch.setattr(pm, "POLICY_PATH", policy)
    git = Git(records)
    backends = Backends()
    val = [BASE.model_copy(update={"case_id": f"case_9{i}_val", "split": "val"})
           for i in range(N_VAL)]  # fmt: skip
    monkeypatch.setattr(cli, "load_all_golden", lambda *_a, **_k: [*load_all_golden(), *val])
    monkeypatch.setattr(cli, "prepare_backends", backends)
    monkeypatch.setattr(cli, "code_identity", git.code_identity)
    monkeypatch.setattr(nr, "_changed_files", git.changed_files)
    # LangSmith: a key so the eval records an experiment, but the recorder is a stub and
    # tracing stays off (no network); no Postgres.
    monkeypatch.setattr(cli, "load_dotenv", lambda *_a, **_k: None)
    for name in ("LANGSMITH_API_KEY", "LANGCHAIN_API_KEY", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setattr(cli, "load_settings",
                        lambda: load_settings({"LANGSMITH_API_KEY": "test-key"}))  # fmt: skip
    import langsmith

    monkeypatch.setattr(langsmith, "Client", lambda *_a, **_k: object())
    monkeypatch.setattr(cli, "sync_dataset", lambda *_a, **_k: None)  # the pre-spend check
    experiments = []

    async def record(report, cases, client, prefix):
        experiments.append(f"{prefix}-{len(experiments) + 1}")
        return experiments[-1]

    monkeypatch.setattr(cli, "record_langsmith_experiment", record)
    return {"policy": policy, "records": records, "git": git, "backends": backends,
            "runs": tmp_path / "runs", "experiments": experiments}  # fmt: skip


def _formal_eval(env, capsys, counts, runs_dir):
    """`womm eval --system-version <api twin> --split val --repetitions 3 --formal` (step 7)."""
    env["backends"].api = _api_backend(counts)
    argv = ["eval", "--system-version", str(INCUMBENT), "--split", "val", "--repetitions", "3",
            "--formal", "--records", str(env["records"]), "--runs-dir", str(runs_dir),
            "--json"]  # fmt: skip
    rc = cli.main(argv)
    out, err = capsys.readouterr()
    return rc, (json.loads(out) if rc == cli.EXIT_OK else None), err


def _noise(env, capsys, *reports):
    """`womm noise report --report <first> [--report <second>] --holdout-cases 8
    --holdout-proposals 8 --record`."""
    argv = ["noise", "report", *[a for r in reports for a in ("--report", str(r))], *HOLDOUT,
            "--record", "--records", str(env["records"]), "--policy", str(env["policy"]),
            "--json"]  # fmt: skip
    rc = cli.main(argv)
    out, err = capsys.readouterr()
    return rc, (json.loads(out) if rc == cli.EXIT_OK else None), err


def _calibrated(env, jv):
    """A passing calibration for the incumbent's judge (recorded as `calibrate score --record`
    would; the calibration itself is covered by test_gate_cli_e2e)."""
    pm.append_records(env["records"], "judge_calibrations", [
        {"judge_version": jv, "agreement": 0.9, "pairs": 30, "annotators": ["ann", "bob"],
         "recorded_on": "2026-10-15", "inter_annotator_agreement": 0.9}])  # fmt: skip
    env["git"].commit()


class Store:
    """The holdout store as preflight reads it: counts and budget only (no comparison)."""

    async def sealed_counts(self):
        return (8, 8)

    async def budget_used(self, _cycle_id):
        return (0, 0)


def _preflight(env):
    policy, _ = pm.load_policy(env["policy"])
    records = pm.load_records(env["records"])
    cand = load_system_version(CANDIDATE, REPO_ROOT)
    inc = load_system_version(INCUMBENT, REPO_ROOT)
    return asyncio.run(pm.preflight(policy, records, cand, inc, store=Store(), cycle_id="c1",
                                    policy_committed=True))  # fmt: skip


def _per_case(*runs):
    """Judge counts for N_VAL cases, case by case; ``runs[i]`` are case i's three runs (the last
    pattern repeats for the remaining cases)."""
    runs = [*runs, *[runs[-1]] * (N_VAL - len(runs))]
    return [k for case in runs for k in case]


# ----------------------------------------------------------------------------- tests


def test_formal_eval_logs_itself_and_a_dirty_records_file_blocks_the_next_formal_run(env, capsys):
    jv = judge_version(load_system_version(INCUMBENT, REPO_ROOT))
    rc, data, err = _formal_eval(env, capsys, _per_case([4, 4, 5], [4, 4, 4]), env["runs"])
    assert rc == cli.EXIT_OK, err
    meta = data["metadata"]
    assert meta["formal"] is True and meta["run_kind"] == "r34_noise"
    assert meta["backends"] == ["api"] and meta["split"] == "val" and meta["repetitions"] == 3
    assert meta["git_sha"] == "sha_000" and meta["git_dirty"] is False
    assert meta["langsmith_experiment"] == "womm-v1.0-unscoped-api-val-formal-1"
    assert len(data["scores"]) == N_VAL * 3 and not data["aborted"]
    assert f"logged the formal run in {env['records']}; commit it" in err
    report = env["runs"] / data["report_path"].rsplit("/", 1)[-1]
    assert json.loads(report.read_text())["metadata"] == meta  # the experiment name included
    assert nr.formal_problems(json.loads(report.read_text())) == []

    # The judge's list price reaches the report (per run and in the summary).
    assert all(s["judge_cost_usd"] == JUDGE_USD for s in data["scores"])
    assert all(s["cost_usd"] > 0 for s in data["scores"])
    assert data["summary"]["judge_cost_usd"] == pytest.approx(JUDGE_USD)

    (logged,) = pm.load_records(env["records"]).formal_runs
    assert logged.run_key == meta["langsmith_experiment"] and logged.report == report.name
    assert (logged.judge_version, logged.git_sha, logged.repetitions) == (jv, "sha_000", 3)
    assert (logged.split, logged.system_version) == ("val", data["system_version"])

    # The next formal run refuses the uncommitted records file before any LLM call.
    rc, _, err = _formal_eval(env, capsys, [], env["runs"] / "again")
    assert rc == cli.EXIT_USAGE and "dirty working tree" in err
    assert env["backends"].api.calls == []
    assert len(pm.load_records(env["records"]).formal_runs) == 1

    # The logged run alone blocks the gate until an R34 attempt reads it.
    assert pm.load_records(env["records"]).noise_chain_problems(jv) == [
        f"formal run(s) {logged.run_key} for judge {jv} are not part of the recorded R34 "
        "chain: every formal run is counted"
    ]


def test_a_quiet_first_run_stops_and_the_gate_goes_statistical(env, capsys):
    jv = judge_version(load_system_version(INCUMBENT, REPO_ROOT))
    _calibrated(env, jv)
    # One case of four varies by one impact: a pooled SD well below the SD star.
    rc, data, err = _formal_eval(env, capsys, _per_case([4, 4, 5], [4, 4, 4]), env["runs"])
    assert rc == cli.EXIT_OK, err
    first = env["runs"] / data["report_path"].rsplit("/", 1)[-1]
    env["git"].commit()  # step 7: commit the logged run

    rc, result, err = _noise(env, capsys, first)
    assert rc == cli.EXIT_OK, err
    rule = result["stop_rule"]
    assert result["attempt"] == {"kind": "first", "next_step": "stop"}
    assert rule["next_step"] == "stop" and rule["interval"][1] < rule["sd_star"]
    assert set(result["recorded"]) == {"noise_attempts", "formal_noise_runs", "mdd_reports"}

    records = pm.load_records(env["records"])
    (attempt,) = records.noise_chain(jv)
    assert (attempt.kind, attempt.next_step) == ("first", "stop")
    assert attempt.git_sha == data["metadata"]["git_sha"]
    assert attempt.run_keys == [data["metadata"]["langsmith_experiment"]]
    assert records.noise_chain_problems(jv) == []
    mdd = records.mdd("coverage", jv)
    assert (mdd.repetitions, mdd.holdout_cases, mdd.holdout_proposals) == (3, 8, 8)
    policy, _ = pm.load_policy(env["policy"])
    assert mdd.mdd <= policy.expected_gain
    env["git"].commit()

    choice = _preflight(env)
    assert (choice.mode, choice.deployable, choice.notes) == ("statistical", True, [])

    # A stopped chain takes no extension, and its first attempt is never restarted.
    rc, _, err = _noise(env, capsys, first)
    assert rc == cli.EXIT_USAGE and "it is never restarted" in err


def test_a_noisy_first_run_extends_and_the_pooled_chain_sets_the_mode(env, capsys):
    jv = judge_version(load_system_version(INCUMBENT, REPO_ROOT))
    _calibrated(env, jv)
    # Every case varies by one impact: the SD interval straddles the SD star.
    rc, data, err = _formal_eval(env, capsys, _per_case([4, 4, 5]), env["runs"] / "first")
    assert rc == cli.EXIT_OK, err
    first = env["runs"] / "first" / data["report_path"].rsplit("/", 1)[-1]
    env["git"].commit()

    rc, result, err = _noise(env, capsys, first)
    assert rc == cli.EXIT_OK, err
    rule = result["stop_rule"]
    assert result["attempt"] == {"kind": "first", "next_step": "extend"}
    assert rule["interval"][0] < rule["sd_star"] < rule["interval"][1]
    assert set(result["recorded"]) == {"noise_attempts"}  # no noise run or MDD yet
    env["git"].commit()

    # The gate refuses the chain that asked for an extension.
    records = pm.load_records(env["records"])
    assert records.noise_chain_problems(jv) == [
        f"the R34 run for judge {jv} asked for an extension that is not recorded: run the "
        "extension (or the fallback) first"
    ]
    with pytest.raises(pm.GateRefused, match="no R34 formal noise run on record"):
        _preflight(env)

    # The extension: 3 more on the same code (HEAD moved only by records commits).
    rc, data2, err = _formal_eval(env, capsys, _per_case([4, 5, 5]), env["runs"] / "second")
    assert rc == cli.EXIT_OK, err
    second = env["runs"] / "second" / data2["report_path"].rsplit("/", 1)[-1]
    assert data2["metadata"]["git_sha"] != data["metadata"]["git_sha"]
    env["git"].commit()

    rc, result, err = _noise(env, capsys, first, second)
    assert rc == cli.EXIT_OK, err
    assert result["attempt"] == {"kind": "extension", "next_step": "final"}
    assert result["noise_run"]["repetitions"] == 6
    assert result["batch_shift"]["flag"] is False
    env["git"].commit()

    records = pm.load_records(env["records"])
    assert [(a.kind, a.next_step) for a in records.noise_chain(jv)] == [
        ("first", "extend"), ("extension", "final")]  # fmt: skip
    assert records.noise_chain(jv)[1].run_keys == [
        data["metadata"]["langsmith_experiment"],
        data2["metadata"]["langsmith_experiment"],
    ]
    assert records.noise_chain_problems(jv) == []
    run = records.noise_run(jv)
    assert run.repetitions == 6 and run.batch_shift is False
    mdd = records.mdd("coverage", jv)
    assert (mdd.repetitions, mdd.holdout_cases, mdd.holdout_proposals) == (3, 8, 8)

    # The pooled noise (6 runs a case) puts the MDD at 3 repetitions just above expected_gain.
    policy, _ = pm.load_policy(env["policy"])
    assert mdd.mdd > policy.expected_gain
    choice = _preflight(env)
    assert (choice.mode, choice.deployable) == ("weak", True)
    assert choice.notes == [f"weak mode: the minimum detectable delta ({mdd.mdd:.3f}) exceeds "
                            f"the expected gain ({policy.expected_gain:.3f})"]  # fmt: skip

    # The first attempt is never restarted, and the chain takes no second extension.
    rc, _, err = _noise(env, capsys, first)
    assert rc == cli.EXIT_USAGE and "it is never restarted" in err
    rc, _, err = _noise(env, capsys, first, second)
    assert rc == cli.EXIT_USAGE and "already has its extension or fallback" in err
    assert pm.load_records(env["records"]).noise_chain_problems(jv) == []


def test_a_formal_run_whose_langsmith_recording_fails_is_logged_and_exits_failed(
    env, capsys, monkeypatch
):
    """Paid for, so it is logged (every finished formal run is); but without its experiment an
    R34 extension would refuse it, so the command fails loudly instead of warning."""

    async def broken(*_a, **_k):
        raise RuntimeError("langsmith down")

    monkeypatch.setattr(cli, "record_langsmith_experiment", broken)
    rc, _, err = _formal_eval(env, capsys, _per_case([4, 4, 5], [4, 4, 4]), env["runs"])
    assert rc == cli.EXIT_FAILED
    assert "no LangSmith experiment" in err and "extension" in err
    assert "logged the formal run" in err
    assert len(pm.load_records(env["records"]).formal_runs) == 1
