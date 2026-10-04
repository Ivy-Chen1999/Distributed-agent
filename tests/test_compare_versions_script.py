"""scripts/compare_versions.py: scoped vs unscoped over pooled eval reports (U6)."""

import importlib.util
import json
import sys

import pytest

from womm.config import REPO_ROOT

spec = importlib.util.spec_from_file_location(
    "compare_versions", REPO_ROOT / "scripts/compare_versions.py"
)
compare_versions = importlib.util.module_from_spec(spec)
sys.modules["compare_versions"] = compare_versions
spec.loader.exec_module(compare_versions)

CompareError = compare_versions.CompareError
SCOPED, UNSCOPED = "sv_scoped0001", "sv_unscoped01"
CASES = {"case_01": "eval_provider_compliance_costs", "case_02": "eval_sme_impacts"}
SCENARIOS = set(CASES.values())
RULE = (
    "if scoped coverage is more than one noise band below unscoped → revise default scopes "
    "before v1.0-scoped is used further; otherwise keep scopes"
)


def _score(case_id, coverage, *, omissions=0.75, grounding=1.0, outcome="scored"):
    return {
        "case_id": case_id, "scenario_id": CASES[case_id], "run_id": "r", "outcome": outcome,
        "coverage": coverage, "omissions_addressed": omissions, "grounding": grounding,
        "latency_s": 100.0, "tokens": 1000, "cost_usd": 0.5,
    }  # fmt: skip


def _report(sv, coverages, *, router="shadow/stub", backends=("api",), aborted=None):
    """One eval report: ``coverages`` maps case id to the coverage of each repetition."""
    scores = [_score(c, v) for c, vals in coverages.items() for v in vals]
    return {
        "system_version": sv,
        "metadata": {"system_version": sv, "router": router, "backends": list(backends),
                     "system_version_file": f"system_versions/{sv}.yaml"},
        "aborted": aborted,
        "scores": scores,
    }  # fmt: skip


def _named(*reports):
    return [(f"eval_{i}_{r['system_version']}.json", r) for i, r in enumerate(reports)]


SAME = [0.7, 0.6, 0.7, 0.8, 0.7, 0.6]  # mean 0.683, sd 0.075


def _compare(reports, **kw):
    kw.setdefault("ai_act_scenarios", SCENARIOS)
    return compare_versions.compare(reports, SCOPED, UNSCOPED, **kw)


def test_happy_path_gives_a_verdict_per_metric_and_keeps_scopes():
    reports = _named(
        _report(SCOPED, {"case_01": SAME, "case_02": SAME}),
        _report(UNSCOPED, {"case_01": SAME, "case_02": [v + 0.05 for v in SAME]}),
    )
    out = _compare(reports)
    for case in CASES:
        for metric in ("coverage", "omissions_addressed", "grounding"):
            assert out["cases"][case][metric]["label"] == "within noise"
    assert out["verdict"]["decision"] == "keep scopes"
    assert out["verdict"]["dev_only"] is False
    text = compare_versions.render(out)
    assert RULE in text
    assert "preset-mode scoping only" in text
    assert "keep scopes" in text
    assert "cost_usd" in text and "latency_s" in text and "router mode: shadow" in text


def test_scoped_far_below_unscoped_revises_scopes():
    low = [v - 0.3 for v in SAME]
    reports = _named(
        _report(SCOPED, {"case_01": low, "case_02": low}),
        _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}),
    )
    out = _compare(reports)
    assert out["cases"]["case_01"]["coverage"]["label"] == "scoped lower"
    assert out["verdict"]["decision"] == "revise default scopes"


def test_runs_pool_across_reports():
    reports = _named(
        _report(SCOPED, {"case_01": SAME[:3], "case_02": SAME[:3]}),
        _report(SCOPED, {"case_01": SAME[3:], "case_02": SAME[3:]}),
        _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}),
    )
    out = _compare(reports)
    assert out["cases"]["case_01"]["coverage"]["scoped"]["n"] == 6
    assert out["verdict"]["decision"] == "keep scopes"


def test_fewer_than_six_runs_gives_no_verdict():
    reports = _named(
        _report(SCOPED, {"case_01": SAME[:5], "case_02": SAME}),
        _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}),
    )
    out = _compare(reports)
    assert out["cases"]["case_01"]["coverage"]["label"] == "insufficient runs"
    assert out["cases"]["case_02"]["coverage"]["label"] == "within noise"
    assert out["verdict"]["decision"] is None
    assert "insufficient runs" in out["verdict"]["reason"]


def test_errored_runs_do_not_count_towards_the_minimum():
    scoped = _report(SCOPED, {"case_01": SAME, "case_02": SAME})
    scoped["scores"][0] = _score("case_01", None, outcome="errored")
    out = _compare(_named(scoped, _report(UNSCOPED, {"case_01": SAME, "case_02": SAME})))
    assert out["cases"]["case_01"]["coverage"]["label"] == "insufficient runs"


def test_overlapping_spreads_are_within_noise_and_a_clear_gap_is_not():
    wide = [0.4, 0.9, 0.5, 0.8, 0.6, 0.7]
    reports = _named(
        _report(SCOPED, {"case_01": wide, "case_02": [0.9] * 6}),
        _report(UNSCOPED, {"case_01": [v + 0.05 for v in wide], "case_02": [0.5] * 6}),
    )
    out = _compare(reports)
    assert out["cases"]["case_01"]["coverage"]["label"] == "within noise"
    assert out["cases"]["case_02"]["coverage"]["label"] == "scoped higher"


def test_unknown_version_id_lists_the_versions_found():
    reports = _named(_report(SCOPED, {"case_01": SAME}), _report("sv_other", {"case_01": SAME}))
    with pytest.raises(CompareError) as exc:
        _compare(reports)
    message = str(exc.value)
    assert UNSCOPED in message and "sv_other" in message and SCOPED in message


def test_active_router_is_refused_naming_the_report():
    reports = _named(
        _report(SCOPED, {"case_01": SAME}, router="active/jev"),
        _report(UNSCOPED, {"case_01": SAME}),
    )
    with pytest.raises(CompareError, match="active") as exc:
        _compare(reports)
    assert reports[0][0] in str(exc.value)


def test_mixed_router_modes_across_arms_are_refused():
    reports = _named(
        _report(SCOPED, {"case_01": SAME}, router="shadow/stub"),
        _report(UNSCOPED, {"case_01": SAME}, router="off/stub"),
    )
    with pytest.raises(CompareError, match="router mode") as exc:
        _compare(reports)
    assert reports[0][0] in str(exc.value) and reports[1][0] in str(exc.value)


def test_mixed_backends_are_refused():
    reports = _named(
        _report(SCOPED, {"case_01": SAME}, backends=("api",)),
        _report(UNSCOPED, {"case_01": SAME}, backends=("claude_code",)),
    )
    with pytest.raises(CompareError, match="backend"):
        _compare(reports)


def test_non_api_backend_withholds_the_verdict_unless_dev():
    reports = _named(
        _report(SCOPED, {"case_01": SAME, "case_02": SAME}, backends=("claude_code",)),
        _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}, backends=("claude_code",)),
    )
    out = _compare(reports)
    assert out["verdict"]["decision"] is None
    assert "api" in out["verdict"]["reason"]
    dev = _compare(reports, dev=True)
    assert dev["verdict"]["decision"] == "keep scopes"
    assert dev["verdict"]["dev_only"] is True
    assert "dev-only" in compare_versions.render(dev)


def test_aborted_reports_and_non_ai_act_cases_are_excluded():
    aborted = _report(SCOPED, {"case_01": [0.0] * 6}, aborted="rate_limit")
    other = _report(SCOPED, {"case_01": SAME, "case_02": SAME})
    other["scores"].append({**_score("case_01", 0.0), "case_id": "case_99",
                            "scenario_id": "gdpr_case"})  # fmt: skip
    reports = _named(aborted, other, _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}))
    out = _compare(reports)
    assert out["cases"]["case_01"]["coverage"]["scoped"]["n"] == 6
    assert "case_99" not in out["cases"]
    assert out["excluded"]["aborted"] == [reports[0][0]]
    assert out["excluded"]["non_ai_act_cases"] == ["case_99"]


# ---------- explore: Planner key recall ----------


def _explore_run(sv, keys, *, status="succeeded", backend="api"):
    agents = ("legal", "fiscal")
    return {
        "run_id": "run_x", "scenario_id": "eval_whole_proposal", "status": status,
        "system_version": sv, "usage": [{"role": "planner", "backend": backend, "model": "m"}],
        "retrievals": [
            {"agent": a, "key": k, "status": "granted_text" if a == "legal" else "out_of_scope",
             "source_ids": [], "at": "2026-10-04T00:00:00Z"}
            for a in agents for k in keys
        ],
    }  # fmt: skip


def test_planner_key_recall_against_golden_keys():
    golden = {"ai_act/art/26", "ai_act/annex/III", "ai_act/art/99", "ai_act/art/10"}
    runs = [
        ("run_a.json", _explore_run(SCOPED, ["ai_act/art/26", "ai_act/annex/III", "ai_act/x"])),
        ("run_b.json", _explore_run(SCOPED, ["ai_act/art/26", "ai_act/art/99", "ai_act/art/10"])),
        ("run_c.json", _explore_run(UNSCOPED, ["ai_act/art/1"])),
        ("run_d.json", _explore_run(UNSCOPED, [], status="failed")),
        ("run_e.json", {**_explore_run(SCOPED, ["ai_act/art/26"]), "scenario_id": "omnibus_2026"}),
    ]
    out = compare_versions.explore_recall(runs, SCOPED, UNSCOPED, golden)
    assert out[SCOPED]["n"] == 2
    assert out[SCOPED]["recall"]["mean"] == pytest.approx((0.5 + 0.75) / 2)
    assert out[SCOPED]["granted"] == {"legal": 3.0, "fiscal": 0.0}
    assert out[SCOPED]["refused"] == {"legal": 0.0, "fiscal": 3.0}
    assert out[UNSCOPED]["n"] == 1 and out[UNSCOPED]["failed"] == 1
    assert out[UNSCOPED]["recall"]["mean"] == 0.0
    assert out[SCOPED]["backends"] == ["api"]


def test_explore_section_says_when_there_are_no_runs():
    reports = _named(
        _report(SCOPED, {"case_01": SAME, "case_02": SAME}),
        _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}),
    )
    out = _compare(reports)
    out["explore"] = compare_versions.explore_recall([], SCOPED, UNSCOPED, {"ai_act/art/26"})
    text = compare_versions.render(out)
    assert "no eval_whole_proposal runs" in text


# ---------- the command ----------


def test_main_reads_runs_dir_and_exits(tmp_path, capsys):
    for name, report in _named(
        _report(SCOPED, {"case_01": SAME, "case_02": SAME}),
        _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}),
    ):
        (tmp_path / name).write_text(json.dumps(report))
    (tmp_path / "run_a.json").write_text(json.dumps(_explore_run(SCOPED, ["ai_act/art/26"])))
    (tmp_path / "selfcheck_1.json").write_text("{}")
    code = compare_versions.main(
        ["--scoped", SCOPED, "--unscoped", UNSCOPED, "--runs-dir", str(tmp_path)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "keep scopes" in out and RULE in out
    assert "Planner key recall" in out


def test_main_refusal_exits_2(tmp_path, capsys):
    (tmp_path / "eval_1.json").write_text(json.dumps(_report(SCOPED, {"case_01": SAME})))
    code = compare_versions.main(
        ["--scoped", SCOPED, "--unscoped", UNSCOPED, "--runs-dir", str(tmp_path)]
    )
    assert code == 2
    assert "versions found" in capsys.readouterr().err


# ---------- review fixes: router mode, --min-runs, one-arm cases, no_data_in_scope ----------


@pytest.mark.parametrize("router", [None, "", "warp/stub", 7])
def test_missing_or_unknown_router_mode_is_refused(router):
    """Refused even when both arms agree on it: an unknown mode may be one that skips experts."""
    arms = [_report(sv, {"case_01": SAME}) for sv in (SCOPED, UNSCOPED)]
    for r in arms:
        r["metadata"]["router"] = router
        if router is None:
            del r["metadata"]["router"]
    reports = _named(*arms)
    with pytest.raises(CompareError, match="missing or unknown router mode") as exc:
        _compare(reports)
    assert reports[0][0] in str(exc.value) and reports[1][0] in str(exc.value)


def test_off_router_is_accepted():
    reports = _named(
        _report(SCOPED, {"case_01": SAME, "case_02": SAME}, router="off/stub"),
        _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}, router="off/stub"),
    )
    assert _compare(reports)["router_mode"] == "off"


@pytest.mark.parametrize("value", ["0", "-3"])
def test_min_runs_below_one_is_an_argparse_error(tmp_path, capsys, value):
    with pytest.raises(SystemExit) as exc:
        compare_versions.main(
            ["--scoped", SCOPED, "--unscoped", UNSCOPED, "--runs-dir", str(tmp_path),
             "--min-runs", value]
        )  # fmt: skip
    assert exc.value.code == 2
    assert "--min-runs" in capsys.readouterr().err


def test_case_scored_in_only_one_arm_withholds_the_verdict():
    reports = _named(
        _report(SCOPED, {"case_01": SAME, "case_02": SAME}),
        _report(UNSCOPED, {"case_01": SAME}),
    )
    out = _compare(reports)
    case = out["cases"]["case_02"]["coverage"]
    assert case["label"] == "insufficient runs" and case["unscoped"]["n"] == 0
    assert out["verdict"]["decision"] is None and "case_02" in out["verdict"]["reason"]
    assert "unscoped n=0" in compare_versions.render(out)


def test_no_data_in_scope_skips_are_counted_per_arm():
    scoped = _report(SCOPED, {"case_01": SAME, "case_02": SAME})
    for s in scoped["scores"][:4]:
        s["expert_failures"] = {"fiscal": "no_data_in_scope"}
    scoped["scores"][0]["expert_failures"]["stakeholder"] = "no_data_in_scope"
    scoped["scores"][5]["expert_failures"] = {"legal": "timeout"}
    reports = _named(scoped, _report(UNSCOPED, {"case_01": SAME, "case_02": SAME}))
    out = _compare(reports)
    assert out["no_data_in_scope"] == {
        "scoped": {"runs": 4, "skips": 5, "by_agent": {"fiscal": 4, "stakeholder": 1}},
        "unscoped": {"runs": 0, "skips": 0, "by_agent": {}},
    }
    text = compare_versions.render(out)
    assert "no_data_in_scope" in text and "not underperformance" in text
    assert "fiscal 4" in text
