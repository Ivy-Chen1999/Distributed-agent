"""The fidelity sweep (plan 2026-10-16-001): tighten every kept answer-key point to what its
source states, after the reference check of 2026-10-14 scored NOT ROBUST.

Three steps, each its own command in ``scripts/key_fidelity_sweep.py``:

- ``sweep``: every kept, not yet swept item of the given drafts gets the labelling page's card
  and the page's own question (``womm.eval.fidelity``, the tie-break role's model). Verdicts go
  to ``sweep.private.json``; no draft is written.
- ``trim``: each ``drop`` goes to the drafter role (a different model), which may only delete
  the words the source does not support, from the fields the page judges; a card with a line to
  judge, or one anchored on the review board's opinion, is never trimmed. The trimmed card is
  judged again; still ``drop`` (or no valid cut) becomes a drop proposal. Proposals go to
  ``edits.private.json`` and a local before/after page; still no draft is written.
- ``apply``: the accepted proposals become each item's ``sweep`` overlay
  (``womm.eval.drafting.SweepBlock``): the person's decision, the tie-break and the audit stay
  as they were. ``key_tightened.json`` records counts, the rulings behind them and each draft's
  digest after the write; it holds no item text.

Holdout drafts are swept only with ``holdout=True``: no tracing, outputs under ``.cache/``, and
nothing about their items is printed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import random
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langsmith import tracing_context

from womm.config import REPO_ROOT
from womm.eval import fidelity, ia_sources
from womm.eval import reference_check as rc
from womm.eval.drafting import (
    SWEEP_TRIMMABLE,
    DraftingConfig,
    GoldenDraft,
    SweepBlock,
    _DraftItem,
    draft_path,
    load_draft,
    require_under_cache,
    write_draft,
)
from womm.eval.golden_review import MIN_KEPT_IMPACTS, is_deletion, is_kept, kept_impacts
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel

TRIM_PROMPT = REPO_ROOT / "prompts/tools/trim_point.md"
TRIM_ROLE = "fidelity_trim"
SWEEP_FILE, EDITS_FILE, RECORD_FILE = (
    "sweep.private.json",
    "edits.private.json",
    "key_tightened.json",
)
# The one failed check this sweep stands in for (owner decision 2026-10-16); any other failed
# check needs a new owner decision and plan.
SAMPLE = "2026-10-14-75af237c"
KEEP_SAMPLE_RATE, KEEP_SAMPLE_MIN = 0.10, 2
MAX_AI_WRONG = 3  # of the 24 errors ruled blind before the sweep (U0)
MAX_WRONG_KEEPS_PER_15 = 1  # wrong keeps in the seeded sample of the sweep's keeps (U3)


class SweepError(ValueError):
    """Bad input for a sweep step: nothing was written."""


class TrimVerdict(StrictModel):
    item_id: str
    action: Literal["trim", "drop"]
    fields: dict[str, str]


class TrimOutput(StrictModel):
    verdicts: list[TrimVerdict]


@dataclass(frozen=True)
class Swept:
    key: str  # "<case_id>/<item_id>": unique across the drafts
    path: Path
    draft: GoldenDraft
    item: _DraftItem


def _key(case_id: str, item_id: str) -> str:
    return f"{case_id}/{item_id}"


def swept_items(path: Path, draft: GoldenDraft) -> list[Swept]:
    """Every item a publish would keep that the sweep has not edited yet."""
    kept = [*kept_impacts(draft), *(o for o in draft.important_omissions if is_kept(o))]
    return [Swept(_key(draft.case_id, i.item_id), path, draft, i) for i in kept if i.sweep is None]


def load(paths: list[Path], *, holdout: bool) -> list[tuple[Path, GoldenDraft]]:
    drafts = []
    for path in paths:
        draft = load_draft(path)
        if (draft.split == "holdout") != holdout:
            raise SweepError(
                f"{path.name}: a holdout draft is swept with --holdout only, and only holdout "
                "drafts are"
            )
        if holdout:
            require_under_cache(path, "a holdout draft")
        drafts.append((path, draft))
    return drafts


def _merged(drafts: list[tuple[Path, GoldenDraft]]) -> dict[str, GoldenDraft]:
    """One draft per case with its companions' omissions, as the labelling page shows it."""
    by_case: dict[str, GoldenDraft] = {}
    for _, d in sorted(drafts, key=lambda pd: pd[1].companion is not None):
        if d.case_id in by_case:
            base = by_case[d.case_id]
            both = [*base.important_omissions, *d.important_omissions]
            by_case[d.case_id] = base.model_copy(update={"important_omissions": both})
        else:
            by_case[d.case_id] = d
    return by_case


def cards(
    items: list[Swept],
    drafts: list[tuple[Path, GoldenDraft]],
    judge_lines: dict[str, str],
    ia_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    by_case = _merged(drafts)
    texts: dict[str, Any] = {}
    for fixture in sorted({d.fixture for d in by_case.values()}):
        cached = ia_sources.load_cached_ia(fixture, ia_root)
        texts[fixture] = cached
    ia = {f: (c.full_text if c else None) for f, c in texts.items()}
    rsb = {f: (c.rsb_text if c else None) for f, c in texts.items()}
    out = {}
    for s in items:
        it = rc.Item(s.draft.case_id, s.draft.fixture, s.item.item_id, "", True)
        out[s.key] = rc.card(s.key, it, by_case, ia, rsb, concern=judge_lines.get(s.key))
    return out


def _trimmable(card: dict[str, Any]) -> list[str]:
    """The fields the page judges on this card that a trim may cut; none for a card with a line
    to judge or one anchored on the board's opinion."""
    if card["source"] != "report" or "concern" in card["claim"]:
        return []
    return [k for k in SWEEP_TRIMMABLE if k in card["claim"]]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def _inputs(paths: list[Path]) -> dict[str, str]:
    """Each input draft's digest, keyed by its resolved path: what a ruling was made against."""
    return {str(p.resolve()): digest(p) for p in sorted(paths)}


def _check_inputs(inputs: dict[str, str], what: str) -> None:
    changed = sorted(Path(p).name for p, d in inputs.items()
                     if not Path(p).exists() or digest(Path(p)) != d)  # fmt: skip
    if changed:
        raise SweepError(f"{', '.join(changed)} changed since {what}; nothing written, sweep again")


def _batch(inputs: dict[str, str], items: dict[str, Any]) -> str:
    blob = json.dumps({"inputs": inputs, "items": items}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


async def sweep(
    paths: list[Path],
    out_dir: Path,
    config: DraftingConfig,
    backends: dict[str, LLMBackend],
    *,
    judge_lines: dict[str, str] | None = None,
    holdout: bool = False,
    ia_root: Path | None = None,
) -> dict[str, int]:
    """Judge every kept item on the page's question; write ``sweep.private.json``."""
    if holdout:
        require_under_cache(out_dir, "the holdout sweep's output")
    drafts = load(paths, holdout=holdout)
    items = [s for p, d in drafts for s in swept_items(p, d)]
    if not items:
        raise SweepError("no kept item left to sweep")
    shown = cards(items, drafts, judge_lines or {}, ia_root)
    with tracing_context(enabled=False if holdout else None):
        verdicts = await fidelity.judge_cards(list(shown.values()), config, backends)
    if missing := sorted(set(shown) - set(verdicts)):
        raise SweepError(f"no verdict for {len(missing)} item(s); nothing written")
    entries = {k: {"path": str(s.path), "case_id": s.draft.case_id, "item_id": s.item.item_id,
                   "verdict": verdicts[k].verdict, "reason": verdicts[k].reason}
               for k, s in ((s.key, s) for s in items)}  # fmt: skip
    inputs = _inputs([p for p, _ in drafts])
    _write_json(out_dir / SWEEP_FILE, {"model": config.roles.tiebreak.model,  # type: ignore[union-attr]
                                       "inputs": inputs, "batch": _batch(inputs, entries),
                                       "items": entries})  # fmt: skip
    return {
        "swept": len(entries),
        "keep": sum(e["verdict"] == "keep" for e in entries.values()),
        "drop": sum(e["verdict"] == "drop" for e in entries.values()),
    }


async def trim(
    out_dir: Path,
    config: DraftingConfig,
    backends: dict[str, LLMBackend],
    *,
    judge_lines: dict[str, str] | None = None,
    holdout: bool = False,
    ia_root: Path | None = None,
) -> dict[str, Any]:
    """Propose a deletion-only cut, or a drop, for every ``drop`` of the sweep."""
    swept = json.loads((out_dir / SWEEP_FILE).read_text(encoding="utf-8"))
    _check_inputs(swept["inputs"], "the sweep")
    flagged = {k: e for k, e in swept["items"].items() if e["verdict"] == "drop"}
    paths = sorted({Path(e["path"]) for e in swept["items"].values()})
    drafts = load(paths, holdout=holdout)
    items = {s.key: s for p, d in drafts for s in swept_items(p, d) if s.key in flagged}
    if missing := sorted(set(flagged) - set(items)):
        raise SweepError(f"{len(missing)} flagged item(s) changed since the sweep; sweep again")
    shown = cards(list(items.values()), drafts, judge_lines or {}, ia_root)
    role = config.roles.drafter
    proposals: dict[str, dict[str, Any]] = {}
    to_cut = {k: c for k, c in shown.items() if _trimmable(c)}
    for k in shown.keys() - to_cut.keys():
        proposals[k] = {"action": "drop", "why": "a judged line or a board point is not trimmed"}
    if to_cut:
        prompt = TRIM_PROMPT.read_text(encoding="utf-8")
        user = "## Cards\n\n" + json.dumps(
            [fidelity.card_input(c) | {"trimmable": _trimmable(c), "why_drop": flagged[k]["reason"]}
             for k, c in to_cut.items()], indent=1, ensure_ascii=False)  # fmt: skip
        with tracing_context(enabled=False if holdout else None):
            out, _ = await backends[role.backend].call(TRIM_ROLE, prompt, user, TrimOutput, role)
        got = {v.item_id: v for v in out.verdicts}
        recheck = {}
        for k, c in to_cut.items():
            v = got.get(k)
            cut = {f: t for f, t in (v.fields if v else {}).items() if t != c["claim"].get(f)}
            ok = (v is not None and v.action == "trim" and cut
                  and all(f in _trimmable(c) and is_deletion(t, c["claim"][f])
                          for f, t in cut.items()))  # fmt: skip
            if not ok:
                proposals[k] = {"action": "drop", "why": "no deletion-only cut keeps it supported"}
                continue
            proposals[k] = {"action": "trim", "after": cut}
            recheck[k] = c | {"claim": c["claim"] | cut}
        if recheck:
            with tracing_context(enabled=False if holdout else None):
                again = await fidelity.judge_cards(list(recheck.values()), config, backends)
            for k in recheck:
                v2 = again.get(k)
                if v2 is None or v2.verdict != "keep":
                    proposals[k] = {"action": "drop", "why": "still unsupported after the cut: "
                                    + (v2.reason if v2 else "no verdict")}  # fmt: skip
                else:
                    proposals[k]["recheck"] = v2.reason
    edits = []
    for k, prop in sorted(proposals.items()):
        s, e = items[k], flagged[k]
        before = {f: getattr(s.item, f) for f in prop.get("after", {})}
        edits.append({"key": k, "path": str(s.path), "case_id": s.draft.case_id,
                      "item_id": s.item.item_id, "reason": e["reason"], **prop,
                      "before": before})  # fmt: skip
    under = _under_minimum(drafts, edits)
    _write_json(out_dir / EDITS_FILE, {"sweep_model": swept["model"], "trim_model": role.model,
                                       "sweep_batch": swept["batch"], "inputs": swept["inputs"],
                                       "edits": edits, "under_minimum": under})  # fmt: skip
    if not holdout:
        (out_dir / "edits.html").write_text(edits_page(edits, shown, under), encoding="utf-8")
    return {"flagged": len(flagged), "trim": sum(e["action"] == "trim" for e in edits),
            "drop": sum(e["action"] == "drop" for e in edits), "under_minimum": under}  # fmt: skip


def _under_minimum(drafts: list[tuple[Path, GoldenDraft]], edits: list[dict]) -> list[str]:
    dropped = {(e["case_id"], e["item_id"]) for e in edits if e["action"] == "drop"}
    under = []
    for _, d in drafts:
        if d.companion is not None:
            continue
        left = [i for i in kept_impacts(d) if (d.case_id, i.item_id) not in dropped]
        if len(left) < MIN_KEPT_IMPACTS:
            under.append(d.case_id)
    return under


def edits_page(edits: list[dict], shown: dict[str, dict], under: list[str]) -> str:
    """A plain local page: every proposal, before and after, with the source and both reasons."""
    esc = html.escape
    rows = []
    for e in edits:
        c = shown[e["key"]]
        p = c.get("passage") or {}
        src = (f"{esc(p.get('before', ''))}<mark>{esc(p.get('quote', c['quote']))}</mark>"
               f"{esc(p.get('after', ''))}")  # fmt: skip
        if e["action"] == "trim":
            change = "".join(f"<p><b>{esc(f)}</b><br><del>{esc(e['before'][f])}</del><br>"
                             f"<ins>{esc(t)}</ins></p>" for f, t in e["after"].items())  # fmt: skip
        else:
            change = f"<p><b>Drop the point</b> ({esc(e.get('why', ''))})</p>"
        claim = "".join(f"<li><b>{esc(k)}</b>: {esc(v)}</li>" for k, v in c["claim"].items())
        rows.append(f"<section><h2>{esc(e['key'])} · {esc(e['action'])}</h2><ul>{claim}</ul>"
                    f"{change}<p class=why>Why flagged: {esc(e['reason'])}</p>"
                    "<details><summary>Source</summary>"
                    f"<p class=src>{src}</p></details></section>")  # fmt: skip
    warn = (f"<p class=warn>Under {MIN_KEPT_IMPACTS} kept impacts if every drop is accepted: "
            f"{esc(', '.join(under))}</p>") if under else ""  # fmt: skip
    return ("<!doctype html><meta charset=utf-8><title>Sweep Edits</title>"
            "<style>body{font:15px/1.5 "
            "system-ui;max-width:900px;margin:24px auto;padding:0 16px}del{color:#b3261e}ins{color:"
            "#2e6b3a;text-decoration:none}section{border-top:1px solid #ccc;padding:8px 0}.why,.src"
            "{color:#555}.warn{color:#b3261e;font-weight:600}</style><h1>Fidelity sweep: proposed "
            f"edits ({len(edits)})</h1><p>Private: stays on this machine.</p>{warn}"
            + "".join(rows))  # fmt: skip


def keep_sample(out_dir: Path, seed: int = 1) -> list[str]:
    """The seeded sample of the sweep's keeps the owner checks (10% per draft, at least 2)."""
    swept = json.loads((out_dir / SWEEP_FILE).read_text(encoding="utf-8"))["items"]
    by_draft: dict[str, list[str]] = {}
    for k, e in sorted(swept.items()):
        if e["verdict"] == "keep":
            by_draft.setdefault(e["path"], []).append(k)
    picked = []
    for path, keys in sorted(by_draft.items()):
        n = min(len(keys), max(KEEP_SAMPLE_MIN, round(KEEP_SAMPLE_RATE * len(keys))))
        picked += random.Random(f"{seed}:{path}").sample(keys, n)
    return sorted(picked)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def apply(
    out_dir: Path,
    *,
    rejected: set[str],
    rulings: dict[str, Any],
    wrong_keeps: int,
    ruled_by: str,
    holdout: bool = False,
    on: dt.date | None = None,
) -> dict[str, Any]:
    """Write the accepted proposals as sweep overlays and the record. ``rulings`` are the counts
    exported by the review page (U0); ``wrong_keeps`` the owner's count in ``keep_sample``."""
    on = on or dt.date.today()
    edits = json.loads((out_dir / EDITS_FILE).read_text(encoding="utf-8"))
    swept = json.loads((out_dir / SWEEP_FILE).read_text(encoding="utf-8"))
    if edits.get("sweep_batch") != swept.get("batch") or edits.get("inputs") != swept["inputs"]:
        raise SweepError("the proposals come from another sweep batch; trim again")
    _check_inputs(swept["inputs"], "the sweep")
    sampled = keep_sample(out_dir)
    if rulings.get("sample") != SAMPLE:
        raise SweepError(f"the rulings are for {rulings.get('sample')!r}, not {SAMPLE}")
    if rulings.get("open", 1) or rulings.get("key_open", 1):
        raise SweepError("rule every card on the review page first (open items remain)")
    if rulings.get("ai_wrong", 99) > MAX_AI_WRONG:
        raise SweepError(f"the AI was ruled wrong on {rulings['ai_wrong']} of the errors "
                         f"(limit {MAX_AI_WRONG}): fix the fidelity prompt and re-run it "
                         "first")  # fmt: skip
    limit = MAX_WRONG_KEEPS_PER_15 * max(1, round(len(sampled) / 15))
    if wrong_keeps > limit:
        raise SweepError(f"{wrong_keeps} wrong keeps in the sample of {len(sampled)} (limit "
                         f"{limit}): the sweep's keeps are not trusted; fix the prompt, sweep "
                         "again")  # fmt: skip
    if unknown := sorted(rejected - {e["key"] for e in edits["edits"]}):
        raise SweepError(f"rejected ids not among the proposals: {', '.join(unknown)}")
    accepted = [e for e in edits["edits"] if e["key"] not in rejected]
    all_paths = [Path(p) for p in sorted(swept["inputs"])]
    under = _under_minimum(load(all_paths, holdout=holdout),
                           [e for e in accepted if e["action"] == "drop"])  # fmt: skip
    if under:
        raise SweepError(f"under {MIN_KEPT_IMPACTS} kept impacts: {', '.join(under)}; reject some "
                         "drops of these cases or retire them first")  # fmt: skip
    staged = {}
    for path in all_paths:
        mine = {e["item_id"]: e for e in accepted if Path(e["path"]).resolve() == path}
        if mine:
            staged[path] = _overlaid(load_draft(path), mine, edits, on)
    _write_all(staged)
    written = {str(p): digest(p) for p in all_paths}  # every swept draft, edited or not
    record = {"sample": SAMPLE, "basis": "tightened, not re-checked", "on": on.isoformat(),
              "ruled_by": ruled_by, "holdout": holdout,
              "sweep_model": edits["sweep_model"], "trim_model": edits["trim_model"],
              "rulings": {k: v for k, v in rulings.items() if k != "sample"},
              "proposed": len(edits["edits"]), "rejected": len(rejected),
              "trimmed": sum(e["action"] == "trim" for e in accepted),
              "dropped": sum(e["action"] == "drop" for e in accepted),
              "sampled_keeps": len(sampled), "wrong_keeps": wrong_keeps,
              "drafts": written}  # fmt: skip
    _write_json(out_dir / RECORD_FILE, record)
    return record


def record_problems(record: dict[str, Any]) -> list[str]:
    """Why a sweep record cannot stand in for the failed check (empty when it can)."""
    problems = []
    if record.get("sample") != SAMPLE:
        problems.append(f"it is for {record.get('sample')!r}, not {SAMPLE}")
    rulings = record.get("rulings", {})
    if rulings.get("open", 1) or rulings.get("key_open", 1):
        problems.append("the review page was not fully ruled")
    if rulings.get("ai_wrong", MAX_AI_WRONG + 1) > MAX_AI_WRONG:
        problems.append(f"the AI was ruled wrong on more than {MAX_AI_WRONG} errors")
    sampled = record.get("sampled_keeps", 0)
    limit = MAX_WRONG_KEEPS_PER_15 * max(1, round(sampled / 15))
    if not sampled or record.get("wrong_keeps", limit + 1) > limit:
        problems.append("the sweep's keeps were not checked within the limit")
    if not record.get("drafts"):
        problems.append("it names no draft")
    return problems


def _overlaid(draft: GoldenDraft, mine: dict[str, dict], edits: dict, on: dt.date) -> GoldenDraft:
    def edit(item: _DraftItem) -> _DraftItem:
        e = mine.get(item.item_id)
        if e is None:
            return item
        if e["action"] == "trim" and any(getattr(item, f) != t for f, t in e["before"].items()):
            raise SweepError(f"{draft.case_id}/{item.item_id} changed since the trim; trim again")
        model = edits["trim_model"] if e["action"] == "trim" else edits["sweep_model"]
        block = SweepBlock(action=e["action"], before=e["before"], reason=e["reason"],
                           model=model, on=on)  # fmt: skip
        return item.model_copy(update={**e.get("after", {}), "sweep": block})

    sections = ("expected_impacts", "important_omissions", "possibly_missing")
    return draft.model_copy(update={s: [edit(i) for i in getattr(draft, s)] for s in sections})


def _rendered(draft: GoldenDraft, path: Path) -> str:
    """The bytes ``write_draft`` would put at ``path``, refused when it would write elsewhere."""
    target = draft_path(draft.case_id, draft.split, path.parent, draft.companion)
    if target != path.resolve():
        raise SweepError(f"{path.name}: the draft would be written as {target.name}")
    with tempfile.TemporaryDirectory() as tmp:
        text = write_draft(draft, Path(tmp)).read_text(encoding="utf-8")
    if "raised_by:" not in path.read_text(encoding="utf-8"):
        text = text.replace(_LATER_NULLS, "")
    return text


def _write_all(staged: dict[Path, GoldenDraft]) -> None:
    """Check every new draft and render it before writing any, so a failure writes nothing."""
    from womm.eval.golden_review import check_draft

    texts = {}
    for path, draft in staged.items():
        if problems := check_draft(draft, None):
            raise SweepError(f"{path.name}: nothing written:\n  " + "\n  ".join(problems))
        texts[path] = _rendered(draft, path)
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8")


_LATER_NULLS = "    raised_by: null\n    feedback_id: null\n    analyst_digest: null\n"


def apply_corrections(
    corrections: list[dict[str, Any]], *, ruled_by: str, on: dt.date | None = None
) -> dict[str, str]:
    """Write rulings made by reading the whole report (not by the sweep) as sweep overlays:
    each entry names ``path``, ``draft_digest`` (the draft the ruling was made on), ``item_id``,
    ``action`` (trim|drop), ``after`` (field -> text, a deletion of the current text) and
    ``reason``. A draft changed since its ruling is refused; every draft must still pass
    ``check_draft``; nothing is written unless all pass. Returns each written draft's digest."""
    on = on or dt.date.today()
    by_path: dict[Path, dict[str, dict]] = {}
    expected: dict[Path, str] = {}
    for c in corrections:
        path = Path(c["path"]).resolve()
        if expected.setdefault(path, c["draft_digest"]) != c["draft_digest"]:
            raise SweepError(f"{path.name}: rulings made on different versions of the draft")
        by_path.setdefault(path, {})[c["item_id"]] = c
    _check_inputs({str(p): d for p, d in expected.items()}, "the rulings")
    staged = {}
    for path, mine in by_path.items():
        draft = load_draft(path)
        items = {i.item_id: i for i in draft.items()}
        if missing := sorted(set(mine) - set(items)):
            raise SweepError(f"{path.name}: no item {', '.join(missing)}")
        entries = {
            iid: c | {"before": {f: getattr(items[iid], f) for f in c.get("after", {})}}
            for iid, c in mine.items()
        }
        staged[path] = _overlaid(
            draft, entries, {"trim_model": ruled_by, "sweep_model": ruled_by}, on
        )
    _write_all(staged)
    return {str(p): digest(p) for p in staged}
