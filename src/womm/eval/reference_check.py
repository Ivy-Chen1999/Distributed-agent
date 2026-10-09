"""One-time robustness check of the reference answers the drafting tool settles without people.

Since 2026-10-08 nobody has to review a train/val golden draft: the three judges accept what they
agree on and the tie-break judge (``womm.eval.tiebreak``) rules on the rest. Anthropic's guidance
is that human review may become occasional *once the system is robust* ("Demystifying evals for
AI agents"), so this check measures that once, on a small blind sample, before the spot checks
become optional for good. ``scripts/reference_check.py sample`` draws the items and writes the
labeling material; ``scripts/reference_check.py score`` compares the labels with the tool.

**Population.** Every tool-decided item of the train/val drafts given: kept by the three judges
(``auto_accepted``), kept by the tie-break (``llm_kept``) or dropped by it (``llm_dropped``).
Items a person decided and items dropped by a deterministic check (anchor not found, unknown
provision keys) are left out: there is no judgement to check. Holdout drafts are refused.

**Strata.** Three, by who decided what: ``judges_accepted``, ``tiebreak_kept`` and
``tiebreak_dropped``, about a third of the sample each (the others fill in when one runs short),
round-robin over cases within a stratum so no case dominates.

**Blind labeling.** The sheet shows each item's claim, its quote and the IA text around it, and
the case's other kept items (for duplicates); never the tool's decision, the judges' verdicts or
the tie-break's reason. Those are kept in ``key.private.json`` next to the sheets.

**Labels.** ``keep``, ``drop`` or ``unsure``. ``unsure`` votes are left out of the majority; an
item with no majority (all unsure, or a tie) is excluded and counted.

**Gate.** The tool's error rate against the human majority, raw and reweighted to the strata's
shares of the population; the gate uses the higher of the two. At or below ``MAX_ERROR`` (the
same 10% that escalates a proposal to full review) the reference answers count as robust and
later spot checks stay optional; above it, fix the prompts, re-draft and check again.
"""

from __future__ import annotations

import datetime as dt
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
from womm.eval.drafting import DraftCandidate, GoldenDraft, _DraftItem, anchor_context
from womm.eval.golden_review import ESCALATION_RATE, KEPT_DECISIONS

LABELS = ("keep", "drop", "unsure")
STRATA = ("judges_accepted", "tiebreak_kept", "tiebreak_dropped")
MAX_ERROR = ESCALATION_RATE
PAGE_TEMPLATE = Path(__file__).with_name("reference_check_page.html")
KEY_FILE = "key.private.json"
README_FILE = "README.md"
CONTEXT_CHARS = 600
# ``why_missing`` is the recall step's own argument for a candidate: not shown, to stay blind.
CLAIM_KEYS = ("affected_actor", "mechanism", "impact", "description")

RUBRIC = """\
**The question.** Should this item be part of the reference answer for this case? The reference
answer lists what the official impact assessment (IA) says these provisions do, to whom and
through which mechanism; WOMM is scored on how much of it it finds from the legal text alone.

- **keep**: the quoted IA text supports the claim, the claim is an effect of these provisions on
  this actor through this mechanism, and it does not repeat one of the case's other items.
- **drop**: the quote does not support it, it is not an effect of these provisions (background,
  a policy option that was not chosen, a general statement), or it repeats another item.
- **unsure**: you cannot decide after reading carefully. Say why in `note`.

Ignore the wording and the category; decide only whether the item belongs."""


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
        return "tiebreak_dropped" if item.tiebreak.model != "deterministic" else None
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


def _claim(item: _DraftItem) -> list[str]:
    return [f"- {k.replace('_', ' ')}: {getattr(item, k)}" for k in CLAIM_KEYS
            if hasattr(item, k)]  # fmt: skip


def _others(draft: GoldenDraft, item_id: str) -> list[str]:
    out = []
    for other in draft.items():
        if other.item_id == item_id or other.review.decision not in KEPT_DECISIONS:
            continue
        actor = getattr(other, "affected_actor", "")
        what = getattr(other, "mechanism", None) or getattr(other, "description", "")
        out.append(f"- {actor}: {what}".replace("- : ", "- "))
    return out or ["_none_"]


def render_sheet(
    annotator: str, ordered: list[tuple[str, Item]], drafts: dict[str, GoldenDraft],
    ia_texts: dict[str, str | None],
) -> str:  # fmt: skip
    """A self-contained Markdown sheet. It never shows the tool's decision or reasons."""
    lines = [
        f"# Reference-answer check: labeling sheet for {annotator}",
        "",
        f"{len(ordered)} items, each from the reference answer draft of one golden case. Decide "
        f"for each whether it belongs in the reference answer, and write your label in "
        f"`answers_{annotator}.yaml` next to this file. Work alone; do not discuss items with "
        "the other annotator until both of you have finished.",
        "",
        RUBRIC,
        "",
    ]
    for item_id, it in ordered:
        draft = drafts[it.case_id]
        item = next(i for i in draft.items() if i.item_id == it.item_id)
        text = ia_texts.get(draft.fixture)
        context = (
            anchor_context(item.ia_anchor, text, CONTEXT_CHARS)
            if text and item.anchor.against == "ia"
            else None
        )
        source = "the IA" if item.anchor.against == "ia" else "the scrutiny board opinion"
        lines += [
            "---", "", f"## {item_id}", "",
            f"Case `{draft.case_id}` ({draft.fixture}), scenario provisions: "
            f"{', '.join(item.provision_keys)}.", "",
            "**Claim**", "", *_claim(item), "",
            f"**Quote from {source}** (section: {item.ia_section})", "", f"> {item.ia_anchor}", "",
            *(["**IA text around the quote**", "", f"> {context}", ""] if context else []),
            "**The case's other reference items**", "", *_others(draft, it.item_id), "",
            f"Label for `{item_id}`: keep / drop / unsure", "",
        ]  # fmt: skip
    return "\n".join(lines)


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
    item_id: str, it: Item, drafts: dict[str, GoldenDraft], ia_texts: dict[str, str | None]
) -> dict[str, Any]:
    """What the labeling page shows for one item: never the tool's decision or reasons."""
    draft = drafts[it.case_id]
    item = next(i for i in draft.items() if i.item_id == it.item_id)
    text = ia_texts.get(draft.fixture)
    return {
        "id": item_id,
        "case": draft.case_id,
        "fixture": draft.fixture,
        "provisions": item.provision_keys,
        "claim": {k: getattr(item, k) for k in CLAIM_KEYS if hasattr(item, k)},
        "source": "IA" if item.anchor.against == "ia" else "Scrutiny board opinion",
        "section": item.ia_section,
        "quote": item.ia_anchor,
        "passage": _passage(item.ia_anchor, text, CONTEXT_CHARS)
        if text and item.anchor.against == "ia"
        else None,
        "others": [o.removeprefix("- ") for o in _others(draft, it.item_id) if o != "_none_"],
    }


def render_page(annotator: str, cards: list[dict[str, Any]]) -> str:
    """A self-contained HTML labeling page: one item per screen, answers kept in the browser and
    downloaded as ``answers_<annotator>.yaml``."""
    data = json.dumps({"annotator": annotator, "items": cards}, ensure_ascii=False)
    data = data.replace("</", "<\\/")
    page = PAGE_TEMPLATE.read_text(encoding="utf-8")
    return page.replace("__ANNOTATOR__", html.escape(annotator)).replace("__DATA__", data)


def render_answers(annotator: str, item_ids: list[str]) -> str:
    rows = [
        f"# Answers of {annotator} for the reference-answer check (sheet_{annotator}.md).",
        "# For every item set label to keep, drop or unsure; note is optional (say why for",
        "# unsure or drop). Do not add or remove rows.",
        f"annotator: {annotator}",
        "answers:",
    ]
    for iid in item_ids:
        rows += [f"- item: {iid}", "  label:", "  note: ''"]
    return "\n".join(rows) + "\n"


def write_sample(
    out_dir: Path,
    drafts: list[GoldenDraft],
    *,
    n: int,
    seed: int,
    annotators: list[str],
    ia_root: Path | None = None,
) -> dict[str, Any]:
    """Draw the sample and write the sheets, answer files and private key into ``out_dir``."""
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
    by_case = {d.case_id: d for d in drafts}
    ia_texts = {}
    for fixture in sorted({d.fixture for d in drafts}):
        cached = ia_sources.load_cached_ia(fixture, ia_root)
        ia_texts[fixture] = cached.full_text if cached else None
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in annotators:
        order = list(ids)
        random.Random(f"{seed}:{name}").shuffle(order)
        (out_dir / f"sheet_{name}.md").write_text(
            render_sheet(name, [(i, keyed[i]) for i in order], by_case, ia_texts),
            encoding="utf-8",
        )
        (out_dir / f"answers_{name}.yaml").write_text(render_answers(name, ids), encoding="utf-8")
        cards = [card(i, keyed[i], by_case, ia_texts) for i in order]
        (out_dir / f"label_{name}.html").write_text(render_page(name, cards), encoding="utf-8")
    shares = {s: sum(i.stratum == s for i in pop) / len(pop) for s in STRATA}
    key = {
        "seed": seed,
        "n_requested": n,
        "created_on": dt.date.today().isoformat(),
        "cases": sorted(by_case),
        "annotators": annotators,
        "population": {"items": len(pop), "shares": shares},
        "items": {iid: asdict(it) for iid, it in keyed.items()},
    }
    (out_dir / KEY_FILE).write_text(json.dumps(key, indent=2), encoding="utf-8")
    (out_dir / README_FILE).write_text(_readme(annotators), encoding="utf-8")
    return {
        "out": str(out_dir),
        "items": len(sample),
        "by_stratum": {s: sum(i.stratum == s for i in sample) for s in STRATA},
        "cases": len({i.case_id for i in sample}),
        "population": len(pop),
        "missing_ia": sorted(f for f, t in ia_texts.items() if t is None),
    }


def _readme(annotators: list[str]) -> str:
    files = "\n".join(
        f"- {a}: `label_{a}.html` (or `sheet_{a}.md` and `answers_{a}.yaml`)" for a in annotators
    )
    return f"""# Reference-answer check sample

Send each annotator their files, and nothing else. `label_<name>.html` is the easy way: open it
in a browser, answer one item per screen, then download `answers_<name>.yaml` and send it back.
The Markdown sheet and the empty answer file are the fallback.

{files}

`{KEY_FILE}` holds the tool's decisions: keep it here and never send it to an annotator.
When the answer files are back in this directory, run
`uv run python scripts/reference_check.py score --dir <this dir>`.
"""


# ----------------------------------------------------------------------------- scoring


def read_answers(directory: Path, item_ids: set[str]) -> dict[str, dict[str, str | None]]:
    """Annotator -> item id -> label (None when left blank) from every ``answers_*.yaml``."""
    out: dict[str, dict[str, str | None]] = {}
    for path in sorted(directory.glob("answers_*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ReferenceCheckError(f"{path.name}: {exc}") from None
        name = data.get("annotator") or path.stem.removeprefix("answers_")
        labels: dict[str, str | None] = {}
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
        if missing := sorted(item_ids - labels.keys()):
            raise ReferenceCheckError(f"{path.name}: rows removed for {', '.join(missing)}")
        if name in out:
            raise ReferenceCheckError(f"two answer files for annotator {name}")
        out[name] = labels
    if not out:
        raise ReferenceCheckError(f"no answers_*.yaml in {directory}")
    return out


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def score(key: dict[str, Any], answers: dict[str, dict[str, str | None]]) -> dict[str, Any]:
    """The tool's error rate against the human majority (see the module docstring)."""
    items = key["items"]
    tool = {iid: bool(it["tool_kept"]) for iid, it in items.items()}
    majority: dict[str, bool] = {}
    for iid in items:
        votes = [answers[a][iid] for a in answers]
        keep, drop = votes.count("keep"), votes.count("drop")
        if keep != drop:
            majority[iid] = keep > drop
    wrong = {iid: majority[iid] != tool[iid] for iid in majority}
    raw = _mean([float(v) for v in wrong.values()])
    by_stratum = {
        s: _mean([float(wrong[i]) for i in wrong if items[i]["stratum"] == s]) for s in STRATA
    }
    shares = key["population"]["shares"]
    natural = None
    if raw is not None and all(by_stratum[s] is not None or not shares[s] for s in STRATA):
        natural = sum(shares[s] * (by_stratum[s] or 0.0) for s in STRATA)
    names = sorted(answers)
    inter = []
    for i, a in enumerate(names):
        for b in names[i + 1 :]:
            both = [iid for iid in items if answers[a][iid] in ("keep", "drop")
                    and answers[b][iid] in ("keep", "drop")]  # fmt: skip
            if both:
                inter.append(sum(answers[a][x] == answers[b][x] for x in both) / len(both))
    error = max((v for v in (raw, natural) if v is not None), default=None)
    blank = {a: sum(v is None for v in answers[a].values()) for a in names}
    return {
        "items_sampled": len(items),
        "items_resolved": len(majority),
        "excluded": len(items) - len(majority),
        "blank": blank,
        "error_raw": raw,
        "error_natural": natural,
        "error_by_stratum": by_stratum,
        "error": error,
        "robust": error is not None and error <= MAX_ERROR and not any(blank.values()),
        "inter_annotator_agreement": _mean(inter),
        "wrong": sorted(i for i, w in wrong.items() if w),
    }


def format_result(result: dict[str, Any], key: dict[str, Any]) -> str:
    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.1%}"

    s = result["error_by_stratum"]
    lines = [
        f"reference-answer check: {result['items_sampled']} items sampled, "
        f"{result['items_resolved']} with a majority label, {result['excluded']} excluded",
        "tool error vs majority: raw " + pct(result["error_raw"]) + "; "
        + ", ".join(f"{k} {pct(v)}" for k, v in s.items()),
        f"natural-rate error {pct(result['error_natural'])}; gate error (the higher) "
        f"{pct(result['error'])} against a {MAX_ERROR:.0%} limit",
        f"inter-annotator agreement {pct(result['inter_annotator_agreement'])}",
    ]  # fmt: skip
    for iid in result["wrong"]:
        it = key["items"][iid]
        lines.append(f"  wrong: {iid} = {it['case_id']} {it['item_id']} ({it['stratum']})")
    if blanks := {a: n for a, n in result["blank"].items() if n}:
        lines.append(f"blank labels: {blanks}; finish the sheets before trusting the result")
    lines.append(
        "ROBUST: spot checks stay optional"
        if result["robust"]
        else "NOT ROBUST: fix the drafting or tie-break prompts, re-draft, then check again"
    )
    return "\n".join(lines)
