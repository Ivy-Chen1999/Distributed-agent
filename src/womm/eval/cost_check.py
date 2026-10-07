"""The R6 IA cost check (EU cost plan U6): cost records scored against SWD(2021) 84.

Scoring side only. The reference (``evals/cost_reference/``) and this module never reach any
agent input: ``womm.graph``, ``womm.cost`` and the Planner-side ``womm.evolve`` modules must not
import this module (``tests/cost/test_ia_isolation.py``). Cost scores are not GEPA scores,
``sv_metrics`` rows, Failure Memory rows or promotion metrics.

Pre-registered metrics (computed per repetition, reported as mean with min-max):

- ``cost_recall``: share of IA items with a non-negligible figure for which at least one
  estimated record on the item's keys has the IA's payer and a band of ``low`` or higher;
- ``band_exact`` / ``band_within_one``: per IA item with a band, the highest band among matching
  records (in the item's recurrence) against the band of the IA figure;
- ``payer_recurrence_agreement``: the IA's recurring items (human oversight and user
  documentation on deployers, the authorities' staff): a matching record carries a recurring
  band;
- ``rank_tau_b``: Kendall's tau-b between band points and IA figures over the items that share
  a unit (one-off, per application, provider). Descriptive only, printed with its n;
- ``ia_silent_costly``: provisions with records at ``medium`` or higher that no IA item covers.
  Reported, never scored (IA silence does not mean "no cost").
"""

from __future__ import annotations

import math
import statistics
import subprocess
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError

from womm.config import REPO_ROOT
from womm.cost.categories import FTE_EUR, band_for_eur
from womm.data.corpus import Corpus
from womm.models.base import StrictModel
from womm.models.cost import Band, CostRecord, band_rank

REFERENCE_PATH = REPO_ROOT / "evals" / "cost_reference" / "ai_act_swd2021_84.yaml"
SCORED = ("cost_recall", "band_exact", "band_within_one", "payer_recurrence_agreement")
COSTLY = band_rank("medium")


class CostReferenceError(ValueError):
    pass


class ReferenceItem(StrictModel):
    item_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    ia_section: str = Field(min_length=1)
    proposal_articles: list[str]
    proposal_annexes: list[str] = Field(default_factory=list)
    payer: str = Field(min_length=1)
    payers: list[str] = Field(min_length=1)
    recurrence: Literal["one_off", "recurring", "both"]
    figure_text: str = Field(min_length=1)
    eur_low: float | None = None
    eur_high: float | None = None
    fte_low: float | None = None
    fte_high: float | None = None
    negligible: bool = False
    unit: Literal[
        "per_application", "per_year", "per_day", "fte_per_member_state", "fte_eu", "none"
    ]
    comparable_unit: bool
    inconsistency: str | None = None
    # Filled by the loader: provision keys through the corpus index, and the computed band.
    keys: list[str] = Field(default_factory=list)
    band: Band | None = None

    @property
    def midpoint_eur(self) -> float | None:
        if self.eur_low is not None and self.eur_high is not None:
            return (self.eur_low + self.eur_high) / 2
        if self.fte_low is not None and self.fte_high is not None:
            return (self.fte_low + self.fte_high) / 2 * FTE_EUR
        return None


class CostReference(StrictModel):
    reference_id: str
    ia: str
    proposal_version: str
    status: Literal["unverified", "verified"]
    verified_by: str | None = None
    verified_on: str | None = None
    notes: str
    items: list[ReferenceItem] = Field(min_length=1)


def _band(item: ReferenceItem) -> Band | None:
    if item.negligible:
        return "negligible"
    if item.unit in ("per_day", "none"):
        return None  # no per-item unit: the item counts for recall only
    mid = item.midpoint_eur
    if mid is None:
        raise CostReferenceError(f"{item.item_id}: unit {item.unit} needs a figure")
    return band_for_eur(mid)


def _keys(item: ReferenceItem, corpus: Corpus, version: str) -> list[str]:
    rows = corpus.index_rows(version)
    keys: list[str] = []
    for kind, numbers in (("article", item.proposal_articles), ("annex", item.proposal_annexes)):
        for number in numbers:
            found = [r.key for r in rows if r.kind == kind and r.number == number]
            if not found:
                raise CostReferenceError(
                    f"{item.item_id}: {version} has no {kind} {number} in the corpus index"
                )
            keys.extend(found)
    if not keys:
        raise CostReferenceError(f"{item.item_id}: names no proposal article or annex")
    return list(dict.fromkeys(keys))


def load_reference(path: Path, corpus: Corpus) -> CostReference:
    """The verified reference with keys and bands filled. Refuses an unverified file, unfilled
    fields, and an article or annex the corpus index does not have."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CostReferenceError(f"cannot read {path}: {exc}") from None
    items = raw.get("items") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        raise CostReferenceError(f"{path}: no items")
    parsed = []
    for n, item in enumerate(items):
        try:
            parsed.append(ReferenceItem.model_validate(item))
        except ValidationError as exc:
            name = item.get("item_id", f"item {n}") if isinstance(item, dict) else f"item {n}"
            raise CostReferenceError(f"{path}: {name} is incomplete or invalid: {exc}") from None
    try:
        ref = CostReference.model_validate({**raw, "items": [p.model_dump() for p in parsed]})
    except ValidationError as exc:
        raise CostReferenceError(f"{path}: {exc}") from None
    if ref.status != "verified" or not ref.verified_by or not ref.verified_on:
        raise CostReferenceError(
            f"{path} is unverified: one person checks it against SWD(2021) 84 and sets status: "
            "verified, verified_by and verified_on before any scored check"
        )
    ids = [i.item_id for i in ref.items]
    if len(ids) != len(set(ids)):
        raise CostReferenceError(f"{path}: duplicate item ids")
    filled = [
        i.model_copy(update={"keys": _keys(i, corpus, ref.proposal_version), "band": _band(i)})
        for i in ref.items
    ]
    return ref.model_copy(update={"items": filled})


def reference_sha(path: Path = REFERENCE_PATH) -> str:
    """The commit that last changed the reference file, marked when the file has local edits."""
    try:
        sha = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", str(path)], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", str(path)], cwd=REPO_ROOT,
            capture_output=True, text=True, check=True,
        ).stdout.strip()  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    if not sha:
        return "uncommitted"
    return f"{sha} (with local edits)" if dirty else sha


# --- metrics -----------------------------------------------------------------------------------


def kendall_tau_b(x: list[float], y: list[float]) -> float | None:
    """Kendall's tau-b with ties; None when either side is constant (undefined)."""
    n = len(x)
    concordant = discordant = ties_x = ties_y = 0
    for i in range(n):
        for j in range(i + 1, n):
            dx, dy = x[i] - x[j], y[i] - y[j]
            if dx == 0:
                ties_x += 1
            if dy == 0:
                ties_y += 1
            if dx == 0 or dy == 0:
                continue
            if (dx > 0) == (dy > 0):
                concordant += 1
            else:
                discordant += 1
    n0 = n * (n - 1) / 2
    denom = math.sqrt((n0 - ties_x) * (n0 - ties_y))
    return None if denom == 0 else (concordant - discordant) / denom


def _predicted(item: ReferenceItem, matching: list[CostRecord]) -> Band | None:
    bands: list[Band | None]
    if item.recurrence == "one_off":
        bands = [r.one_off for r in matching]
    elif item.recurrence == "recurring":
        bands = [r.recurring for r in matching]
    else:
        bands = [r.top_band for r in matching]
    present = [b for b in bands if b is not None]
    return max(present, key=band_rank) if present else None


def _share(flags: list[bool]) -> float | None:
    return sum(flags) / len(flags) if flags else None


def score_records(
    records: list[CostRecord], ref: CostReference, *, restrict_keys: set[str] | None = None
) -> dict[str, Any]:
    """The metrics of one repetition. ``restrict_keys`` (a scenario's keys) scores only the IA
    items with a key among them."""
    items = [i for i in ref.items if restrict_keys is None or set(i.keys) & restrict_keys]
    estimated = [r for r in records if r.status == "estimated"]
    detail: dict[str, dict[str, Any]] = {}
    recall, exact, within, payer_rec = [], [], [], []
    tau_x, tau_y = [], []
    for item in items:
        keys = set(item.keys)
        on_keys = [r for r in estimated if r.provision_key in keys]
        matching = [r for r in on_keys if r.payer in item.payers]
        d: dict[str, Any] = {"ia_band": item.band, "matching_records": len(matching)}
        if item.band != "negligible":
            d["recalled"] = any(band_rank(r.top_band) >= band_rank("low") for r in matching)
            recall.append(d["recalled"])
        predicted = _predicted(item, matching)
        d["predicted_band"] = predicted
        if item.band is not None and item.band != "negligible":
            diff = abs(band_rank(predicted) - band_rank(item.band))
            d["band_exact"] = predicted == item.band
            d["band_within_one"] = predicted is not None and diff <= 1
            exact.append(d["band_exact"])
            within.append(d["band_within_one"])
        if item.recurrence == "recurring":
            d["payer_recurrence"] = any(r.recurring is not None for r in matching)
            payer_rec.append(d["payer_recurrence"])
        if item.comparable_unit and item.midpoint_eur is not None:
            tau_x.append(band_rank(_predicted(item, matching)))
            tau_y.append(item.midpoint_eur)
        detail[item.item_id] = d
    tau = kendall_tau_b(tau_x, tau_y) if len(tau_x) >= 2 else None
    covered = {k for i in ref.items for k in i.keys}
    silent = list(
        dict.fromkeys(
            r.provision_key
            for r in estimated
            if r.provision_key not in covered and band_rank(r.top_band) >= COSTLY
        )
    )
    scored_keys = {k for i in items for k in i.keys}
    bases: dict[str, int] = {}
    for r in records:
        if r.provision_key in scored_keys:
            bases[r.payer_basis] = bases.get(r.payer_basis, 0) + 1
    return {
        "cost_recall": _share(recall),
        "band_exact": _share(exact),
        "band_within_one": _share(within),
        "payer_recurrence_agreement": _share(payer_rec),
        "rank_tau_b": tau,
        "rank_n": len(tau_x),
        "rank_note": "descriptive, not significant"
        if tau is not None
        else "undefined: every comparable item has the same band (or the same figure)",
        "ia_silent_costly": silent,
        "payers_by_basis": dict(sorted(bases.items())),
        "items": detail,
    }


def _spread(values: list[float | None]) -> dict[str, Any]:
    present = [v for v in values if v is not None]
    if not present:
        return {"mean": None, "min": None, "max": None, "values": values}
    return {
        "mean": statistics.fmean(present),
        "min": min(present),
        "max": max(present),
        "values": values,
    }


def score_repetitions(
    repetitions: dict[int, list[CostRecord]],
    ref: CostReference,
    *,
    restrict_keys: set[str] | None = None,
) -> dict[str, Any]:
    per_rep = {
        k: score_records(v, ref, restrict_keys=restrict_keys)
        for k, v in sorted(repetitions.items())
    }
    reps = list(per_rep.values())
    bases: dict[str, int] = {}
    for m in reps:
        for b, n in m["payers_by_basis"].items():
            bases[b] = bases.get(b, 0) + n
    silent: dict[str, int] = {}
    for m in reps:
        for key in m["ia_silent_costly"]:
            silent[key] = silent.get(key, 0) + 1
    return {
        "repetitions": len(reps),
        "metrics": {name: _spread([m[name] for m in reps]) for name in SCORED},
        "rank_tau_b": _spread([m["rank_tau_b"] for m in reps]),
        "rank_n": reps[0]["rank_n"] if reps else 0,
        "ia_silent_costly": silent,
        "payers_by_basis": dict(sorted(bases.items())),
        "per_repetition": per_rep,
    }


def _fmt(s: dict[str, Any]) -> str:
    if s["mean"] is None:
        return "n/a"
    return f"{s['mean']:.2f} (min {s['min']:.2f}, max {s['max']:.2f})"


def format_report(out: dict[str, Any], header: dict[str, Any]) -> str:
    backend = header.get("backend")
    label = "formal (api backend)" if backend == "api" else f"dev-only ({backend} backend)"
    lines = [
        "# IA cost check (R6): SWD(2021) 84",
        "",
        f"- reference sha: {header.get('reference_sha')}",
        f"- system version: {header.get('system_version')}",
        f"- source: {header.get('source')}",
        f"- label: {label}",
        f"- repetitions: {out['repetitions']}",
    ]
    if header.get("restricted_to"):
        lines.append(f"- scored items limited to the keys of {header['restricted_to']}")
    lines += ["", "| metric | mean (min, max) |", "|---|---|"]
    lines += [f"| {name} | {_fmt(out['metrics'][name])} |" for name in SCORED]
    lines += [
        "",
        f"rank_tau_b: {_fmt(out['rank_tau_b'])}, n = {out['rank_n']} "
        "(descriptive, not significant; undefined when the comparable items share one band)",
        "",
        "Payers of the scored records by basis (recall depends on the payer fallback): "
        + (", ".join(f"{b} {n}" for b, n in out["payers_by_basis"].items()) or "none"),
        "",
        "Provisions with records at medium or higher that no IA item covers (reported, not "
        "scored; repetitions in which they appear):",
    ]
    silent = out["ia_silent_costly"]
    lines += [f"- {k}: {n}" for k, n in silent.items()] or ["- none"]
    lines += [
        "",
        "Limits: the IA quantifies about 11 cost items covering about a quarter of the "
        "proposal's duties; IA silence is not 'no cost'; only five items share a unit, so rank "
        "agreement says little.",
    ]
    return "\n".join(lines)
