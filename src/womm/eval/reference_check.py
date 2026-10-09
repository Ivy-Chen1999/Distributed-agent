"""One-time robustness check of the reference answers the drafting tool settles without people.

Since 2026-10-08 nobody has to review a train/val golden draft: the three judges accept what they
agree on and the tie-break judge (``womm.eval.tiebreak``) rules on the rest. Anthropic's guidance
is that human review may become occasional *once the system is robust* ("Demystifying evals for
AI agents"), so this check measures that once, on a small blind sample, before the spot checks
become optional for good. ``scripts/reference_check.py sample`` draws the items and writes one
labeling page per annotator; ``scripts/reference_check.py score`` compares the labels with the
tool.

**Population.** Every tool-decided item of the train/val drafts given: kept by the three judges
(``auto_accepted``), kept by the tie-break (``llm_kept``) or dropped by it (``llm_dropped``).
Items a person decided and items dropped by a deterministic check (anchor not found, unknown
provision keys) are left out: there is no judgement to check. Holdout drafts are refused.

**Strata.** Three, by who decided what: ``judges_accepted``, ``tiebreak_kept`` and
``tiebreak_dropped``, about a third of the sample each (the others fill in when one runs short),
round-robin over cases within a stratum so no case dominates.

**Blind labeling.** The page shows each item's claim, its quote and the IA text around it, and
the case's other kept items that are not in the sample (to spot repeats; a sampled item never
appears there, or its presence would give away the tool's decision). It never shows the tool's
decision, the judges' verdicts or the tie-break's reason; those stay in ``key.private.json``.

**Labels and several annotators.** ``keep``, ``drop`` or ``unsure``, each annotator alone. An
item is settled when more than half of the annotators give the same keep/drop label; otherwise
(a split, or an ``unsure``) ``score`` writes ``reconcile.html``, which shows those items with
each person's label and note (still never the tool's decision), and the annotators pick one label
together; the page downloads ``reconciled.yaml``. Agreement before reconciling is reported as raw
agreement and Cohen's kappa (mean over pairs). Every file carries the sample's id, so answers
from another sample are refused.

**Gate.** The tool's error rate against the human label, raw and reweighted to the strata's
shares of the population; the gate uses the higher of the two, and an item still unsettled
counts as an error. At or below ``MAX_ERROR`` (the same 10% that escalates a proposal to full
review), with every annotator's answers complete, the reference answers count as robust and later
spot checks stay optional; above it, fix the prompts, re-draft and check again. With about 20
items this catches gross failures, not small ones: the one-sided 95% upper bound printed next to
the rate says how far the true rate could be (no error in 20 items still allows about 12%).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import random
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

from womm.citations import normalize
from womm.eval import ia_sources
from womm.eval.drafting import DraftCandidate, GoldenDraft, _DraftItem
from womm.eval.golden_review import ESCALATION_RATE, KEPT_DECISIONS

LABELS = ("keep", "drop", "unsure")
STRATA = ("judges_accepted", "tiebreak_kept", "tiebreak_dropped")
MAX_ERROR = ESCALATION_RATE
PAGE_TEMPLATE = Path(__file__).with_name("reference_check_page.html")
KEY_FILE = "key.private.json"
CARDS_FILE = "cards.json"
README_FILE = "README.md"
RECONCILE_PAGE = "reconcile.html"
RECONCILED_FILE = "reconciled.yaml"
# One-sided 95% for the upper bound printed next to the error rate.
Z_95 = 1.645
SEND_TO = "the person who sent you this page"
CONTEXT_CHARS = 600
# ``why_missing`` is the recall step's own argument for a candidate: not shown, to stay blind.
CLAIM_KEYS = ("affected_actor", "mechanism", "impact", "description")


class ReferenceCheckError(ValueError):
    """Bad input for a check step: nothing was written."""


@dataclass(frozen=True)
class Item:
    case_id: str
    fixture: str
    item_id: str
    stratum: str
    tool_kept: bool


def stratum(item: _DraftItem) -> str | None:
    """Who decided the item, or None when there is no tool judgement to check."""
    decision = item.review.decision
    if decision == "llm_kept":
        return "tiebreak_kept"
    if decision == "llm_dropped":
        deterministic = item.tiebreak is None or item.tiebreak.model == "deterministic"
        return None if deterministic else "tiebreak_dropped"
    if (
        decision == "auto_accepted"
        and item.tiebreak is None
        and not isinstance(item, DraftCandidate)
    ):
        return "judges_accepted"
    return None


def population(drafts: list[GoldenDraft]) -> list[Item]:
    items = []
    for draft in drafts:
        if draft.split == "holdout":
            raise ReferenceCheckError(f"{draft.case_id}: a holdout draft is verified by its owner")
        for item in draft.items():
            if (s := stratum(item)) is not None:
                kept = item.review.decision in KEPT_DECISIONS
                items.append(Item(draft.case_id, draft.fixture, item.item_id, s, kept))
    return items


def _spread(items: list[Item], k: int, rng: random.Random) -> list[Item]:
    """Up to ``k`` items, round-robin over cases."""
    by_case: dict[str, list[Item]] = defaultdict(list)
    for i in items:
        by_case[i.case_id].append(i)
    queues = [rng.sample(by_case[c], len(by_case[c])) for c in sorted(by_case)]
    rng.shuffle(queues)
    out: list[Item] = []
    while len(out) < k and any(queues):
        for q in queues:
            if q and len(out) < k:
                out.append(q.pop(0))
    return out


def stratified_sample(items: list[Item], n: int, seed: int) -> list[Item]:
    """About n/3 per stratum; a short stratum's remainder goes to the others in turn."""
    rng = random.Random(seed)
    pools = {s: [i for i in items if i.stratum == s] for s in STRATA}
    want = {s: 0 for s in STRATA}
    left = min(n, len(items))
    while left:
        open_ = [s for s in STRATA if want[s] < len(pools[s])]
        for s in open_[:left]:
            want[s] += 1
            left -= 1
    return [i for s in STRATA for i in _spread(pools[s], want[s], rng)]


# ----------------------------------------------------------------------------- writing


def _others(draft: GoldenDraft, sampled: set[str]) -> list[str]:
    """The case's kept items outside the sample: whether a sampled item was kept stays hidden."""
    out = []
    for other in draft.items():
        if other.item_id in sampled or other.review.decision not in KEPT_DECISIONS:
            continue
        actor = getattr(other, "affected_actor", "")
        what = getattr(other, "mechanism", None) or getattr(other, "description", "")
        out.append(f"{actor}: {what}" if actor else what)
    return out


def _passage(quote: str, text: str, chars: int) -> dict[str, str] | None:
    """The IA text around the quote's first segment, case kept, split into before / quote /
    after so the page can highlight the quote; None when the quote is not found."""
    cased = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text))
    low = cased.casefold()
    if len(low) != len(cased):
        cased = low
    first = re.split(r"\[?\s*\.{3,}\s*\]?", normalize(quote))[0].strip(" .,;:")
    pos = low.find(first) if first else -1
    if pos < 0:  # quotes or hyphenation differ: fall back to the normalised (lower-case) text
        cased = low = normalize(text)
        pos = low.find(first) if first else -1
    if pos < 0:
        return None
    start, end = max(0, pos - chars), min(len(cased), pos + len(first) + chars)
    return {
        "before": ("..." if start else "") + cased[start:pos],
        "quote": cased[pos : pos + len(first)],
        "after": cased[pos + len(first) : end] + ("..." if end < len(cased) else ""),
    }


def card(
    item_id: str,
    it: Item,
    drafts: dict[str, GoldenDraft],
    ia_texts: dict[str, str | None],
    sampled: set[str],
) -> dict[str, Any]:
    """What the labeling page shows for one item: never the tool's decision or reasons.
    ``sampled`` holds the item ids of this case that are in the sample."""
    draft = drafts[it.case_id]
    item = next(i for i in draft.items() if i.item_id == it.item_id)
    text = ia_texts.get(draft.fixture)
    on_ia = item.anchor.against == "ia"
    return {
        "id": item_id,
        "case": draft.case_id,
        "fixture": draft.fixture,
        "provisions": item.provision_keys,
        "claim": {k: getattr(item, k) for k in CLAIM_KEYS if hasattr(item, k)},
        "source": "report" if on_ia else "scrutiny board opinion",
        "section": item.ia_section,
        "quote": item.ia_anchor,
        "passage": _passage(item.ia_anchor, text, CONTEXT_CHARS) if text and on_ia else None,
        "others": _others(draft, sampled),
    }


def render_page(
    annotator: str,
    cards: list[dict[str, Any]],
    *,
    sample: str,
    send_to: str = SEND_TO,
    reconcile: bool = False,
) -> str:
    """A self-contained HTML labeling page: one item per screen, answers kept in the browser and
    downloaded as ``answers_<annotator>.yaml`` (``reconciled.yaml`` for the reconcile page, whose
    cards carry each annotator's ``votes``)."""
    meta = {
        "annotator": annotator,
        "sample": sample,
        "sendTo": send_to,
        "mode": "reconcile" if reconcile else "label",
        "file": RECONCILED_FILE if reconcile else f"answers_{annotator}.yaml",
    }
    data = json.dumps(meta | {"items": cards}, ensure_ascii=False).replace("</", "<\\/")
    page = PAGE_TEMPLATE.read_text(encoding="utf-8")
    return (
        page.replace("__ANNOTATOR__", html.escape(annotator))
        .replace("__SEND_TO__", html.escape(send_to))
        .replace("__DATA__", data)
    )


def write_sample(
    out_dir: Path,
    drafts: list[GoldenDraft],
    *,
    n: int,
    seed: int,
    annotators: list[str],
    send_to: str = SEND_TO,
    ia_root: Path | None = None,
) -> dict[str, Any]:
    """Draw the sample and write the labeling pages, the cards and the private key."""
    if n < 3:
        raise ReferenceCheckError("--n must be at least 3")
    if not annotators or len(set(annotators)) != len(annotators):
        raise ReferenceCheckError("give each annotator a distinct name")
    for name in annotators:
        if not name.replace("_", "").replace("-", "").isalnum():
            raise ReferenceCheckError(f"annotator name {name!r}: letters, digits, - and _ only")
    if (out_dir / KEY_FILE).exists():
        raise ReferenceCheckError(f"{out_dir / KEY_FILE} exists; pick a new --out directory")
    pop = population(drafts)
    if not pop:
        raise ReferenceCheckError("no tool-decided item in the drafts")
    sample = stratified_sample(pop, n, seed)
    random.Random(seed).shuffle(sample)
    width = len(str(len(sample)))
    ids = [f"r{i + 1:0{width}d}" for i in range(len(sample))]
    keyed = dict(zip(ids, sample, strict=True))
    created = dt.date.today().isoformat()
    digest = hashlib.sha1(json.dumps([asdict(i) for i in sample]).encode()).hexdigest()[:8]
    sample_id = f"{created}-{digest}"
    by_case = {d.case_id: d for d in drafts}
    in_sample: dict[str, set[str]] = defaultdict(set)
    for it in sample:
        in_sample[it.case_id].add(it.item_id)
    ia_texts = {}
    for fixture in sorted({d.fixture for d in drafts}):
        cached = ia_sources.load_cached_ia(fixture, ia_root)
        ia_texts[fixture] = cached.full_text if cached else None
    out_dir.mkdir(parents=True, exist_ok=True)
    cards = {i: card(i, keyed[i], by_case, ia_texts, in_sample[keyed[i].case_id]) for i in ids}
    for name in annotators:
        order = list(ids)
        random.Random(f"{seed}:{name}").shuffle(order)
        page = render_page(name, [cards[i] for i in order], sample=sample_id, send_to=send_to)
        (out_dir / f"label_{name}.html").write_text(page, encoding="utf-8")
    shares = {s: sum(i.stratum == s for i in pop) / len(pop) for s in STRATA}
    key = {
        "sample": sample_id,
        "seed": seed,
        "n_requested": n,
        "created_on": created,
        "cases": sorted(by_case),
        "annotators": annotators,
        "send_to": send_to,
        "population": {"items": len(pop), "shares": shares},
        "items": {iid: asdict(it) for iid, it in keyed.items()},
    }
    (out_dir / KEY_FILE).write_text(json.dumps(key, indent=2), encoding="utf-8")
    # Blind, like the pages; kept so ``score`` can build the reconcile page after the drafts
    # are published (publishing deletes them).
    (out_dir / CARDS_FILE).write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    (out_dir / README_FILE).write_text(_readme(annotators), encoding="utf-8")
    return {
        "out": str(out_dir),
        "sample": sample_id,
        "items": len(sample),
        "by_stratum": {s: sum(i.stratum == s for i in sample) for s in STRATA},
        "cases": len({i.case_id for i in sample}),
        "population": len(pop),
        "missing_ia": sorted(f for f, t in ia_texts.items() if t is None),
    }


def _readme(annotators: list[str]) -> str:
    files = "\n".join(f"   - {a}: `label_{a}.html`" for a in annotators)
    return f"""# Reference-answer check sample

1. **Label alone (about 10 minutes each).** Send each annotator their own page, and nothing
   else. They open it in a browser (not a private window), answer one item per screen and send
   back the downloaded `answers_<name>.yaml`. No talking about items until everyone is done.
{files}
2. **Score.** Put the answer files in this directory and run
   `uv run python scripts/reference_check.py score --dir <this dir>`.
3. **Decide the splits together (a few minutes).** If any item is not settled, `score` writes
   `{RECONCILE_PAGE}`: the annotators open it together, pick one label per item and send back
   `{RECONCILED_FILE}`; put it here and score again.

`{KEY_FILE}` holds the tool's decisions: keep it here and never send it to an annotator.
`{CARDS_FILE}` is what the pages show; `score` builds the reconcile page from it.
"""


# ----------------------------------------------------------------------------- scoring

Labels = dict[str, dict[str, str | None]]
Notes = dict[str, dict[str, str]]


def _read_file(
    path: Path, key: dict[str, Any], *, complete: bool
) -> tuple[str, dict[str, str | None], dict[str, str]]:
    """(annotator, item id -> label or None when blank, item id -> note) from one answer file."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ReferenceCheckError(f"{path.name}: {exc}") from None
    if (sample := data.get("sample")) is not None and str(sample) != key["sample"]:
        raise ReferenceCheckError(f"{path.name} belongs to sample {sample}, not {key['sample']}")
    item_ids = set(key["items"])
    name = str(data.get("annotator") or path.stem.removeprefix("answers_"))
    labels: dict[str, str | None] = {}
    notes: dict[str, str] = {}
    for row in data.get("answers") or []:
        iid = str(row.get("item"))
        label = row.get("label")
        label = label.strip().lower() if isinstance(label, str) and label.strip() else None
        if iid not in item_ids:
            raise ReferenceCheckError(f"{path.name}: unknown item {iid}")
        if iid in labels:
            raise ReferenceCheckError(f"{path.name}: item {iid} is answered twice")
        if label is not None and label not in LABELS:
            raise ReferenceCheckError(f"{path.name}: {iid} has label {label!r}; use one of "
                                      f"{', '.join(LABELS)}")  # fmt: skip
        labels[iid] = label
        if note := str(row.get("note") or "").strip():
            notes[iid] = note
    if complete:
        labels |= {iid: None for iid in item_ids - labels.keys()}
    return name, labels, notes


def read_answers(directory: Path, key: dict[str, Any]) -> tuple[Labels, Notes]:
    """Annotator -> item id -> label (None when blank), and annotator -> item id -> note, from
    one ``answers_<name>.yaml`` per annotator of the sample."""
    labels: Labels = {}
    notes: Notes = {}
    for path in sorted(directory.glob("answers_*.yaml")):
        name, file_labels, file_notes = _read_file(path, key, complete=True)
        if name in labels:
            raise ReferenceCheckError(f"two answer files for annotator {name}")
        labels[name], notes[name] = file_labels, file_notes
    expected = set(key["annotators"])
    if missing := sorted(expected - labels.keys()):
        raise ReferenceCheckError(f"no answers yet from {', '.join(missing)}")
    if extra := sorted(labels.keys() - expected):
        raise ReferenceCheckError(f"answers from {', '.join(extra)}, who is not an annotator "
                                  "of this sample")  # fmt: skip
    return labels, notes


def read_reconciled(directory: Path, key: dict[str, Any]) -> dict[str, str]:
    """Item id -> the label the annotators agreed on together (``reconciled.yaml``, if any)."""
    path = directory / RECONCILED_FILE
    if not path.exists():
        return {}
    _, labels, _ = _read_file(path, key, complete=False)
    return {iid: label for iid, label in labels.items() if label}


def _settled(votes: list[str | None]) -> bool | None:
    """True / False when more than half of the annotators say keep / drop, else None."""
    for label, kept in (("keep", True), ("drop", False)):
        if votes.count(label) * 2 > len(votes):
            return kept
    return None


def write_reconcile_page(
    directory: Path, key: dict[str, Any], split: list[str], answers: Labels, notes: Notes
) -> Path:
    """``reconcile.html``: the split items with each annotator's label and note, never the
    tool's decision. It lists every split item, reconciled before or not, because its download
    replaces ``reconciled.yaml``."""
    path = directory / CARDS_FILE
    if not path.exists():
        raise ReferenceCheckError(f"no {CARDS_FILE} in {directory}: draw the sample again")
    cards = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for iid in split:
        votes = [{"annotator": a, "label": answers[a][iid] or "", "note": notes[a].get(iid, "")}
                 for a in sorted(answers)]  # fmt: skip
        out.append(cards[iid] | {"votes": votes})
    send_to = key.get("send_to", SEND_TO)
    page = directory / RECONCILE_PAGE
    page.write_text(
        render_page("together", out, sample=key["sample"], send_to=send_to, reconcile=True),
        encoding="utf-8",
    )
    return page


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _kappa(a: list[str], b: list[str]) -> float | None:
    """Cohen's kappa of two keep/drop label lists; None when chance agreement is total."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    chance = sum((a.count(lab) / n) * (b.count(lab) / n) for lab in ("keep", "drop"))
    return None if chance == 1 else (observed - chance) / (1 - chance)


def upper_bound(errors: int, n: int) -> float:
    """One-sided 95% Wilson upper bound of an error rate of ``errors`` in ``n``."""
    p, z2 = errors / n, Z_95**2
    centre = p + z2 / (2 * n)
    spread = Z_95 * (p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5
    return min(1.0, (centre + spread) / (1 + z2 / n))


def score(key: dict[str, Any], answers: Labels, reconciled: dict[str, str] | None = None) -> dict:
    """The tool's error rate against the human label (see the module docstring)."""
    items = key["items"]
    reconciled = reconciled or {}
    human: dict[str, bool] = {}
    split = []
    for iid in items:
        label = _settled([answers[a][iid] for a in answers])
        if label is None:
            split.append(iid)
            if reconciled.get(iid) in ("keep", "drop"):
                label = reconciled[iid] == "keep"
        if label is not None:
            human[iid] = label
    unsettled = [i for i in split if i not in human]
    wrong = sorted(i for i in human if human[i] != bool(items[i]["tool_kept"]))
    # The gate counts an unsettled item as an error: the hard items must not drop out.
    errors = set(wrong) | set(unsettled)
    raw = len(errors) / len(items)
    by_stratum = {}
    for s in STRATA:
        ids = [i for i in items if items[i]["stratum"] == s]
        by_stratum[s] = sum(i in errors for i in ids) / len(ids) if ids else None
    shares = key["population"]["shares"]
    natural = sum(shares[s] * (by_stratum[s] or 0.0) for s in STRATA)
    names = sorted(answers)
    inter, kappas = [], []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            both = [iid for iid in items if answers[a][iid] in ("keep", "drop")
                    and answers[b][iid] in ("keep", "drop")]  # fmt: skip
            if both:
                la, lb = [answers[a][x] for x in both], [answers[b][x] for x in both]
                inter.append(sum(x == y for x, y in zip(la, lb, strict=True)) / len(both))
                if (k := _kappa(la, lb)) is not None:
                    kappas.append(k)
    error = max(raw, natural)
    blank = {a: sum(v is None for v in answers[a].values()) for a in names}
    return {
        "items_sampled": len(items),
        "settled": len(human),
        "split": split,
        "reconciled": len(split) - len(unsettled),
        "unsettled": unsettled,
        "blank": blank,
        "error_raw": raw,
        "error_natural": natural,
        "error_by_stratum": by_stratum,
        "error": error,
        "upper_bound_95": upper_bound(len(errors), len(items)),
        "robust": error <= MAX_ERROR and not any(blank.values()),
        "inter_annotator_agreement": _mean(inter),
        "cohen_kappa": _mean(kappas),
        "wrong": wrong,
    }


def format_result(result: dict[str, Any], key: dict[str, Any]) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.1%}"

    s = result["error_by_stratum"]
    kappa = result["cohen_kappa"]
    lines = [
        f"reference-answer check: {result['items_sampled']} items, {result['settled']} settled "
        f"({result['reconciled']} of them together), {len(result['unsettled'])} not yet",
        f"agreement before reconciling {pct(result['inter_annotator_agreement'])}"
        + ("" if kappa is None else f" (Cohen's kappa {kappa:.2f})"),
        "tool error vs the human label (unsettled items count as errors): raw "
        + pct(result["error_raw"]) + "; " + ", ".join(f"{k} {pct(v)}" for k, v in s.items()),
        f"natural-rate error {pct(result['error_natural'])}; gate error (the higher) "
        f"{pct(result['error'])} against a {MAX_ERROR:.0%} limit; 95% upper bound "
        f"{pct(result['upper_bound_95'])}",
    ]  # fmt: skip
    if result["unsettled"]:
        # Listing the tool's mistakes now could sway the joint decision: wait until it is done.
        lines.append(f"not settled yet: {', '.join(result['unsettled'])}")
    else:
        for iid in result["wrong"]:
            it = key["items"][iid]
            lines.append(f"  wrong: {iid} = {it['case_id']} {it['item_id']} ({it['stratum']})")
    if blanks := {a: n for a, n in result["blank"].items() if n}:
        lines.append(f"blank labels: {blanks}")
    if any(result["blank"].values()):
        lines.append("INCOMPLETE: finish the answers, then score again")
    elif result["unsettled"]:
        lines.append(f"NOT SETTLED YET: decide the split items together ({RECONCILE_PAGE}), "
                     "then score again")  # fmt: skip
    elif result["robust"]:
        lines.append("ROBUST: no gross failure; spot checks stay optional")
    else:
        lines.append(
            "NOT ROBUST: fix the drafting or tie-break prompts, re-draft, then check again"
        )
    return "\n".join(lines)
