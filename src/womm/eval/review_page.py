"""The owner's review page of a scored labelling round (``review.html`` in the sample directory).

``score`` (the reference check) and ``womm calibrate score`` write it: the verdict in plain words
with what to do next, the numbers with what they mean, and every item where the people and the
AI disagree, side by side with the AI's own reason, so the owner can mark who is right. It holds
the tool's or judge's decisions, so it stays next to ``key.private.json``: never upload or send it.
"""

# The verdict texts and number explanations are user-facing sentences kept whole for reading.
# ruff: noqa: E501

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from womm.eval import hosted_labels

TEMPLATE = Path(__file__).with_name("review_page.html")
FILE = "review.html"


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.0%}" if x in (0, 1) else f"{x:.1%}"


def _interval(result: dict) -> str:
    ci = result.get("agreement_raw_ci95")
    if not ci:
        return ""
    return (
        f" With this few pairs the true raw figure could be anywhere from {_pct(ci[0])} to "
        f"{_pct(ci[1])} (95%): context only, the gate uses the measured figure."
    )


def _cards_by_id(directory: Path) -> dict[str, dict]:
    try:
        pages = hosted_labels.read_pages(directory)
    except hosted_labels.HostedLabelsError:
        return {}
    return {c["id"]: c for cards in pages["annotators"].values() for c in cards}


def _write(directory: Path, data: dict[str, Any], file: str = FILE) -> Path:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path = directory / file
    path.write_text(TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", blob), "utf-8")
    return path


def reference_check(
    directory: Path,
    key: dict,
    result: dict,
    labels: dict,
    notes: dict,
    *,
    limit: float,
    file: str = FILE,
    title: str = "Answer key check: review",
) -> Path:
    cards = _cards_by_id(directory)
    items = key["items"]
    n, errs = result["items"], len(result["wrong"]) + len(result["unsure"])
    verdict = {
        "robust": ("pass", "Passed", "The answer key is reliable enough to use",
                   "People and the tool disagree rarely enough that the tool-made answer key can be "
                   "trusted without anyone reviewing each item.",
                   ["Publish the golden cases (<code>scripts/publish_golden_cases.py</code>) and record "
                    "the result in the human-steps file."]),
        "extend": ("wait", "Not decided yet", "Errors in the first round: send the reserve",
                   "The check stops early only with no error at all. With some, it needs the extra "
                   "items to tell a few slips from a real problem.",
                   ["Run <code>scripts/reference_check.py extend --dir &lt;this dir&gt; --url &lt;app&gt;</code> "
                    "and ask both people to open their link again.",
                    "Then <code>fetch</code> and <code>score</code> again."]),
        "not_robust": ("fail", "Failed", "The answer key makes too many mistakes",
                       "Fix how the key is made before anything is scored with it.",
                       ["Fix the drafting or tie-break prompt where the tool was wrong, settle the "
                        "open items again, and run a new check with a new seed."]),
        "incomplete": ("wait", "Not finished", "Some items have no answer yet",
                       "A verdict needs every item answered.",
                       ["Wait until everyone has sent, then <code>fetch</code> and <code>score</code> "
                        "again."]),
    }[result["verdict"]]  # fmt: skip
    state, tag, headline, meaning, nxt = verdict
    bound = result["upper_bound_95"]
    if result.get("borderline"):
        meaning += (f" It is borderline: with this many items the true error rate could still be "
                    f"{_pct(bound)}, so say so wherever the key is used.")  # fmt: skip
    numbers = [
        {"label": "Tool error rate", "value": _pct(result["error"]),
         "good": result["error"] <= limit,
         "explain": f"{errs} of {n} items where the person and the tool differ, \"can't tell\" "
                    f"counted as an error. The limit is {_pct(limit)}."},
        {"label": "Worst case (95%)", "value": _pct(bound), "good": None,
         "explain": "How high the true error rate could still be, given this sample size. Context "
                    "only: the verdict uses the measured rate above."},
        {"label": "Items checked", "value": str(n), "good": None,
         "explain": "Each person had their own items, a third each of: kept by the three judges, "
                    "kept by the tie-break, dropped by the tie-break."},
    ]  # fmt: skip
    if (shared := result.get("overlap")) is not None:
        numbers.append({"label": "People agree with each other",
                        "value": f"{shared['agree']} of {shared['pairs']}", "good": None,
                        "explain": "Items both people labelled. Context only: when people often "
                                   "differ, a disagreement with the tool may be the person's slip."})  # fmt: skip
    groups = {
        "judges_accepted": ("Kept by the three judges", "the drafting judges"),
        "tiebreak_kept": ("Kept by the tie-break", "the tie-break prompt"),
        "tiebreak_dropped": ("Dropped by the tie-break", "the tie-break prompt"),
    }
    for s, rate in result["error_by_stratum"].items():
        if rate:
            name, fix = groups[s]
            numbers.append({"label": f"Errors: {name.lower()}", "value": _pct(rate),
                            "good": False if rate > limit else None,
                            "explain": f"Error rate among the items {name.lower()}. If errors "
                                       f"cluster here, {fix} is what to fix."})  # fmt: skip

    def one(iid: str, section: str) -> dict:
        it, c = items[iid], cards.get(iid, {})
        return {
            "id": iid, "case": it["case_id"], "section": section,
            "tag": groups[it["stratum"]][0],
            "claim": c.get("claim", {}), "source": c.get("source", "report"),
            "section_name": c.get("section", ""), "passage": c.get("passage"),
            "quote": c.get("quote", ""),
            "people": [{"name": it["annotator"], "label": labels.get(iid), "note": notes.get(iid, "")}],
            "ai": {"label": "keep" if it["tool_kept"] else "drop", "reason": it.get("tool_reason", "")},
        }  # fmt: skip

    out = [one(i, "disagree") for i in result["wrong"]]
    out += [one(i, "unsure") for i in result["unsure"]]
    data = {"kind": "reference_check", "sample": key["sample"], "title": title,
            "verdict": {"state": state, "tag": tag, "headline": headline, "meaning": meaning,
                        "next": nxt},
            "numbers": numbers, "cards": out}  # fmt: skip
    return _write(directory, data, file)


def _judge_verdicts(key: dict) -> dict[str, dict]:
    """Pair id -> the judge's verdict entry (justification, matched impact) from the reports."""
    reports: dict[str, dict] = {}
    out = {}
    for pid, p in key["pairs"].items():
        if p["report"] not in reports:
            try:
                reports[p["report"]] = json.loads(Path(p["report"]).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                reports[p["report"]] = {}
        for s in reports[p["report"]].get("scores", []):
            if s.get("run_id") == p["run_id"]:
                for v in (s.get("judge") or {}).get("expected", []):
                    if v.get("expected_id") == p["expected_id"]:
                        out[pid] = v
    return out


def calibration(
    directory: Path, key: dict, result: dict, answers: dict, notes: dict, *, minimum: float
) -> Path:
    cards = _cards_by_id(directory)
    verdicts = _judge_verdicts(key)
    agreement = result["agreement"]
    blank = any(a["blank"] for a in result["annotators"].values())
    problems = []
    if not blank and agreement is not None:
        from womm.eval import judge_calibration as jc
        from womm.evolve import promotion as pm

        problems = pm.calibration_problems(
            pm.JudgeCalibration(**jc.calibration_record(result, "review"))
        )
    if blank:
        state, tag, headline, meaning, nxt = (
            "wait", "Not finished", "Some pairs have no answer yet",
            "A result needs every pair answered by everyone.",
            ["Wait until everyone has sent, then <code>womm calibrate fetch</code> and "
             "<code>score</code> again."])  # fmt: skip
    elif problems:
        state, tag, headline, meaning, nxt = (
            "fail", "Does not count", "The people's answers are not a clear enough reference",
            "Before the grader can be judged, the people must mostly agree with each other: "
            + "; ".join(problems) + ".",
            ["Read the pairs where the two people split (below, \"unsure\") and sharpen the "
             "rule's wording, then calibrate again with new pairs (a new seed)."])  # fmt: skip
    elif agreement is not None and agreement >= minimum:
        state, tag, headline, meaning, nxt = (
            "pass", "Passed", "The grader agrees with people well enough to be trusted",
            "Scores from this grader can be used for promotion decisions.",
            ["Record it: <code>womm calibrate score --dir &lt;this dir&gt; --record</code>, then "
             "commit <code>evals/promotion_records.yaml</code>."])  # fmt: skip
    else:
        state, tag, headline, meaning, nxt = (
            "fail", "Failed", "The grader disagrees with people too often",
            "Scores from this grader cannot be used for promotion yet.",
            ["Fix the coverage judge's prompt where it was wrong, then calibrate again with new "
             "pairs (a new seed)."])  # fmt: skip
    inter = result["inter_annotator_agreement"]
    numbers = [
        {"label": "Grader agrees with people", "value": _pct(agreement),
         "good": None if agreement is None else agreement >= minimum,
         "explain": f"Over the pairs where the people's majority is clear; the lower of the raw "
                    f"and the natural-rate figure. Needs {_pct(minimum)}.{_interval(result)}"},
        {"label": "People agree with each other", "value": _pct(inter),
         "good": None if inter is None else inter >= 0.8,  # promotion.CALIBRATION_MIN_INTER
         "explain": "If this is low, the rule itself is unclear: fix the wording before trusting "
                    "any grader result."},
        {"label": "Pairs with a clear answer", "value": f"{result['pairs_resolved']} of {result['pairs_sampled']}",
         "good": None,
         "explain": f"{result['excluded']} left out: both said can't tell, or the people split. "
                    "At least 24 are needed for the result to count."},
        {"label": "Grader too generous", "value": str(result["confusion"]["judge_covered_human_not"]),
         "good": None, "explain": "Pairs the grader counted as found that people did not."},
        {"label": "Grader too strict", "value": str(result["confusion"]["judge_missed_human_covered"]),
         "good": None, "explain": "Pairs the grader counted as missed that people found."},
    ]  # fmt: skip

    def one(pid: str, section: str) -> dict:
        p, c, v = key["pairs"][pid], cards.get(pid, {}), verdicts.get(pid, {})
        return {
            "id": pid, "case": p["case_id"], "section": section,
            "tag": "grader: covered" if p["judge_covered"] else "grader: missed",
            "expected": c.get("expected", {}), "dossier": c.get("dossier", []),
            "people": [{"name": a, "label": answers[a].get(pid), "note": notes.get(a, {}).get(pid, "")}
                       for a in sorted(answers)],
            "ai": {"label": "covered" if p["judge_covered"] else "not_covered",
                   "reason": v.get("justification", ""), "matched": v.get("impact_id")},
        }  # fmt: skip

    out = [one(p, "disagree") for p in result["disagreements"]]
    out += [one(p, "unsure") for p in result["excluded_pairs"]]
    data = {"kind": "calibration", "sample": key.get("sample", "calibration"),
            "title": "Grader check: review",
            "verdict": {"state": state, "tag": tag, "headline": headline, "meaning": meaning,
                        "next": nxt},
            "numbers": numbers, "cards": out}  # fmt: skip
    return _write(directory, data)
