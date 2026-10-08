"""Coverage-judge calibration (golden-case plan, Revision 2026-10-04): people label about 30
(dossier, expected impact) pairs as covered or not, blind to the judge, and WOMM's coverage
judge must agree with them on at least 85% before any formal promotion run.

``womm calibrate sample`` draws the pairs and writes the labeling material;
``womm calibrate score`` compares the labels with the judge.

**Population.** Every expected impact the judge gave a verdict on, in the scored train/val runs
of one or more eval reports (``womm eval`` writes them to ``runs/``) whose judge is the one
being calibrated. Holdout material is refused: a report claiming a holdout split, and any case
that is not a public golden case.

**Stratification.** The judge marks most expected impacts of a good dossier as covered, so a
uniform sample would hold few judge misses, the verdicts most likely to be wrong. The sample is
split in two strata by the judge's verdict, half judge-covered and half judge-missed (the other
stratum fills in when one runs short). Within a stratum, pairs are taken round-robin over
cases, distinct expected impacts first, so no case dominates.

**Reweighting.** Agreement is reported twice: raw (over the labeled sample) and at the natural
rate, where each stratum's agreement is weighted by its share of the population
(``p * agree_covered + (1 - p) * agree_missed``, ``p`` the judge's covered rate over every pair
in the population). The gate's ``agreement`` is the lower of the two, so the stratification
can never flatter the judge. Cohen's kappa (judge vs the human majority) is computed on the
sample, where both classes are well represented.

**Blind labeling.** The sheets and answer files never show the judge's verdict, matched impact
or justification. The verdicts are kept in ``key.private.json`` in the same directory under
``.cache/`` (gitignored); do not send that file to annotators. The sheets list pairs in a
different order per annotator.

**Labels.** ``covered``, ``not_covered`` or ``unsure``. ``unsure`` votes are left out of the
majority and counted; a pair with no majority (all unsure, or a tie) is excluded and counted.
"""

from __future__ import annotations

import datetime as dt
import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from womm.eval.golden import HOLDOUT_REFUSAL, GoldenCase, GoldenError
from womm.eval.run_eval import read_report, report_judge_versions

LABELS = ("covered", "not_covered", "unsure")
KEY_FILE = "key.private.json"
README_FILE = "README.md"
MIN_PAIRS_ADVISED = 30
STRATIFICATION = (
    "two strata by the judge's verdict (covered / not covered), half of the pairs from each "
    "(the other stratum fills in when one runs short); round-robin over cases within a "
    "stratum, distinct expected impacts first"
)

RUBRIC = """\
**The standard (the same one the judge applies).** The system only saw the legal text, not the
impact assessment's cost data or surveys. Judge on **affected actor + mechanism of effect**,
and ignore missing quantities (euro amounts, percentages, FTE numbers).

- **covered**: at least one dossier impact names the same or a clearly overlapping actor group
  *and* the same mechanism. A more specific dossier impact covers a broader expected one, but
  not the reverse. Wording may differ.
- **not_covered**: no dossier impact does. Judge only what the dossier says; do not reward
  impacts that are plausible but absent from it.
- **unsure**: you cannot decide after reading carefully. Say why in `note`; unsure pairs are
  left out and counted."""


class CalibrationError(ValueError):
    """Bad input for a calibration step: nothing was written."""


@dataclass(frozen=True)
class Pair:
    """One (run, expected impact) the judge gave a verdict on."""

    case_id: str
    fixture: str
    split: str
    run_id: str
    expected_id: str
    judge_covered: bool
    report: str


# ----------------------------------------------------------------------------- sampling


def population(
    reports: list[Path], cases: dict[str, GoldenCase], *, judge_version: str, system_version: str
) -> list[Pair]:
    """Every expected-impact verdict of the reports' scored runs, for one judge; holdout is
    refused (a report with a holdout split, or a case that is not a public golden case)."""
    pairs: list[Pair] = []
    for path in reports:
        data = read_report(path)
        jvs = report_judge_versions(data)
        if jvs and jvs != {judge_version}:
            raise CalibrationError(f"{path} was scored by judge {sorted(jvs)}, not "
                                   f"{judge_version}")  # fmt: skip
        if not jvs and data.get("system_version") != system_version:
            raise CalibrationError(f"{path} records no judge version and is not a report of "
                                   f"{system_version}")  # fmt: skip
        for s in data["scores"]:
            case = cases.get(s["case_id"])
            if case is None:
                raise GoldenError(f"{path}: {s['case_id']} is not a public golden case; "
                                  f"{HOLDOUT_REFUSAL}")  # fmt: skip
            if case.split not in ("train", "val"):
                raise GoldenError(f"{case.case_id}: {HOLDOUT_REFUSAL}")
            judge = s.get("judge")
            if s.get("outcome") != "scored" or not judge or s.get("judge_error"):
                continue
            pairs += [
                Pair(case.case_id, case.fixture, case.split, s["run_id"], v["expected_id"],
                     bool(v["covered"]), str(path))
                for v in judge["expected"]
            ]  # fmt: skip
    return pairs


def _spread(pairs: list[Pair], k: int, rng: random.Random) -> list[Pair]:
    """Up to ``k`` pairs, round-robin over cases, distinct expected impacts first."""
    by_case: dict[str, list[Pair]] = defaultdict(list)
    for p in pairs:
        by_case[p.case_id].append(p)
    queues = []
    for case_id in sorted(by_case):
        items = by_case[case_id]
        rng.shuffle(items)
        seen: dict[str, int] = defaultdict(int)
        ranked = []
        for p in items:
            ranked.append((seen[p.expected_id], p))
            seen[p.expected_id] += 1
        queues.append([p for _, p in sorted(ranked, key=lambda r: r[0])])
    rng.shuffle(queues)
    out: list[Pair] = []
    while len(out) < k and any(queues):
        for q in queues:
            if q and len(out) < k:
                out.append(q.pop(0))
    return out


def stratified_sample(pairs: list[Pair], n: int, seed: int) -> list[Pair]:
    """Half judge-covered, half judge-missed (see the module docstring)."""
    rng = random.Random(seed)
    covered = [p for p in pairs if p.judge_covered]
    missed = [p for p in pairs if not p.judge_covered]
    want_missed = min(len(missed), n // 2)
    want_covered = min(len(covered), n - want_missed)
    want_missed = min(len(missed), n - want_covered)
    return _spread(covered, want_covered, rng) + _spread(missed, want_missed, rng)


# ----------------------------------------------------------------------------- writing


def _load_run(run_id: str, dirs: list[Path]) -> dict[str, Any] | None:
    for d in dirs:
        path = d / f"{run_id}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def _dossier_md(run: dict[str, Any]) -> list[str]:
    impacts = (run.get("dossier") or {}).get("impacts") or []
    if not impacts:
        return ["_The dossier has no impacts._"]
    out = []
    for imp in impacts:
        out.append(f"- **{imp.get('impact_id', '?')}**: {imp.get('summary', '').strip()}")
        for f in imp.get("findings", []):
            out.append(f"  - actor: {f.get('affected_actor', '')}; mechanism: "
                       f"{f.get('mechanism', '')}; impact: {f.get('impact', '')}")  # fmt: skip
    return out


def render_sheet(
    annotator: str, ordered: list[tuple[str, Pair]], cases: dict[str, GoldenCase],
    runs: dict[str, dict[str, Any]],
) -> str:  # fmt: skip
    """A self-contained Markdown labeling sheet. It never shows the judge's verdict."""
    lines = [
        f"# Coverage-judge calibration: labeling sheet for {annotator}",
        "",
        f"{len(ordered)} pairs. For each pair, read the expected impact (written from the "
        "official impact assessment), then the dossier the system produced, and decide whether "
        f"the dossier covers the expected impact. Write your label in `answers_{annotator}.yaml` "
        "next to this file (one row per pair id). Work alone; do not discuss pairs with other "
        "annotators until everyone has finished.",
        "",
        RUBRIC,
        "",
    ]
    for pair_id, p in ordered:
        expected = next(e for e in cases[p.case_id].expected_impacts
                        if e.expected_id == p.expected_id)  # fmt: skip
        lines += [
            "---", "", f"## {pair_id}", "",
            f"Case `{p.case_id}` ({p.fixture}).", "",
            "**Expected impact**", "",
            f"- affected actor: {expected.affected_actor}",
            f"- mechanism: {expected.mechanism}",
            f"- impact: {expected.impact}", "",
            "**Dossier impacts**", "",
            *_dossier_md(runs[p.run_id]), "",
            f"Label for `{pair_id}`: covered / not_covered / unsure", "",
        ]  # fmt: skip
    return "\n".join(lines)


def render_answers(annotator: str, pair_ids: list[str]) -> str:
    rows = [
        f"# Answers of {annotator} for the coverage-judge calibration (sheet_{annotator}.md).",
        "# For every pair set label to covered, not_covered or unsure; note is optional (say",
        "# why for unsure). Do not add or remove rows.",
        f"annotator: {annotator}",
        "answers:",
    ]
    for pid in pair_ids:
        rows += [f"- pair_id: {pid}", "  label:", "  note: ''"]
    return "\n".join(rows) + "\n"


def write_sample(
    out_dir: Path,
    *,
    system_version: str,
    judge_version: str,
    reports: list[Path],
    cases: dict[str, GoldenCase],
    runs_dirs: list[Path],
    n: int,
    seed: int,
    annotators: list[str],
) -> dict[str, Any]:
    """Draw the sample and write the sheets, answer files and private key into ``out_dir``."""
    if n < 2:
        raise CalibrationError("--n must be at least 2")
    if not annotators or len(set(annotators)) != len(annotators):
        raise CalibrationError("give each annotator a distinct name")
    for name in annotators:
        if not name.replace("_", "").replace("-", "").isalnum():
            raise CalibrationError(f"annotator name {name!r}: letters, digits, - and _ only")
    if (out_dir / KEY_FILE).exists():
        raise CalibrationError(f"{out_dir / KEY_FILE} exists; pick a new --out directory")
    pairs = population(reports, cases, judge_version=judge_version,
                       system_version=system_version)  # fmt: skip
    runs: dict[str, dict[str, Any]] = {}
    for run_id in sorted({p.run_id for p in pairs}):
        dirs = [*{Path(p.report).parent for p in pairs if p.run_id == run_id}, *runs_dirs]
        if (run := _load_run(run_id, dirs)) is not None:
            runs[run_id] = run
    missing_runs = len({p.run_id for p in pairs} - runs.keys())
    usable = [p for p in pairs if p.run_id in runs]
    if not usable:
        raise CalibrationError("no judged pair with its run file (runs/<run_id>.json) found in "
                               "the reports; run `womm eval` with a runs directory")  # fmt: skip
    sample = stratified_sample(usable, n, seed)
    rng = random.Random(seed)
    rng.shuffle(sample)
    width = len(str(len(sample)))
    ids = [f"p{i + 1:0{width}d}" for i in range(len(sample))]
    keyed = dict(zip(ids, sample, strict=True))
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in annotators:
        order = list(ids)
        random.Random(f"{seed}:{name}").shuffle(order)
        (out_dir / f"sheet_{name}.md").write_text(
            render_sheet(name, [(i, keyed[i]) for i in order], cases, runs), encoding="utf-8"
        )
        (out_dir / f"answers_{name}.yaml").write_text(render_answers(name, ids), encoding="utf-8")
    covered_pop = sum(p.judge_covered for p in usable)
    key = {
        "judge_version": judge_version,
        "system_version": system_version,
        "seed": seed,
        "n_requested": n,
        "created_on": dt.date.today().isoformat(),
        "reports": [str(r) for r in reports],
        "annotators": annotators,
        "stratification": STRATIFICATION,
        "population": {
            "pairs": len(usable),
            "judge_covered": covered_pop,
            "judge_missed": len(usable) - covered_pop,
            "natural_covered_rate": covered_pop / len(usable),
            "runs_missing": missing_runs,
        },
        "pairs": {pid: asdict(p) for pid, p in keyed.items()},
    }
    (out_dir / KEY_FILE).write_text(json.dumps(key, indent=2), encoding="utf-8")
    (out_dir / README_FILE).write_text(_readme(annotators), encoding="utf-8")
    return {
        "out": str(out_dir),
        "pairs": len(sample),
        "judge_covered": sum(p.judge_covered for p in sample),
        "judge_missed": sum(not p.judge_covered for p in sample),
        "cases": len({p.case_id for p in sample}),
        "population": key["population"],
        "annotators": annotators,
    }


def _readme(annotators: list[str]) -> str:
    files = "\n".join(f"- {a}: `sheet_{a}.md` and `answers_{a}.yaml`" for a in annotators)
    return f"""# Coverage-judge calibration sample

Send each annotator their two files, and nothing else:

{files}

`{KEY_FILE}` holds the judge's verdicts: keep it here and never send it to an annotator.
When the answer files are back in this directory, run `womm calibrate score --dir <this dir>`,
then `--record` to append the result to `evals/promotion_records.yaml`, and commit that file.
"""


# ----------------------------------------------------------------------------- scoring


def read_answers(directory: Path, pair_ids: set[str]) -> dict[str, dict[str, str | None]]:
    """Annotator -> pair id -> label (None when left blank) from every ``answers_*.yaml``."""
    out: dict[str, dict[str, str | None]] = {}
    for path in sorted(directory.glob("answers_*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise CalibrationError(f"{path.name}: {exc}") from None
        name = data.get("annotator") or path.stem.removeprefix("answers_")
        labels: dict[str, str | None] = {}
        for row in data.get("answers") or []:
            pid = str(row.get("pair_id"))
            label = row.get("label")
            label = label.strip().lower() if isinstance(label, str) and label.strip() else None
            if pid not in pair_ids:
                raise CalibrationError(f"{path.name}: unknown pair id {pid}")
            if pid in labels:
                raise CalibrationError(f"{path.name}: pair {pid} is answered twice")
            if label is not None and label not in LABELS:
                raise CalibrationError(f"{path.name}: {pid} has label {label!r}; use one of "
                                       f"{', '.join(LABELS)}")  # fmt: skip
            labels[pid] = label
        if missing := sorted(pair_ids - labels.keys()):
            raise CalibrationError(f"{path.name}: rows removed for {', '.join(missing)}")
        if name in out:
            raise CalibrationError(f"two answer files for annotator {name}")
        out[name] = labels
    if not out:
        raise CalibrationError(f"no answers_*.yaml in {directory}")
    return out


def _kappa(a: list[bool], b: list[bool]) -> float | None:
    """Cohen's kappa for two binary raters; None when undefined (no pairs or pe == 1)."""
    if not a:
        return None
    n = len(a)
    po = sum(x == y for x, y in zip(a, b, strict=True)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe >= 1 else (po - pe) / (1 - pe)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def score(key: dict[str, Any], answers: dict[str, dict[str, str | None]]) -> dict[str, Any]:
    """Agreement of the judge with each annotator and with the majority (see the docstring)."""
    judge = {pid: bool(p["judge_covered"]) for pid, p in key["pairs"].items()}
    per_annotator = {}
    for name, labels in answers.items():
        decided = {pid: lab == "covered" for pid, lab in labels.items()
                   if lab in ("covered", "not_covered")}  # fmt: skip
        per_annotator[name] = {
            "labeled": len(decided),
            "unsure": sum(lab == "unsure" for lab in labels.values()),
            "blank": sum(lab is None for lab in labels.values()),
            "agreement": _mean([float(v == judge[pid]) for pid, v in decided.items()]),
        }
    majority: dict[str, bool] = {}
    for pid in judge:
        votes = [answers[a][pid] for a in answers if answers[a][pid] in ("covered", "not_covered")]
        yes, no = votes.count("covered"), votes.count("not_covered")
        if yes != no:
            majority[pid] = yes > no
    resolved = sorted(majority)
    agree = {pid: majority[pid] == judge[pid] for pid in resolved}
    raw = _mean([float(v) for v in agree.values()])
    by_stratum = {
        label: _mean([float(agree[pid]) for pid in resolved if judge[pid] is flag])
        for label, flag in (("judge_covered", True), ("judge_missed", False))
    }
    p = key["population"]["natural_covered_rate"]
    natural = None
    if raw is not None:
        cov, miss = by_stratum["judge_covered"], by_stratum["judge_missed"]
        if (cov is not None or p == 0) and (miss is not None or p == 1):
            natural = p * (cov or 0.0) + (1 - p) * (miss or 0.0)
    names = sorted(answers)
    inter_agree, inter_kappa = [], []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            both = [pid for pid in judge if answers[a][pid] in ("covered", "not_covered")
                    and answers[b][pid] in ("covered", "not_covered")]  # fmt: skip
            xa = [answers[a][pid] == "covered" for pid in both]
            xb = [answers[b][pid] == "covered" for pid in both]
            if both:
                inter_agree.append(sum(x == y for x, y in zip(xa, xb, strict=True)) / len(both))
            if (k := _kappa(xa, xb)) is not None:
                inter_kappa.append(k)
    confusion = {
        "judge_covered_human_covered": sum(judge[i] and majority[i] for i in resolved),
        "judge_covered_human_not": sum(judge[i] and not majority[i] for i in resolved),
        "judge_missed_human_covered": sum(not judge[i] and majority[i] for i in resolved),
        "judge_missed_human_not": sum(not judge[i] and not majority[i] for i in resolved),
    }
    candidates = [v for v in (raw, natural) if v is not None]
    return {
        "judge_version": key["judge_version"],
        "pairs_sampled": len(judge),
        "pairs_resolved": len(resolved),
        "excluded": len(judge) - len(resolved),
        "annotators": per_annotator,
        "agreement_raw": raw,
        "agreement_natural": natural,
        "agreement_by_stratum": by_stratum,
        "natural_covered_rate": p,
        "agreement": min(candidates) if candidates else None,
        "kappa": _kappa([judge[i] for i in resolved], [majority[i] for i in resolved]),
        "inter_annotator_agreement": _mean(inter_agree),
        "inter_annotator_kappa": _mean(inter_kappa),
        "confusion": confusion,
    }


def calibration_record(result: dict[str, Any], recorded_on: str) -> dict[str, Any]:
    """The ``judge_calibrations`` entry: aggregates only, never a case or pair id."""

    def r(x: float | None) -> float | None:
        return None if x is None else round(x, 4)

    record = {
        "judge_version": result["judge_version"],
        "agreement": r(result["agreement"]),
        "pairs": result["pairs_resolved"],
        "annotators": sorted(result["annotators"]),
        "recorded_on": recorded_on,
        "agreement_raw": r(result["agreement_raw"]),
        "agreement_natural": r(result["agreement_natural"]),
        "kappa": r(result["kappa"]),
        "inter_annotator_agreement": r(result["inter_annotator_agreement"]),
        "excluded": result["excluded"],
    }
    return {k: v for k, v in record.items() if v is not None}


def record_problems(result: dict[str, Any]) -> list[str]:
    """Why a result may not be recorded (empty when it may)."""
    problems = [f"{name} left {a['blank']} pair(s) blank"
                for name, a in result["annotators"].items() if a["blank"]]  # fmt: skip
    if result["agreement"] is None:
        problems.append("no pair has a human majority label")
    return problems


def format_result(result: dict[str, Any]) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.1%}"

    def num(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.3f}"

    lines = [
        f"coverage-judge calibration for {result['judge_version']}",
        f"pairs: {result['pairs_sampled']} sampled, {result['pairs_resolved']} with a majority "
        f"label, {result['excluded']} excluded (unsure or tied)",
    ]
    for name, a in sorted(result["annotators"].items()):
        lines.append(f"  {name}: agreement {pct(a['agreement'])} over {a['labeled']} labeled "
                     f"({a['unsure']} unsure, {a['blank']} blank)")  # fmt: skip
    s = result["agreement_by_stratum"]
    lines += [
        f"judge vs majority: raw {pct(result['agreement_raw'])}; judge-covered stratum "
        f"{pct(s['judge_covered'])}, judge-missed stratum {pct(s['judge_missed'])}",
        f"natural-rate agreement {pct(result['agreement_natural'])} (judge covered rate "
        f"{result['natural_covered_rate']:.1%})",
        f"gate agreement (the lower): {pct(result['agreement'])}; Cohen's kappa "
        f"{num(result['kappa'])}",
        f"inter-annotator agreement {pct(result['inter_annotator_agreement'])}, kappa "
        f"{num(result['inter_annotator_kappa'])}",
        "confusion (judge / majority): " + ", ".join(f"{k} {v}"
                                                    for k, v in result["confusion"].items()),
    ]  # fmt: skip
    if result["pairs_resolved"] < MIN_PAIRS_ADVISED:
        lines.append(f"note: fewer than {MIN_PAIRS_ADVISED} labeled pairs; the plan asks for "
                     "about 30")  # fmt: skip
    return "\n".join(lines)
