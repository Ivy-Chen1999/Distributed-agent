"""One-time robustness check of the reference answers the drafting tool settles without people.

Nobody reviews a train/val golden draft: the three judges accept what they agree on and the
tie-break judge (``womm.eval.tiebreak``) rules on the rest. Those judges are LLM graders, and
Anthropic's guidance is to calibrate such graders against people, after which human review can
be occasional ("Demystifying evals for AI agents"). This check does that once, before the drafts
are published: ``scripts/reference_check.py sample`` draws the items and writes one labeling page
per annotator; ``upload`` hosts them on the app (``womm.api.labels``), where answers are saved as
people give them, and ``fetch`` brings the answers back; ``scripts/reference_check.py score``
compares the labels with the tool.

**Population.** Every tool-decided item of the train/val drafts given: kept by the three judges
(``auto_accepted``), kept by the tie-break (``llm_kept``) or dropped by it (``llm_dropped``).
Items a person decided and items dropped by a deterministic check are left out: there is no
judgement to check. Holdout drafts are refused.

**Sample.** ``n`` items per annotator (20 by default), each annotator their own items, about a
third from each stratum (``judges_accepted``, ``tiebreak_kept``, ``tiebreak_dropped``; the others
fill in when one runs short), round-robin over cases so no case dominates. A reserve of
``extra`` more per annotator (10 by default), drawn the same way from the rest, is kept back.

**Blind labeling.** The page shows each item's claim, its quote with the source text around it,
and the text of the articles the claim names. It never shows the tool's decision or reasons;
those stay in ``key.private.json``. Spotting repeats is left to the tie-break.

**Gate, in two looks.** Each item gets ``keep``, ``drop`` or ``unsure``. The tool's error rate is
the share of items where the person's keep/drop differs from the tool, with ``unsure`` counted as
an error so the hard items do not drop out. Over all items the reference answers count as robust
at or below ``MAX_ERROR`` (the same 10% that escalates a proposal to full review). The first round
decides alone when more items could not change the outcome's strength: it passes when its 95%
upper bound is no wider than that of the worst pass the full check allows (0 to 3 errors in 40),
and fails when the errors would stay above the limit even if every reserve item agreed (7 or more
in 40 against 60). Only in between does the reserve go out (``extend``). Above the limit, fix the
prompts, re-draft and check again. Every item must be answered before any verdict. Each wrong item
is printed with the tool's own reason, and ``score`` writes ``review.html`` (local only: it holds
the tool's decisions) to read them side by side. The one-sided 95% upper bound printed next to the
rate says how far the true rate could be; a pass whose bound is above ``BORDERLINE_BOUND`` is
called borderline (context only, never the verdict).

**Shared items (optional, ``overlap``).** A few first-round items are also given to every other
annotator, under their own ids and mixed into their pages. Only the first copy counts in the gate;
the copies measure how often the people agree with each other, which says how far a disagreement
with the tool can be blamed on the tool.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import math
import random
import re
import unicodedata
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from womm.citations import normalize
from womm.data.fixtures import fixture_dir, load_fixture
from womm.eval import hosted_labels, ia_sources
from womm.eval.drafting import DraftCandidate, GoldenDraft, _DraftItem
from womm.eval.golden_review import ESCALATION_RATE, KEPT_DECISIONS

LABELS = ("keep", "drop", "unsure")
STRATA = ("judges_accepted", "tiebreak_kept", "tiebreak_dropped")
# Where the tool's own judges disagreed (the tie-break ruled). A disputed-only census labels
# these alone: the judges-accepted stratum is calibrated by the random first check.
DISPUTED = ("tiebreak_kept", "tiebreak_dropped")
# Decisions people are not asked to judge, because the page asks only whether the AI read its
# source faithfully: a drop for repeating a kept item (checked by code against the item it
# names), for not following from the articles, or for a kind the rules exclude.
OUT_OF_SCOPE = ("duplicate", "law", "rule")
MAX_ERROR = ESCALATION_RATE
PAGE_TEMPLATE = Path(__file__).with_name("labelling_page.html")
KEY_FILE = "key.private.json"
TASKS_FILE = hosted_labels.PAGES_FILE
KIND = "reference_check"
README_FILE = "README.md"
# One-sided 95% for the upper bound printed next to the error rate.
Z_95 = 1.645
# A pass whose 95% upper bound is above this is called borderline (1.5 times the limit).
BORDERLINE_BOUND = 1.5 * MAX_ERROR
SEND_TO = "the person who sent you this page"
CONTEXT_CHARS = 600
ARTICLE_CHARS = 4000  # an article's text on a card, cut beyond this
# ``why_missing`` is the recall step's own argument for a candidate: not shown, to stay blind.
CLAIM_KEYS = ("affected_actor", "mechanism", "impact", "description")
NAME_OK = re.compile(r"[A-Za-z0-9_-]{1,64}")


class ReferenceCheckError(ValueError):
    """Bad input for a check step: nothing was written."""


@dataclass(frozen=True)
class Item:
    case_id: str
    fixture: str
    item_id: str
    stratum: str
    tool_kept: bool
    tool_reason: str = ""
    annotator: str = ""
    stage: int = 1
    overlap_of: str = ""
    check: str = "reference"


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


def _tool_reason(item: _DraftItem) -> str:
    if item.tiebreak is not None:
        return f"tie-break: {item.tiebreak.reason}"
    j = item.judge
    return (f"judges: anchor: {j.anchor_faithfulness.reason} / derivability: "
            f"{j.derivability.reason} / category: {j.category.reason}")  # fmt: skip


def population(drafts: list[GoldenDraft]) -> list[Item]:
    items = []
    for draft in drafts:
        if draft.split == "holdout":
            raise ReferenceCheckError(f"{draft.case_id}: a holdout draft is verified by its owner")
        for item in draft.items():
            if (s := stratum(item)) is not None:
                kept = item.review.decision in KEPT_DECISIONS
                items.append(Item(draft.case_id, draft.fixture, item.item_id, s, kept,
                                  _tool_reason(item)))  # fmt: skip
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


@cache
def _fixture(name: str):
    return load_fixture(fixture_dir(name))


def _articles(draft: GoldenDraft, keys: list[str]) -> list[dict[str, str]]:
    """The text of the articles ``keys`` name, in the version the case's scenario assesses; none
    when the scenario is not in the public fixture (a holdout case)."""
    try:
        fixture = _fixture(draft.fixture)
        version = fixture.version(fixture.scenario(draft.scenario_id).after_version)
    except (KeyError, ValueError, OSError):
        return []
    wanted = set(keys)
    out = []
    for p in version.provisions:
        if p.provision_key in wanted:
            text = p.text if len(p.text) <= ARTICLE_CHARS else p.text[:ARTICLE_CHARS] + " ..."
            out.append({"label": f"Article {p.article}", "text": text})
    return out


def card(
    item_id: str,
    it: Item,
    drafts: dict[str, GoldenDraft],
    ia_texts: dict[str, str | None],
    rsb_texts: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    """What the labeling page shows for one item: never the tool's decision or reasons.
    An item anchored on the RSB opinion shows the opinion's text around its quote, from
    ``rsb_texts``; the articles the item names are shown in their own tabs."""
    draft = drafts[it.case_id]
    item = next(i for i in draft.items() if i.item_id == it.item_id)
    on_ia = item.anchor.against == "ia"
    text = (ia_texts if on_ia else (rsb_texts or {})).get(draft.fixture)
    return {
        "id": item_id,
        "case": draft.case_id,
        "fixture": draft.fixture,
        "provisions": item.provision_keys,
        "claim": {k: getattr(item, k) for k in CLAIM_KEYS if hasattr(item, k)},
        "source": "report" if on_ia else "scrutiny board opinion",
        "section": item.ia_section,
        "quote": item.ia_anchor,
        "passage": _passage(item.ia_anchor, text, CONTEXT_CHARS) if text else None,
        "articles": _articles(draft, item.provision_keys),
    }


def render_page(
    annotator: str, cards: list[dict[str, Any]], *, sample: str, send_to: str = SEND_TO
) -> str:
    """A self-contained HTML labeling page: one item per screen, answers kept in the browser and
    downloaded as ``answers_<annotator>.yaml``."""
    meta = {"kind": KIND, "annotator": annotator, "sample": sample, "sendTo": send_to,
            "file": f"answers_{annotator}.yaml"}  # fmt: skip
    return _page(meta | {"items": cards}, annotator, send_to)


def render_hosted_page(sample: str, *, api: str, kind: str = KIND, title: str | None = None) -> str:
    """The page as the server hosts it (``womm.api.labels``) for either task: the annotator
    signs in with their name, the cards come from ``api`` and each answer is saved there.
    ``title`` replaces the task's own title (the local holdout review)."""
    data = {"kind": kind, "annotator": "", "sample": sample, "api": api, "items": []}
    if title:
        data["title"] = title
    return _page(data, "", SEND_TO)


def _page(data: dict[str, Any], annotator: str, send_to: str) -> str:
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = PAGE_TEMPLATE.read_text(encoding="utf-8")
    return (
        page.replace("__ANNOTATOR__", html.escape(annotator))
        .replace("__SEND_TO__", html.escape(send_to))
        .replace("__DATA__", blob)
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
    extra: int = 10,
    companions: list[GoldenDraft] | None = None,
    overlap: int = 0,
    disputed_only: bool = False,
    with_companions: list[GoldenDraft] | None = None,
    out_of_scope: dict[str, dict] | None = None,
) -> dict[str, Any]:
    """Draw ``n`` items per annotator, each annotator their own, plus a reserve of ``extra``
    each, and write the labeling pages and the private key. With ``companions`` (RSB companion
    drafts, plan 2026-10-12-001) the items are drawn from the companions only, and each case is
    shown with its companion's omissions next to its own; ``drafts`` are then their cases.
    ``overlap`` first-round items are also given to every other annotator (shared items,
    outside the gate). ``disputed_only`` keeps the items the tie-break ruled on (``DISPUTED``).
    ``with_companions`` puts the companion check on the same pages: the case sample is drawn
    exactly as without it, and the companions' disputed items are added (``check:
    "companion"``), split between the annotators and scored on their own (``part``).
    ``out_of_scope`` ("<case>/<item>" -> ``{"basis": ...}``) names decisions people are not asked
    to judge (``OUT_OF_SCOPE``): they are drawn as usual, then taken out and listed in the key."""
    if n < 3:
        raise ReferenceCheckError("--n must be at least 3")
    if not annotators or len(set(annotators)) != len(annotators):
        raise ReferenceCheckError("give each annotator a distinct name")
    for name in annotators:
        if not NAME_OK.fullmatch(name):
            raise ReferenceCheckError(f"annotator name {name!r}: letters, digits, - and _ only")
    if (out_dir / KEY_FILE).exists():
        raise ReferenceCheckError(f"{out_dir / KEY_FILE} exists; pick a new --out directory")
    if companions is not None and with_companions is not None:
        raise ReferenceCheckError("give companions= or with_companions=, not both")
    added: list[Item] = []
    if with_companions is not None:
        if any(d.companion is not None for d in drafts):
            raise ReferenceCheckError("companion drafts go in with_companions=")
        added = [replace(i, check="companion") for i in population(with_companions)
                 if i.stratum in DISPUTED]  # fmt: skip
        companions_shown = with_companions
    if companions is not None:
        companions_shown = companions
    if companions is not None or with_companions is not None:
        by_id = {d.case_id: d for d in drafts if d.companion is None}
        orphans = sorted({c.case_id for c in companions_shown} - set(by_id))
        if orphans or any(c.companion is None for c in companions_shown):
            raise ReferenceCheckError(f"companions need their case drafts: {orphans}")
        pop = population(companions) if companions is not None else population(list(by_id.values()))
        merged = dict(by_id)
        for c in companions_shown:
            case = merged[c.case_id]
            both = [*case.important_omissions, *c.important_omissions]
            merged[c.case_id] = case.model_copy(update={"important_omissions": both})
        drafts = list(merged.values())
    else:
        if any(d.companion is not None for d in drafts):
            raise ReferenceCheckError("companion drafts are checked on their own (companions=)")
        pop = population(drafts)
    if disputed_only:
        pop = [i for i in pop if i.stratum in DISPUTED]
    if not pop:
        raise ReferenceCheckError("no tool-decided item in the drafts")
    if extra < 0:
        raise ReferenceCheckError("--extra must not be negative")
    if overlap < 0 or (overlap and len(annotators) < 2):
        raise ReferenceCheckError(
            "--overlap needs at least two annotators and must not be negative"
        )

    def deal(drawn: list[Item], stage: int) -> list[Item]:
        # Deal in stratum order so each annotator gets about a third of each stratum.
        dealt = [replace(it, annotator=annotators[j % len(annotators)], stage=stage)
                 for j, it in enumerate(drawn)]  # fmt: skip
        random.Random(f"{seed}:{stage}").shuffle(dealt)
        return dealt

    scope = out_of_scope or {}

    def outside(it: Item) -> bool:
        return scope.get(f"{it.case_id}/{it.item_id}", {}).get("basis") in OUT_OF_SCOPE

    first = stratified_sample(pop, n * len(annotators), seed)
    taken = {(i.case_id, i.item_id) for i in first}
    rest = [i for i in pop if (i.case_id, i.item_id) not in taken]
    sample = deal(first, 1)
    reserve = deal(stratified_sample(rest, extra * len(annotators), seed + 1), 2) if extra else []
    # A random sample of every decision, restricted to those people can judge, stays random.
    taken_out = [i for i in sample + reserve + added if outside(i)]
    sample = [i for i in sample if not outside(i)]
    reserve = [i for i in reserve if not outside(i)]
    added = [i for i in added if not outside(i)]
    extra_items = deal(added, 3) if added else []
    extra_items = [replace(i, stage=1) for i in extra_items]
    shared_from = random.Random(f"{seed}:overlap").sample(range(len(sample)),
                                                          min(overlap, len(sample)))  # fmt: skip
    copies = len(shared_from) * (len(annotators) - 1)
    width = len(str(len(sample) + len(reserve) + copies + len(extra_items)))
    ids = [f"r{i + 1:0{width}d}" for i in range(len(sample) + len(reserve))]
    keyed = dict(zip(ids, sample + reserve, strict=True))
    for it in extra_items:  # numbered after the reserve, so the case sample keeps its ids
        rid = f"r{len(keyed) + 1:0{width}d}"
        keyed[rid] = it
    shared: dict[str, Item] = {}
    for j in sorted(shared_from):
        orig_id, orig = ids[j], sample[j]
        for name in annotators:
            if name != orig.annotator:
                rid = f"r{len(keyed) + len(shared) + 1:0{width}d}"
                shared[rid] = replace(orig, annotator=name, overlap_of=orig_id)
    created = dt.date.today().isoformat()
    drawn = [asdict(i) for i in sample + reserve + extra_items]
    drawn += [asdict(i) for i in shared.values()]
    digest = hashlib.sha1(json.dumps(drawn).encode()).hexdigest()[:8]
    sample_id = f"{created}-{digest}"
    by_case = {d.case_id: d for d in drafts}
    ia_texts, rsb_texts = {}, {}
    for fixture in sorted({d.fixture for d in drafts}):
        cached = ia_sources.load_cached_ia(fixture, ia_root)
        ia_texts[fixture] = cached.full_text if cached else None
        rsb_texts[fixture] = cached.rsb_text if cached else None
    out_dir.mkdir(parents=True, exist_ok=True)
    pages, held = {}, {}
    for name in annotators:
        mine = [i for i in ids if keyed[i].annotator == name]
        mine_added = [i for i in keyed if i not in ids and keyed[i].annotator == name]
        mine_shared = [i for i, it in shared.items() if it.annotator == name]
        every = keyed | shared
        cards = {i: card(i, every[i], by_case, ia_texts, rsb_texts)
                 for i in mine + mine_added + mine_shared}  # fmt: skip
        pages[name] = [cards[i] for i in mine if keyed[i].stage == 1]
        rng = random.Random(f"{seed}:companion:{name}")
        for i in mine_added:  # mixed in among the case sample, never first (the guided one)
            pages[name].insert(rng.randint(1, max(1, len(pages[name]))), cards[i])
        rng = random.Random(f"{seed}:overlap:{name}")
        for i in mine_shared:  # mixed in, so nothing marks them
            pages[name].insert(rng.randint(0, len(pages[name])), cards[i])
        held[name] = [cards[i] for i in mine if keyed[i].stage == 2]
        page = render_page(name, pages[name], sample=sample_id, send_to=send_to)
        (out_dir / f"label_{name}.html").write_text(page, encoding="utf-8")
    hosted_labels.write_pages(out_dir, kind=KIND, sample=sample_id, pages=pages, reserve=held)
    key = {
        "sample": sample_id,
        "seed": seed,
        "n_per_annotator": n,
        "extra_per_annotator": extra,
        "extended": False,
        "created_on": created,
        "cases": sorted(by_case),
        "annotators": annotators,
        "population": len(pop),
        "disputed_only": disputed_only,
        "out_of_scope": [asdict(i) | scope[f"{i.case_id}/{i.item_id}"] for i in taken_out],
        "items": {iid: asdict(it) for iid, it in keyed.items() if it.stage == 1},
        "reserve": {iid: asdict(it) for iid, it in keyed.items() if it.stage == 2},
    }
    if shared:
        key["overlap"] = {iid: asdict(it) for iid, it in shared.items()}
    (out_dir / KEY_FILE).write_text(json.dumps(key, indent=2), encoding="utf-8")
    (out_dir / README_FILE).write_text(_readme(annotators), encoding="utf-8")
    return {
        "out": str(out_dir),
        "sample": sample_id,
        "items": len(sample),
        "reserve": len(reserve),
        "per_annotator": {a: sum(i.annotator == a for i in sample) for a in annotators},
        "out_of_scope": len(taken_out),
        "shared": len(shared),
        "companion_items": {a: sum(i.annotator == a for i in extra_items) for a in annotators},
        "by_stratum": {s: sum(i.stratum == s for i in sample) for s in STRATA},
        "cases": len({i.case_id for i in sample}),
        "population": len(pop),
        "missing_ia": sorted(f for f, t in ia_texts.items() if t is None),
    }


def extend(out_dir: Path) -> dict[str, list[dict]]:
    """Move the reserve into the sample: the key, pages.json and the local pages. Returns the
    new cards per annotator, for the hosted pages."""
    key_path = out_dir / KEY_FILE
    key = json.loads(key_path.read_text(encoding="utf-8"))
    if key.get("extended"):
        raise ReferenceCheckError("this sample was already extended")
    if not key.get("reserve"):
        raise ReferenceCheckError("this sample has no reserve to send")
    pages = hosted_labels.read_pages(out_dir)
    added = pages.get("reserve") or {}
    for name, cards in added.items():
        pages["annotators"][name] = pages["annotators"].get(name, []) + cards
        page = render_page(name, pages["annotators"][name], sample=key["sample"])
        (out_dir / f"label_{name}.html").write_text(page, encoding="utf-8")
    hosted_labels.write_pages(out_dir, kind=KIND, sample=key["sample"], pages=pages["annotators"])
    key["items"] |= key.pop("reserve")
    key["reserve"] = {}
    key["extended"] = True
    key_path.write_text(json.dumps(key, indent=2), encoding="utf-8")
    return added


def _readme(annotators: list[str]) -> str:
    files = "\n".join(f"   - {a}: `label_{a}.html`" for a in annotators)
    return f"""# Reference-answer check sample

1. **Host the pages** (the usual way):
   `uv run python scripts/reference_check.py upload --dir <this dir> --url <app url>`. It
   prints one link; send it to the annotators. Each signs in with their name and their answers
   are saved on the server as they go. When everyone is done:
   `uv run python scripts/reference_check.py fetch --dir <this dir> --url <app url>`.

   Without the server, send each annotator their own page instead, and nothing else; they
   send back the downloaded `answers_<name>.yaml`:
{files}
2. **Score.** With an answer file per annotator in this directory, run
   `uv run python scripts/reference_check.py score --dir <this dir>`.

`{KEY_FILE}` holds the tool's decisions: keep it here and never send or upload it.
`{TASKS_FILE}` is what `upload` sends: the pages only.
"""


def write_answer_files(out_dir: Path, sample: str, annotators: dict[str, dict]) -> list[str]:
    """Write the server's answers as one ``answers_<name>.yaml`` per annotator, the format
    ``read_answers`` reads; returns the names whose page is not submitted yet."""
    pending = []
    for name, got in sorted(annotators.items()):
        if not NAME_OK.fullmatch(name):
            raise ReferenceCheckError(f"annotator name {name!r} from the server is not valid")
        if not got.get("submitted_at"):
            pending.append(name)
        rows = [{"item": a["item"], "label": a["label"], "note": a.get("note", "")}
                for a in got.get("answers", [])]  # fmt: skip
        data = {"sample": sample, "annotator": name, "answers": rows}
        text = f"# Answers of {name}, fetched from the hosted page.\n" + yaml.safe_dump(
            data, allow_unicode=True, sort_keys=False
        )
        (out_dir / f"answers_{name}.yaml").write_text(text, encoding="utf-8")
    return pending


# ----------------------------------------------------------------------------- scoring


def _read_file(path: Path, key: dict[str, Any]) -> tuple[str, dict[str, str | None], dict]:
    """(annotator, item id -> label or None when blank, item id -> note) from one answer file."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ReferenceCheckError(f"{path.name}: {exc}") from None
    rows = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ReferenceCheckError(f"{path.name}: not an answer file (no list of answers)")
    if str(data.get("sample")) != key["sample"]:
        raise ReferenceCheckError(f"{path.name} belongs to sample {data.get('sample')}, not "
                                  f"{key['sample']}")  # fmt: skip
    name = str(data.get("annotator") or path.stem.removeprefix("answers_"))
    if name not in key["annotators"]:
        raise ReferenceCheckError(f"{path.name}: {name} is not an annotator of this sample")
    on_page = key["items"] | key.get("overlap", {})
    mine = {iid for iid, it in on_page.items() if it["annotator"] == name}
    labels: dict[str, str | None] = {}
    notes: dict[str, str] = {}
    for row in rows:
        iid = str(row.get("item"))
        label = row.get("label")
        label = label.strip().lower() if isinstance(label, str) and label.strip() else None
        if iid not in mine:
            raise ReferenceCheckError(f"{path.name}: item {iid} is not on {name}'s page")
        if iid in labels:
            raise ReferenceCheckError(f"{path.name}: item {iid} is answered twice")
        if label is not None and label not in LABELS:
            raise ReferenceCheckError(f"{path.name}: {iid} has label {label!r}; use one of "
                                      f"{', '.join(LABELS)}")  # fmt: skip
        labels[iid] = label
        if note := str(row.get("note") or "").strip():
            notes[iid] = note
    return name, labels | {iid: None for iid in mine - labels.keys()}, notes


def read_answers(directory: Path, key: dict[str, Any]) -> tuple[dict, dict]:
    """Item id -> label (None when blank) and item id -> note, from one ``answers_<name>.yaml``
    per annotator of the sample."""
    labels: dict[str, str | None] = {}
    notes: dict[str, str] = {}
    seen: set[str] = set()
    for path in sorted(directory.glob("answers_*.yaml")):
        name, file_labels, file_notes = _read_file(path, key)
        if name in seen:
            raise ReferenceCheckError(f"two answer files for annotator {name}")
        seen.add(name)
        labels |= file_labels
        notes |= file_notes
    if missing := sorted(set(key["annotators"]) - seen):
        raise ReferenceCheckError(f"no answers yet from {', '.join(missing)}")
    return labels, notes


def parts(key: dict[str, Any]) -> list[str]:
    """The checks on these pages: ``reference``, then ``companion`` when it shares them."""
    found = {it.get("check", "reference") for it in key["items"].values()}
    return [c for c in ("reference", "companion") if c in found]


def part(key: dict[str, Any], check: str) -> dict[str, Any]:
    """The key of one check on shared pages, scored on its own. The reserve belongs to the
    reference check; the companion check is a census with none."""
    items = {i: it for i, it in key["items"].items() if it.get("check", "reference") == check}
    reserve = key.get("reserve", {}) if check == "reference" else {}
    return key | {"items": items, "reserve": reserve}


def upper_bound(errors: int, n: int) -> float:
    """One-sided 95% Wilson upper bound of an error rate of ``errors`` in ``n``."""
    p, z2 = errors / n, Z_95**2
    centre = p + z2 / (2 * n)
    spread = Z_95 * (p * (1 - p) / n + z2 / (4 * n * n)) ** 0.5
    return min(1.0, (centre + spread) / (1 + z2 / n))


def first_look_verdict(errors: int, n: int, reserve: int) -> str:
    """The first round's verdict: ``robust`` with no error, or when its worst case is no wider
    than the worst pass of the full check; ``not_robust`` when no reserve answers could bring
    the rate under the limit; otherwise ``extend`` (the reserve goes out)."""
    full = n + reserve
    worst_full_pass = upper_bound(math.floor(MAX_ERROR * full + 1e-9), full)
    if errors == 0 or upper_bound(errors, n) <= worst_full_pass:
        return "robust"
    if errors / full > MAX_ERROR:
        return "not_robust"
    return "extend"


def score(key: dict[str, Any], labels: dict[str, str | None]) -> dict[str, Any]:
    """The tool's error rate against the people's labels (see the module docstring)."""
    items = key["items"]
    blank = sorted(i for i in items if not labels.get(i))
    unsure = sorted(i for i in items if labels.get(i) == "unsure")
    wrong = sorted(i for i in items if labels.get(i) in ("keep", "drop")
                   and (labels[i] == "keep") != bool(items[i]["tool_kept"]))  # fmt: skip
    errors = set(wrong) | set(unsure)
    by_stratum = {}
    for s in STRATA:
        ids = [i for i in items if items[i]["stratum"] == s]
        by_stratum[s] = sum(i in errors for i in ids) / len(ids) if ids else None
    error = len(errors) / len(items)
    first_look = bool(key.get("reserve")) and not key.get("extended")
    if blank:
        verdict = "incomplete"
    elif first_look:
        verdict = first_look_verdict(len(errors), len(items), len(key["reserve"]))
    else:
        verdict = "robust" if error <= MAX_ERROR else "not_robust"
    bound = upper_bound(len(errors), len(items))
    result = {
        "items": len(items),
        "blank": blank,
        "unsure": unsure,
        "wrong": wrong,
        "error": error,
        "error_by_stratum": by_stratum,
        "upper_bound_95": bound,
        "verdict": verdict,
        "robust": verdict == "robust",
        "borderline": verdict == "robust" and bound > BORDERLINE_BOUND,
        "first_look": first_look,
    }
    if shared := key.get("overlap"):
        answered = [i for i, it in shared.items()
                    if labels.get(i) and labels.get(it["overlap_of"])]  # fmt: skip
        disagree = sorted(i for i in answered if labels[i] != labels[shared[i]["overlap_of"]])
        result["overlap"] = {"pairs": len(answered), "agree": len(answered) - len(disagree),
                             "disagree": disagree}  # fmt: skip
    return result


def format_result(
    result: dict[str, Any], key: dict[str, Any], notes: dict[str, str] | None = None
) -> str:
    """The verdict, with each wrong item's tool reason and the person's note, if any."""

    def pct(x: float | None) -> str:
        return "n/a" if x is None else f"{x:.1%}"

    def name(iid: str) -> str:
        it = key["items"][iid]
        kept = "kept" if it["tool_kept"] else "dropped"
        return f"{iid} = {it['case_id']} {it['item_id']} ({it['stratum']}, {kept} by the tool)"

    lines = [
        f"reference-answer check: {result['items']} items; tool error {pct(result['error'])} "
        f"against a {MAX_ERROR:.0%} limit (95% upper bound {pct(result['upper_bound_95'])}); "
        "not sure counts as an error",
        "by stratum: " + ", ".join(f"{k} {pct(v)}" for k, v in result["error_by_stratum"].items()),
    ]
    if (shared := result.get("overlap")) is not None:
        lines.append(f"people agree with each other on {shared['agree']} of {shared['pairs']} "
                     "shared items (context only, not in the gate)")  # fmt: skip
        for iid in shared["disagree"]:
            it = key["overlap"][iid]
            lines.append(f"  people differ: {iid} and {it['overlap_of']} = {it['case_id']} "
                         f"{it['item_id']}")  # fmt: skip
    for iid in result["wrong"]:
        lines.append(f"  wrong: {name(iid)}")
        if reason := key["items"][iid].get("tool_reason"):
            lines.append(f"    tool: {reason[:300]}")
        if note := (notes or {}).get(iid):
            lines.append(f"    person: {note}")
    for iid in result["unsure"]:
        lines.append(f"  not sure: {name(iid)}")
        if note := (notes or {}).get(iid):
            lines.append(f"    person: {note}")
    verdict = result["verdict"]
    if verdict == "incomplete":
        lines.append(f"INCOMPLETE: {len(result['blank'])} items have no label; finish them, "
                     "then score again")  # fmt: skip
    elif verdict == "robust":
        lines.append("ROBUST: the reference answers can be published; spot checks stay optional")
        if result.get("borderline"):
            lines.append(f"  borderline: the 95% upper bound is above {BORDERLINE_BOUND:.0%}; "
                         "say so when the key is used")  # fmt: skip
    elif verdict == "extend":
        lines.append("NOT YET: too many errors to pass on the first round alone, too few to fail; "
                     "send the reserve with `extend` "
                     "(each annotator gets their extra items), then score again")  # fmt: skip
    else:
        lines.append(
            "NOT ROBUST: fix the drafting or tie-break prompts, re-draft, then check again"
        )
    return "\n".join(lines)
