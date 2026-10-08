"""R34 run-to-run noise and the minimum detectable delta (MDD) of the holdout comparison
(golden-case plan; self-evolution plan U7).

**Noise.** Per metric, the within-case standard deviation over repetitions, pooled over cases:
``sd = sqrt(sum_i (r_i - 1) s_i^2 / sum_i (r_i - 1))`` over the scored runs of each case (the
same estimator as ``womm.eval.holdout``'s pooled noise). Errored runs and null metrics are left
out.

**MDD.** The holdout comparison (``holdout.compare``) runs each of ``n`` sealed cases ``r`` times
per version (the policy's ``repetitions``), takes per-case means, and bootstraps the mean paired
difference by resampling whole proposals (``k`` clusters). From run-to-run noise alone, a
paired case difference has variance ``2 sd^2 / r``, so

    deff = 1 + (n / k - 1) * icc                      (design effect of clustering by proposal)
    se   = sqrt(deff * 2 * sd^2 / (r * n))
    mdd  = (t[1 - alpha/2, k - 1] + t[power, k - 1]) * se

with two-sided ``alpha`` 0.05 (the gate's CI95) and power 0.8. Student-t quantiles with
``k - 1`` degrees of freedom stand in for the normal ones because the bootstrap resamples only
``k`` proposals. ``icc`` is the intra-proposal correlation of case differences; the default 1.0
is the conservative bound (the proposals are the independent units), lower it only with
evidence. The MDD covers run-to-run noise only, not how a real effect varies between cases, so
it is a lower bound: an MDD at most ``expected_gain`` is necessary, not sufficient, for the
statistical mode to resolve that gain.

The holdout case and proposal counts are passed in by a person: this report never reads the
sealed store, and the noise run itself is train/val only.
"""

from __future__ import annotations

import math
import statistics
from typing import Any

from womm.eval.holdout import MIN_PROPOSALS
from womm.eval.run_eval import FORMAL_MIN_REPETITIONS, FORMAL_RUN_KIND, report_judge_versions

METRICS = ("coverage", "omissions_addressed", "grounding")


def pooled_noise(scores: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    """Pooled within-case SD of ``metric`` over a report's scored runs."""
    by_case: dict[str, list[float]] = {}
    for s in scores:
        v = s.get(metric)
        if s.get("outcome") == "scored" and v is not None:
            by_case.setdefault(s["case_id"], []).append(float(v))
    multi = [v for v in by_case.values() if len(v) > 1]
    dof = sum(len(v) - 1 for v in multi)
    sd = None
    if dof:
        sd = math.sqrt(sum(statistics.variance(v) * (len(v) - 1) for v in multi) / dof)
    return {"sd": sd, "dof": dof, "cases": len(multi), "runs": sum(len(v) for v in multi)}


def t_cdf(x: float, df: int) -> float:
    """Student-t CDF for integer ``df`` (Abramowitz and Stegun 26.7.3 and 26.7.4, exact)."""
    if df < 1:
        raise ValueError("df must be at least 1")
    theta = math.atan(abs(x) / math.sqrt(df))
    c2, s = math.cos(theta) ** 2, math.sin(theta)
    if df % 2:  # odd
        term, total = math.cos(theta), 0.0
        if df > 1:
            total = term
            for j in range(3, df - 1, 2):
                term *= c2 * (j - 1) / j
                total += term
        a = 2 / math.pi * (theta + s * total)
    else:
        term = total = 1.0
        for j in range(2, df - 1, 2):
            term *= c2 * (j - 1) / j
            total += term
        a = s * total
    half = a / 2
    return 0.5 + half if x >= 0 else 0.5 - half


def t_quantile(p: float, df: int) -> float:
    """Inverse of ``t_cdf`` by bisection (|error| < 1e-10)."""
    if not 0 < p < 1:
        raise ValueError("p must be in (0, 1)")
    if p < 0.5:
        return -t_quantile(1 - p, df)
    lo, hi = 0.0, 1.0
    while t_cdf(hi, df) < p:
        hi *= 2
    while hi - lo > 1e-10:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if t_cdf(mid, df) < p else (lo, mid)
    return (lo + hi) / 2


def mdd(
    sd: float, *, repetitions: int, n_cases: int, n_proposals: int, icc: float = 1.0,
    alpha: float = 0.05, power: float = 0.8,
) -> dict[str, float]:  # fmt: skip
    """The minimum detectable delta of the holdout comparison (formula in the docstring)."""
    if repetitions < 1 or n_proposals < 2 or n_cases < n_proposals:
        raise ValueError("need repetitions >= 1, at least 2 proposals and n_cases >= proposals")
    if not 0 <= icc <= 1 or not 0 < alpha < 1 or not 0 < power < 1:
        raise ValueError("icc in [0, 1], alpha and power in (0, 1)")
    df = n_proposals - 1
    deff = 1 + (n_cases / n_proposals - 1) * icc
    se = math.sqrt(deff * 2 * sd**2 / (repetitions * n_cases))
    t_alpha, t_power = t_quantile(1 - alpha / 2, df), t_quantile(power, df)
    return {"mdd": (t_alpha + t_power) * se, "se": se, "deff": deff, "df": df,
            "t_alpha": t_alpha, "t_power": t_power}  # fmt: skip


def formal_problems(report: dict[str, Any]) -> list[str]:
    """Why a report is not a recordable R34 formal noise run (empty when it is)."""
    meta = report["metadata"]
    problems = []
    if not meta.get("formal") or meta.get("run_kind") != FORMAL_RUN_KIND:
        problems.append("not a formal run (`womm eval --split val --repetitions 6 --formal`)")
    if meta.get("backends") != ["api"]:
        problems.append(f"backends {meta.get('backends')}: a formal run is api only")
    if (meta.get("repetitions") or 1) < FORMAL_MIN_REPETITIONS:
        problems.append(f"{meta.get('repetitions')} repetitions, fewer than "
                        f"{FORMAL_MIN_REPETITIONS}")  # fmt: skip
    if meta.get("split") not in ("train", "val"):
        problems.append(f"split {meta.get('split')!r}: one train or val split")
    if report.get("aborted"):
        problems.append(f"aborted: {report['aborted']}")
    if meta.get("git_dirty"):
        problems.append("run from a dirty working tree")
    if len(report_judge_versions(report)) != 1:
        problems.append("the report does not record exactly one judge version")
    return problems


def noise_report(
    report: dict[str, Any], *, repetitions: int, n_cases: int, n_proposals: int,
    icc: float = 1.0, alpha: float = 0.05, power: float = 0.8,
) -> dict[str, Any]:  # fmt: skip
    """Per-metric noise and MDD for the holdout design; aggregates only."""
    meta = report["metadata"]
    jvs = sorted(report_judge_versions(report))
    metrics = {}
    for metric in METRICS:
        noise = pooled_noise(report["scores"], metric)
        entry: dict[str, Any] = {"noise_sd": noise["sd"], "dof": noise["dof"],
                                 "cases": noise["cases"], "runs": noise["runs"]}  # fmt: skip
        if noise["sd"] is not None:
            entry |= mdd(noise["sd"], repetitions=repetitions, n_cases=n_cases,
                         n_proposals=n_proposals, icc=icc, alpha=alpha, power=power)  # fmt: skip
        metrics[metric] = entry
    notes = []
    if n_proposals < MIN_PROPOSALS:
        notes.append(f"{n_proposals} holdout proposals, fewer than {MIN_PROPOSALS}: the gate's "
                     "CIs are null and a statistical comparison is decided as weak")  # fmt: skip
    return {
        "noise_run": {
            "system_version": report["system_version"],
            "judge_version": jvs[0] if len(jvs) == 1 else None,
            "split": meta.get("split"),
            "repetitions": meta.get("repetitions"),
            "experiment": meta.get("langsmith_experiment"),
            "backends": meta.get("backends"),
        },
        "design": {
            "repetitions": repetitions,
            "holdout_cases": n_cases,
            "holdout_proposals": n_proposals,
            "icc": icc,
            "alpha": alpha,
            "power": power,
        },
        "metrics": metrics,
        "formal_problems": formal_problems(report),
        "notes": notes,
    }


def records_for(result: dict[str, Any], recorded_on: str) -> tuple[dict, list[dict]]:
    """The ``formal_noise_runs`` entry and the ``mdd_reports`` entries of a formal result."""
    run, design = result["noise_run"], result["design"]
    noise_run = {"system_version": run["system_version"], "judge_version": run["judge_version"],
                 "split": run["split"], "repetitions": run["repetitions"],
                 "experiment": run["experiment"], "recorded_on": recorded_on}  # fmt: skip
    mdds = [
        {"metric": metric, "judge_version": run["judge_version"], "mdd": round(m["mdd"], 4),
         "recorded_on": recorded_on, "noise_sd": round(m["noise_sd"], 4),
         "repetitions": design["repetitions"], "holdout_cases": design["holdout_cases"],
         "holdout_proposals": design["holdout_proposals"], "icc": design["icc"],
         "alpha": design["alpha"], "power": design["power"]}
        for metric, m in result["metrics"].items()
        if m.get("mdd") is not None and round(m["mdd"], 4) > 0
    ]  # fmt: skip
    return noise_run, mdds


def format_report(result: dict[str, Any], expected_gain: float | None = None) -> str:
    run, d = result["noise_run"], result["design"]
    lines = [
        f"noise run: {run['system_version']} judge {run['judge_version']} split {run['split']}, "
        f"{run['repetitions']} repetitions, backends {run['backends']}",
        f"holdout design: {d['holdout_cases']} cases over {d['holdout_proposals']} proposals, "
        f"{d['repetitions']} repetitions per arm, icc {d['icc']}, alpha {d['alpha']}, power "
        f"{d['power']}",
    ]
    for metric, m in result["metrics"].items():
        if m["noise_sd"] is None:
            lines.append(f"  {metric}: no case with two scored runs; noise unknown")
            continue
        flag = ""
        if expected_gain is not None and metric == "coverage":
            flag = " (<= expected_gain)" if m["mdd"] <= expected_gain else " (> expected_gain)"
        lines.append(f"  {metric}: noise SD {m['noise_sd']:.4f} over {m['cases']} case(s); "
                     f"MDD {m['mdd']:.4f}{flag} (se {m['se']:.4f}, deff {m['deff']:.2f}, "
                     f"t df {m['df']})")  # fmt: skip
    lines += [f"note: {n}" for n in result["notes"]]
    if result["formal_problems"]:
        lines.append("not recordable as the R34 formal noise run: "
                     + "; ".join(result["formal_problems"]))  # fmt: skip
    return "\n".join(lines)
