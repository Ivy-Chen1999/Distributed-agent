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
import random
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


# ----------------------------------------------------------------------------- data-driven runs

R34_MAX_REPETITIONS = 6
STOP_RULE_LEVEL = 0.8


def _lower_gamma_regularised(a: float, x: float) -> float:
    """P(a, x) by its series (x < a + 1) or continued fraction (Numerical Recipes 6.2)."""
    if x <= 0:
        return 0.0
    ln_front = a * math.log(x) - x - math.lgamma(a)
    if x < a + 1:
        term = total = 1 / a
        n = a
        while abs(term) > abs(total) * 1e-15:
            n += 1
            term *= x / n
            total += term
        return total * math.exp(ln_front)
    tiny = 1e-300
    b, c, d = x + 1 - a, 1 / tiny, 1 / (x + 1 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = tiny if abs(d) < tiny else d
        c = b + an / c
        c = tiny if abs(c) < tiny else c
        d = 1 / d
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return 1 - math.exp(ln_front) * h


def chi2_quantile(p: float, df: int) -> float:
    """Inverse chi-square CDF by bisection on the regularised lower incomplete gamma."""
    if not 0 < p < 1 or df < 1:
        raise ValueError("p in (0, 1) and df >= 1")
    lo, hi = 0.0, float(df)
    while _lower_gamma_regularised(df / 2, hi / 2) < p:
        hi *= 2
    while hi - lo > 1e-10 * max(1.0, hi):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _lower_gamma_regularised(df / 2, mid / 2) < p else (lo, mid)
    return (lo + hi) / 2


def sd_interval(sd: float, dof: int, level: float = STOP_RULE_LEVEL) -> tuple[float, float]:
    """Two-sided ``level`` interval for a pooled SD with ``dof`` degrees of freedom."""
    tail = (1 - level) / 2
    return (sd * math.sqrt(dof / chi2_quantile(1 - tail, dof)),
            sd * math.sqrt(dof / chi2_quantile(tail, dof)))  # fmt: skip


def sd_star(
    expected_gain: float, *, repetitions: int, n_cases: int, n_proposals: int, icc: float = 1.0
) -> float:
    """The coverage noise SD at which the MDD equals ``expected_gain`` (the MDD is linear in
    the SD)."""
    unit = mdd(1.0, repetitions=repetitions, n_cases=n_cases, n_proposals=n_proposals, icc=icc)
    return expected_gain / unit["mdd"]


def r34_next_step(
    sd: float, dof: int, star: float, level: float = STOP_RULE_LEVEL
) -> dict[str, Any]:
    """The pre-registered R34 stop rule: stop when the SD interval lies on one side of
    ``star``; otherwise extend. It reads the incumbent's noise only, never a candidate."""
    lo, hi = sd_interval(sd, dof, level)
    step = "stop" if lo > star or hi < star else "extend"
    return {"next_step": step, "sd": sd, "dof": dof, "sd_star": star, "interval": [lo, hi],
            "level": level}  # fmt: skip


def comparison_extends(
    sd: float | None, *, r_min: int, r_max: int, n_cases: int, n_proposals: int,
    expected_gain: float, icc: float = 1.0,
) -> dict[str, Any]:  # fmt: skip
    """Blinded re-estimation for a holdout comparison: extend from ``r_min`` to ``r_max`` only
    when the MDD at ``r_min`` exceeds ``expected_gain`` and the MDD at ``r_max`` does not. ``sd``
    is the pooled within-case SD over both arms; no candidate-minus-baseline value is read."""
    out: dict[str, Any] = {"extend": False, "pooled_sd": sd, "min": r_min, "max": r_max,
                           "mdd_at_min": None, "mdd_at_max": None}  # fmt: skip
    if sd is None or r_max <= r_min or n_proposals < 2 or n_cases < n_proposals:
        return out
    design = {"n_cases": n_cases, "n_proposals": n_proposals, "icc": icc}
    at_min = mdd(sd, repetitions=r_min, **design)["mdd"]
    at_max = mdd(sd, repetitions=r_max, **design)["mdd"]
    out |= {"mdd_at_min": at_min, "mdd_at_max": at_max,
            "extend": at_min > expected_gain and at_max <= expected_gain}  # fmt: skip
    return out


IDENTITY = ("system_version", "git_sha", "split")


def pool_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """One report from several formal runs of the same version, judge, code and split: scores
    concatenated, repetitions summed, experiment names joined."""
    first = reports[0]
    for other in reports[1:]:
        for field in IDENTITY:
            a = first["metadata"].get(field, first.get(field))
            b = other["metadata"].get(field, other.get(field))
            if a != b:
                raise ValueError(f"cannot pool runs with different {field}: {a} vs {b}")
        if report_judge_versions(other) != report_judge_versions(first):
            raise ValueError("cannot pool runs with different judge versions")
    meta = dict(first["metadata"])
    meta["repetitions"] = sum(r["metadata"].get("repetitions") or 0 for r in reports)
    names = [r["metadata"].get("langsmith_experiment") for r in reports]
    meta["langsmith_experiment"] = ", ".join(n for n in names if n) or None
    meta["git_dirty"] = any(r["metadata"].get("git_dirty") for r in reports)
    return first | {
        "metadata": meta,
        "aborted": next((r["aborted"] for r in reports if r.get("aborted")), None),
        "scores": [s for r in reports for s in r["scores"]],
        "case_runs": [c for r in reports for c in r.get("case_runs", [])],
    }


def _case_means(scores: list[dict[str, Any]], metric: str) -> dict[str, float]:
    by_case: dict[str, list[float]] = {}
    for s in scores:
        if s.get("outcome") == "scored" and s.get(metric) is not None:
            by_case.setdefault(s["case_id"], []).append(float(s[metric]))
    return {k: statistics.fmean(v) for k, v in by_case.items()}


def batch_shift(
    first: dict[str, Any], second: dict[str, Any], metric: str = "coverage"
) -> dict[str, Any]:
    """Mean per-case difference between two runs, against the first run's pooled SD; a shift
    above that SD is flagged (the two halves may not be one sample)."""
    a, b = _case_means(first["scores"], metric), _case_means(second["scores"], metric)
    common = sorted(a.keys() & b.keys())
    shift = abs(statistics.fmean(b[k] - a[k] for k in common)) if common else None
    first_sd = pooled_noise(first["scores"], metric)["sd"]
    flag = shift is not None and first_sd is not None and shift > first_sd
    return {"shift": shift, "first_sd": first_sd, "flag": flag}


def null_pass_rate(
    scores: list[dict[str, Any]], *, bar_se: float, metric: str = "coverage",
    draws: int = 2000, seed: int = 0,
) -> dict[str, Any]:  # fmt: skip
    """A/A: split each case's runs at random into two halves of equal size (a version against
    itself), and count how often the mean over cases of the half differences exceeds
    ``bar_se`` x its SE (the weak bar). Seeded, so the figure is reproducible."""
    by_case: dict[str, list[float]] = {}
    for s in scores:
        if s.get("outcome") == "scored" and s.get(metric) is not None:
            by_case.setdefault(s["case_id"], []).append(float(s[metric]))
    usable = [v for v in by_case.values() if len(v) >= 2]
    if not usable:
        return {"rate": None, "draws": 0, "half": None, "cases": 0}
    half = min(len(v) for v in usable) // 2
    sd = pooled_noise(scores, metric)["sd"] or 0.0
    se = math.sqrt(2 * sd**2 / (half * len(usable)))
    rng = random.Random(seed)
    above = 0
    for _ in range(draws):
        diffs = []
        for v in usable:
            picked = rng.sample(v, 2 * half)
            diffs.append(statistics.fmean(picked[:half]) - statistics.fmean(picked[half:]))
        above += statistics.fmean(diffs) > bar_se * se
    return {"rate": above / draws, "draws": draws, "half": half, "cases": len(usable),
            "bar_se": bar_se, "se": se}  # fmt: skip


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
