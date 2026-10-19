"""Compare a scoped and an unscoped SystemVersion over pooled eval reports (plan U6).

    uv run python scripts/compare_versions.py --scoped <version_id> --unscoped <version_id>
        [--runs-dir runs] [--min-runs 6] [--dev] [--json]

Reads the eval reports (``eval_*.json``, written by ``womm eval``) under ``--runs-dir`` for
exactly two version ids, pools the scored runs per golden case, and prints per case and metric
the mean ± spread (sample standard deviation) of each arm, plus cost, latency and the router
mode. AI Act golden cases only: scores whose scenario is not in the AI Act fixture are excluded.

Refusals (exit 2): an unknown version id; a report with the router in ``active`` mode (an
active router skips experts, so scopes would not be the only difference); a report whose router
mode is missing or unknown (only ``shadow`` and ``off`` are accepted); router modes that differ
across arms; backends that differ across reports. ``--min-runs`` below 1 is a usage error.

Scoped experts with nothing within their scope are not called (``no_data_in_scope``). The report
counts such skips per arm so they are not read as underperformance.

The verdict applies the pre-registered rule below to the case-balanced mean coverage (the mean
of the per-case means). One noise band is the larger of the two arms' pooled run-to-run spread
of coverage, where an arm's pooled spread is the square root of the mean of its per-case sample
variances. A verdict needs at least ``--min-runs`` scored runs for every case in both arms, and
the api backend; ``--dev`` allows another backend, and the verdict is then labelled dev-only.

Golden-case results measure preset-mode scoping only (the golden cases are preset scenarios).
When saved run results of the ``eval_whole_proposal`` explore scenario exist for either version
(``womm run eval_whole_proposal``), the report adds the Planner's key recall against the golden
cases' provision keys, and per-expert granted and refused counts. These are unscored and never
used for the verdict.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

METRICS = ("coverage", "omissions_addressed", "grounding")
RUN_METRICS = ("cost_usd", "latency_s", "tokens")
MIN_RUNS = 6
EXPLORE_SCENARIO = "eval_whole_proposal"
RULE = (
    "if scoped coverage is more than one noise band below unscoped → revise default scopes "
    "before v1.0-scoped is used further; otherwise keep scopes"
)
PRESET_ONLY = (
    "Golden-case results measure preset-mode scoping only: the golden cases are preset "
    "scenarios, so explore-mode scoping is not scored here."
)
REFUSED_STATUSES = {"out_of_scope", "unknown_key"}
ACCEPTED_ROUTER_MODES = {"shadow", "off"}  # modes where every expert runs
NO_DATA = "no_data_in_scope"

Named = tuple[str, dict[str, Any]]


class CompareError(ValueError):
    """A comparison that must not be made; the message says why and names the reports."""


# ---------- reading ----------


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"warning: skipping unreadable {path.name}", file=sys.stderr)
        return None
    return data if isinstance(data, dict) else None


def load_reports(runs_dir: Path) -> list[Named]:
    """Every eval report under ``runs_dir``, as (file name, report)."""
    out = []
    for path in sorted(runs_dir.glob("eval_*.json")):
        data = _read(path)
        if data is not None and "system_version" in data and "scores" in data:
            out.append((path.name, data))
    return out


def load_run_results(runs_dir: Path) -> list[Named]:
    """Every saved run result under ``runs_dir`` (``womm run`` and ``womm eval`` save them)."""
    out = []
    for path in sorted(runs_dir.glob("*.json")):
        if path.name.startswith(("eval_", "selfcheck_")):
            continue
        data = _read(path)
        if data is not None and "run_id" in data and "scenario_id" in data:
            out.append((path.name, data))
    return out


# ---------- statistics ----------


def _stats(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "mean": statistics.fmean(values) if values else None,
        "sd": statistics.stdev(values) if len(values) > 1 else None,
    }


def _label(scoped: dict, unscoped: dict, min_runs: int) -> tuple[str, float | None, float | None]:
    """The per-case contrast: within noise when the difference is no larger than the larger
    of the two spreads."""
    if scoped["n"] < min_runs or unscoped["n"] < min_runs:
        return "insufficient runs", None, None
    diff = scoped["mean"] - unscoped["mean"]
    band = max(scoped["sd"] or 0.0, unscoped["sd"] or 0.0)
    if abs(diff) <= band:
        return "within noise", diff, band
    return ("scoped higher" if diff > 0 else "scoped lower"), diff, band


def _router_mode(report: dict) -> str:
    router = (report.get("metadata") or {}).get("router")
    return router.split("/", 1)[0] if isinstance(router, str) and router else "unknown"


def _backends(report: dict) -> tuple[str, ...]:
    return tuple(sorted((report.get("metadata") or {}).get("backends") or ()))


# ---------- comparison ----------


def _check_arms(reports: list[Named], scoped: str, unscoped: str) -> dict[str, list[Named]]:
    arms = {
        sv: [(n, r) for n, r in reports if r["system_version"] == sv] for sv in (scoped, unscoped)
    }
    missing = [sv for sv, rs in arms.items() if not rs]
    if missing:
        found: dict[str, list[str]] = {}
        for name, r in reports:
            found.setdefault(r["system_version"], []).append(name)
        listing = (
            "\n".join(
                f"  {sv}: {len(names)} report(s), e.g. {names[0]}"
                for sv, names in sorted(found.items())
            )
            or "  (none)"
        )
        raise CompareError(f"no eval report for {', '.join(missing)}; versions found:\n{listing}")
    active = [n for rs in arms.values() for n, r in rs if _router_mode(r) == "active"]
    if active:
        raise CompareError(
            "refusing reports run with the router in active mode (it skips experts, so scopes "
            f"would not be the only difference): {', '.join(active)}"
        )
    unknown = [
        f"{n} ({_router_mode(r)})"
        for rs in arms.values()
        for n, r in rs
        if _router_mode(r) not in ACCEPTED_ROUTER_MODES
    ]
    if unknown:
        raise CompareError(
            "refusing reports with a missing or unknown router mode (only shadow and off run "
            f"every expert): {', '.join(unknown)}"
        )
    modes = {sv: {_router_mode(r) for _, r in rs} for sv, rs in arms.items()}
    if len(modes[scoped] | modes[unscoped]) > 1:
        named = ", ".join(f"{n} ({_router_mode(r)})" for rs in arms.values() for n, r in rs)
        raise CompareError(f"refusing mixed router modes across arms: {named}")
    backends = {_backends(r) for rs in arms.values() for _, r in rs}
    if len(backends) > 1:
        named = ", ".join(
            f"{n} ({'+'.join(_backends(r)) or 'unknown'})" for rs in arms.values() for n, r in rs
        )
        raise CompareError(f"refusing to mix backends across reports: {named}")
    return arms


def compare(
    reports: list[Named],
    scoped: str,
    unscoped: str,
    *,
    ai_act_scenarios: set[str],
    dev: bool = False,
    min_runs: int = MIN_RUNS,
) -> dict[str, Any]:
    """The comparison as data. Raises ``CompareError`` for a comparison that must not be made."""
    arms = _check_arms(reports, scoped, unscoped)
    aborted = [n for rs in arms.values() for n, r in rs if r.get("aborted")]
    non_ai_act: set[str] = set()
    scores: dict[str, list[dict]] = {}
    included: dict[str, list[dict]] = {}  # every AI Act score of a kept report, scored or not
    for sv, rs in arms.items():
        scores[sv] = []
        included[sv] = []
        for name, r in rs:
            if name in aborted:
                continue
            for s in r["scores"]:
                if s.get("scenario_id") not in ai_act_scenarios:
                    non_ai_act.add(s.get("case_id", "?"))
                    continue
                included[sv].append(s)
                if s.get("outcome") == "scored":
                    scores[sv].append(s)

    case_ids = sorted({s["case_id"] for sv in scores for s in scores[sv]})
    cases: dict[str, dict] = {}
    for case in case_ids:
        cases[case] = {}
        for metric in METRICS:
            per_arm = {
                key: _stats(
                    [
                        s[metric]
                        for s in scores[sv]
                        if s["case_id"] == case and s.get(metric) is not None
                    ]
                )
                for key, sv in (("scoped", scoped), ("unscoped", unscoped))
            }
            label, diff, band = _label(per_arm["scoped"], per_arm["unscoped"], min_runs)
            cases[case][metric] = {**per_arm, "label": label, "diff": diff, "band": band}

    runs = {
        key: {m: _stats([s[m] for s in scores[sv] if s.get(m) is not None]) for m in RUN_METRICS}
        for key, sv in (("scoped", scoped), ("unscoped", unscoped))
    }
    backends = _backends(arms[scoped][0][1])
    no_data = {
        key: _no_data(included[sv]) for key, sv in (("scoped", scoped), ("unscoped", unscoped))
    }
    return {
        "scoped": scoped,
        "unscoped": unscoped,
        "router_mode": _router_mode(arms[scoped][0][1]),
        "backends": list(backends),
        "reports": {
            key: [n for n, _ in arms[sv]]
            for key, sv in (("scoped", scoped), ("unscoped", unscoped))
        },
        "excluded": {"aborted": aborted, "non_ai_act_cases": sorted(non_ai_act)},
        "min_runs": min_runs,
        "cases": cases,
        "runs": runs,
        "no_data_in_scope": no_data,
        "rule": RULE,
        "verdict": _verdict(cases, backends, dev, min_runs),
        "explore": None,
    }


def _no_data(scores: list[dict]) -> dict[str, Any]:
    """Runs with at least one scoped expert skipped for no data in its scope, the skips, and
    the skips per expert."""
    by_agent: dict[str, int] = {}
    runs = 0
    for s in scores:
        agents = [a for a, kind in (s.get("expert_failures") or {}).items() if kind == NO_DATA]
        runs += bool(agents)
        for a in agents:
            by_agent[a] = by_agent.get(a, 0) + 1
    return {
        "runs": runs,
        "skips": sum(by_agent.values()),
        "by_agent": dict(sorted(by_agent.items())),
    }


def _verdict(cases: dict, backends: tuple[str, ...], dev: bool, min_runs: int) -> dict[str, Any]:
    out: dict[str, Any] = {
        "decision": None,
        "reason": None,
        "dev_only": False,
        "scoped_mean": None,
        "unscoped_mean": None,
        "band": None,
    }
    if not cases:
        out["reason"] = "no scored AI Act golden-case runs"
        return out
    short = [c for c, m in cases.items() if m["coverage"]["label"] == "insufficient runs"]
    if short:
        out["reason"] = (
            f"insufficient runs: {', '.join(short)} (at least {min_runs} scored runs per case "
            "in both arms)"
        )
        return out
    api = backends == ("api",)
    if not api and not dev:
        out["reason"] = (
            f"backend {'+'.join(backends) or 'unknown'}: a verdict needs the api backend "
            "(rerun on api, or pass --dev for a dev-only verdict)"
        )
        return out
    cov = [m["coverage"] for m in cases.values()]
    scoped_mean = statistics.fmean(c["scoped"]["mean"] for c in cov)
    unscoped_mean = statistics.fmean(c["unscoped"]["mean"] for c in cov)

    def pooled(arm: str) -> float:
        return math.sqrt(statistics.fmean((c[arm]["sd"] or 0.0) ** 2 for c in cov))

    band = max(pooled("scoped"), pooled("unscoped"))
    revise = scoped_mean - unscoped_mean < -band
    out.update(
        decision="revise default scopes" if revise else "keep scopes",
        reason=(
            f"scoped {scoped_mean:.3f} vs unscoped {unscoped_mean:.3f} "
            f"(difference {scoped_mean - unscoped_mean:+.3f}, one noise band {band:.3f})"
        ),
        dev_only=not api,
        scoped_mean=scoped_mean,
        unscoped_mean=unscoped_mean,
        band=band,
    )
    return out


# ---------- explore: Planner key recall ----------


def golden_provision_keys(cases: Iterable[Any]) -> set[str]:
    """The provision keys of every expected impact and important omission."""
    return {
        k
        for c in cases
        for item in (*c.expected_impacts, *c.important_omissions)
        for k in item.provision_keys
    }


def explore_recall(
    runs: list[Named], scoped: str, unscoped: str, golden_keys: set[str]
) -> dict[str, dict]:
    """Per version: the Planner's key recall on ``eval_whole_proposal`` runs against the golden
    keys, and the mean granted and refused counts per expert. Every expert requests the union
    of the Planner's focus keys, so the distinct keys of the retrieval log are the Planner's
    selection. Failed runs are counted and left out."""
    out: dict[str, dict] = {}
    for sv in (scoped, unscoped):
        mine = [
            r
            for _, r in runs
            if r.get("scenario_id") == EXPLORE_SCENARIO and r.get("system_version") == sv
        ]
        ok = [r for r in mine if r.get("status") != "failed"]
        recalls: list[float] = []
        granted: dict[str, list[int]] = {}
        refused: dict[str, list[int]] = {}
        for r in ok:
            records = r.get("retrievals") or []
            keys = {rec["key"] for rec in records}
            recalls.append(len(keys & golden_keys) / len(golden_keys) if golden_keys else 0.0)
            for agent in dict.fromkeys(rec["agent"] for rec in records):
                statuses = [rec["status"] for rec in records if rec["agent"] == agent]
                n_refused = sum(s in REFUSED_STATUSES for s in statuses)
                granted.setdefault(agent, []).append(len(statuses) - n_refused)
                refused.setdefault(agent, []).append(n_refused)
        out[sv] = {
            "n": len(ok),
            "failed": len(mine) - len(ok),
            "recall": _stats(recalls),
            "golden_keys": len(golden_keys),
            "granted": {a: statistics.fmean(v) for a, v in granted.items()},
            "refused": {a: statistics.fmean(v) for a, v in refused.items()},
            "backends": sorted({u["backend"] for r in ok for u in r.get("usage") or []}),
        }
    return out


# ---------- rendering ----------


def _fmt(stats: dict, digits: int = 2) -> str:
    if not stats["n"]:
        return "n=0"
    sd = f" ± {stats['sd']:.{digits}f}" if stats["sd"] is not None else ""
    return f"{stats['mean']:.{digits}f}{sd} (n={stats['n']})"


def render(out: dict[str, Any]) -> str:
    v = out["verdict"]
    lines = [
        f"Scoped vs unscoped: {out['scoped']} vs {out['unscoped']}",
        f"router mode: {out['router_mode']}   backends: {'+'.join(out['backends']) or 'unknown'}",
        f"reports: scoped {len(out['reports']['scoped'])}, unscoped "
        f"{len(out['reports']['unscoped'])}",
        "AI Act golden cases only. " + PRESET_ONLY,
    ]
    ex = out["excluded"]
    if ex["aborted"]:
        lines.append(f"excluded aborted reports: {', '.join(ex['aborted'])}")
    if ex["non_ai_act_cases"]:
        lines.append(f"excluded non-AI Act cases: {', '.join(ex['non_ai_act_cases'])}")
    lines += ["", "Per case (mean ± spread over pooled scored runs):"]
    for case, metrics in out["cases"].items():
        for metric, m in metrics.items():
            diff = f"  diff {m['diff']:+.2f}, band {m['band']:.2f}" if m["diff"] is not None else ""
            lines.append(
                f"  {case} {metric:<20} scoped {_fmt(m['scoped'])}  unscoped "
                f"{_fmt(m['unscoped'])}  → {m['label']}{diff}"
            )
    lines += ["", "Per run (scored runs, all cases):"]
    for metric in RUN_METRICS:
        lines.append(
            f"  {metric:<10} scoped {_fmt(out['runs']['scoped'][metric], 3)}  unscoped "
            f"{_fmt(out['runs']['unscoped'][metric], 3)}"
        )
    lines += [
        "",
        "no_data_in_scope (a scoped expert not called: nothing within its scope; not "
        "underperformance):",
    ]
    for arm in ("scoped", "unscoped"):
        nd = (out.get("no_data_in_scope") or {}).get(arm) or {"runs": 0, "skips": 0, "by_agent": {}}
        agents = ", ".join(f"{a} {n}" for a, n in nd["by_agent"].items())
        lines.append(
            f"  {arm}: {nd['skips']} skip(s) in {nd['runs']} run(s)"
            + (f" ({agents})" if agents else "")
        )
    lines += ["", f"Pre-registered rule: {out['rule']}."]
    if v["decision"] is None:
        lines.append(f"Verdict: none ({v['reason']}).")
    else:
        label = " [dev-only: not api-backend evidence]" if v["dev_only"] else ""
        lines.append(f"Verdict: {v['decision']}{label}. {v['reason']}.")
    lines += [
        "",
        f"Explore ({EXPLORE_SCENARIO}): Planner key recall against the golden cases' "
        "provision keys (unscored, not used for the verdict):",
    ]
    explore = out.get("explore") or {}
    if not any(e["n"] or e["failed"] for e in explore.values()):
        lines.append(f"  no {EXPLORE_SCENARIO} runs found for either version")
    for sv, e in explore.items():
        if not (e["n"] or e["failed"]):
            lines.append(f"  {sv}: no runs")
            continue
        per_expert = ", ".join(
            f"{a} {e['granted'][a]:.1f}/{e['refused'][a]:.1f}" for a in e["granted"]
        )
        lines.append(
            f"  {sv}: recall {_fmt(e['recall'])} of {e['golden_keys']} golden keys, "
            f"failed runs {e['failed']}, backends {'+'.join(e['backends']) or 'unknown'}"
        )
        if per_expert:
            lines.append(f"    granted/refused per expert (mean): {per_expert}")
    return "\n".join(lines)


# ---------- command ----------


def _positive_int(value: str) -> int:
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}") from None
    if n < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {n}")
    return n


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scoped", required=True, help="version id of the scoped arm")
    parser.add_argument("--unscoped", required=True, help="version id of the unscoped arm")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--min-runs", type=_positive_int, default=MIN_RUNS)
    parser.add_argument("--dev", action="store_true", help="allow a non-api backend (dev-only)")
    parser.add_argument("--json", action="store_true", help="print the comparison as JSON")
    args = parser.parse_args(argv)
    if args.scoped == args.unscoped:
        print("error: --scoped and --unscoped must be two different version ids", file=sys.stderr)
        return 2

    from womm.data.fixtures import load_fixture
    from womm.eval.golden import load_all_golden

    try:
        out = compare(
            load_reports(args.runs_dir),
            args.scoped,
            args.unscoped,
            ai_act_scenarios=set(load_fixture().scenarios),
            dev=args.dev,
            min_runs=args.min_runs,
        )
    except CompareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    golden = golden_provision_keys(load_all_golden())
    out["explore"] = explore_recall(
        load_run_results(args.runs_dir), args.scoped, args.unscoped, golden
    )
    print(json.dumps(out, indent=2, ensure_ascii=False) if args.json else render(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
