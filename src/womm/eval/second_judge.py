"""The holdout's blind second judge (plan 2026-10-26-001, docs/plans/).

The Claude chain (three judges and the tie-break) settles every holdout draft; a judge from
another model family (the ``second_judge`` role of evals/drafting.yaml) then judges every item,
and an item enters the sealed key only when both keep it (scripts/second_judge_holdout.py). The
judge sees an item's point, its kind, the source text around its anchor quote and the text of the
provisions it cites. It never sees the judges' verdicts or reasons, the tie-break, the review
decision, the category, the flags or the original item id: items get neutral ids per call.

Every call is paid at list price (the api backend). ``Spend`` reserves an estimate before each
call, against the command's own cap and against one ledger of all second-judge spend
(``TOTAL_CAP_USD``), and records the actual cost after it. ``BatchStore`` keeps each batch's
verdicts, keyed by the exact judge input, so a rerun after a crash or a rate limit pays only for
the batches still missing. Calls run with LangSmith tracing disabled.

Acceptance (``acceptance_items`` and ``score_pass``) checks the judge on the public regression set
(evals/regression/key_fidelity_2026-10-17.yaml) in both directions before any holdout call: it
must keep no point the set marks unsupported, and keep at least ``GATE_B_MIN`` of the supported
controls.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import yaml
from langsmith import tracing_context

from womm.config import REPO_ROOT
from womm.data.fixtures import Fixture, fixture_dir, load_fixture
from womm.eval import key_check as kc
from womm.eval import key_sources as ks
from womm.eval.drafting import DraftingConfig, GoldenDraft, _DraftItem
from womm.eval.reference_check import _passage
from womm.llm.api import list_price_usd
from womm.llm.base import LLMBackend, LLMError
from womm.models.base import StrictModel
from womm.models.regulation import Scenario
from womm.models.system_version import RoleConfig

ROLE = "second_judge"
BATCH = 15  # items per call
PASSAGE_CHARS = 1500  # source text on each side of the anchor (about 3000 in all)
PROVISION_CHARS = 2500  # text of one cited provision
# Passages found elsewhere in the IA, the board's opinion or the proposal, after the anchor
# window (plan KTD2, revision of 2026-11-03: most IA points rest on text far from the anchor).
FULL_TEXT_PASSAGES = 4
CHARS_PER_TOKEN = 3  # a conservative estimate for the pre-call reservation
# Output tokens reserved per attempt, reasoning included. The api backend sets no output limit,
# so a long answer can still cost more; the overshoot is at most part of one call.
OUTPUT_ALLOWANCE = 16000
TOTAL_CAP_USD = 5.0  # all second-judge spend, acceptance and settlement together (plan R7)
GATE_B_MIN = 12  # supported controls a pass must keep (plan KTD4)
CACHE_DIR = REPO_ROOT / ".cache" / "second_judge"
LEDGER = CACHE_DIR / "spend.json"
REGRESSION = REPO_ROOT / "evals/regression/key_fidelity_2026-10-17.yaml"
CODEX_LABELLED = frozenset({"j01", "j02", "j03", "j04", "w12"})  # labels Codex ruled (2026-10-17)


class SecondJudgeError(RuntimeError):
    """Refused before or during judging; the message names what to do."""


class BudgetExceeded(SecondJudgeError):
    pass


class SecondJudgeVerdict(StrictModel):
    item_id: str
    verdict: Literal["keep", "drop", "unsure"]
    reason: str


class SecondJudgeOutput(StrictModel):
    verdicts: list[SecondJudgeVerdict]


# ----------------------------------------------------------------------------- the blind input


@dataclass(frozen=True)
class JudgeItem:
    """What the judge sees of one item, plus the id it is mapped back to."""

    item_id: str
    kind: str  # "effect" or "omission"
    point: dict[str, str]
    source: str  # "official report" or "review board opinion"
    section: str
    passages: tuple[str, ...]
    provisions: tuple[tuple[str, str], ...]  # (provision key, text)

    def card(self, neutral_id: str) -> dict[str, Any]:
        return {
            "item_id": neutral_id,
            "kind": self.kind,
            "point": self.point,
            "source": self.source,
            "section": self.section,
            "source_text": list(self.passages),
            "provisions": [{"key": k, "text": t} for k, t in self.provisions],
        }


def passage(quote: str, text: str, chars: int = PASSAGE_CHARS) -> str:
    """The source text around the anchor quote, the quote marked ``[[...]]``."""
    found = _passage(quote, text, chars) if text else None
    if found is None:
        return f"[[{quote}]] (the surrounding text was not found)"
    return f"{found['before']}[[{found['quote']}]]{found['after']}"


def index_for(fixture: str, ia_root: Path | None = None) -> kc.Index:
    """The full-text index of a fixture's IA, board opinion and proposal (womm.eval.key_sources,
    the passages the full-text key check reads)."""
    return kc.Index(list(ks.load_sources(fixture, ia_root).passages))


def retrieved(
    index: kc.Index, fields: dict[str, str], anchor: str, on_rsb: bool,
    k: int = FULL_TEXT_PASSAGES,
) -> tuple[str, ...]:  # fmt: skip
    """Up to ``k`` passages from anywhere in the fixture's texts that the point's own words rank
    highest (BM25, each field a search; no model call), the anchor's own passage left out since the
    window shows it. A board-opinion point searches the opinion and the proposal only."""
    text = " ".join(v for v in fields.values() if v)
    point = kc.PointInput(key="", fixture="", fields=fields, anchor=anchor,
                          anchored_on="rsb_opinion" if on_rsb else "ia")  # fmt: skip
    claim = kc.SplitClaim(id="c", text=text, searches=[v for v in fields.values() if v])
    own = kc._norm(anchor)[:120]
    out: list[str] = []
    for found in kc._retrieve(index, point, claim):
        if own and own in kc._norm(found.text):
            continue
        where = found.where + (f", {found.section}" if found.section else "")
        out.append(f"[{where}] {found.text}")
        if len(out) == k:
            break
    return tuple(out)


def provision_texts(
    fixture: Fixture, scenario: Scenario | None, keys: list[str]
) -> tuple[tuple[str, str], ...]:
    """The after-version text of each cited provision; a local holdout scenario works too."""
    if scenario is None or not keys:
        return ()
    wanted = set(keys)
    after = fixture.version(scenario.after_version)
    return tuple(
        (p.provision_key, p.text[:PROVISION_CHARS])
        for p in after.provisions
        if p.provision_key in wanted
    )


def _point(item: _DraftItem) -> dict[str, str]:
    keys = ("affected_actor", "mechanism", "impact", "description")  # never why_missing
    return {k: getattr(item, k) for k in keys if hasattr(item, k)}


def draft_items(
    draft: GoldenDraft,
    fixture: Fixture,
    scenario: Scenario,
    ia_text: str,
    rsb_text: str,
    index: kc.Index | None = None,
) -> list[JudgeItem]:
    """Every item of ``draft`` as the judge sees it (candidates are shown as effects); with
    ``index``, the anchor window is followed by passages found elsewhere in the texts."""
    items = []
    for item in draft.items():
        on_rsb = item.anchor.against == "rsb"
        window = passage(item.ia_anchor, rsb_text if on_rsb else ia_text)
        more = retrieved(index, _point(item), item.ia_anchor, on_rsb) if index else ()
        items.append(
            JudgeItem(
                item_id=item.item_id,
                kind="omission" if hasattr(item, "omission_id") else "effect",
                point=_point(item),
                source="review board opinion" if on_rsb else "official report",
                section=item.ia_section or "",
                passages=(window, *more),
                provisions=provision_texts(fixture, scenario, list(item.provision_keys)),
            )
        )
    return items


def user_content(items: list[JudgeItem]) -> tuple[str, dict[str, str]]:
    """The call's user content and the neutral-id -> item-id map."""
    ids = {f"i{n:02d}": it.item_id for n, it in enumerate(items, 1)}
    cards = [it.card(nid) for nid, it in zip(ids, items, strict=True)]
    return "## Items\n\n" + json.dumps(cards, indent=1, ensure_ascii=False), ids


def judge_id(role: RoleConfig) -> str:
    return f"second-judge:{role.model}"


def role_of(config: DraftingConfig) -> RoleConfig:
    role = config.roles.second_judge
    if role is None:
        raise SecondJudgeError("no second_judge role in the drafting config (evals/drafting.yaml)")
    if list_price_usd(role.model, 1, 0, 1) is None:
        raise SecondJudgeError(
            f"{role.model} has no list price in womm.llm.api.PRICES_PER_MTOK; a USD cap cannot "
            "be enforced, so it is refused"
        )
    return role


def prompt_sha256(config: DraftingConfig) -> str:
    role = role_of(config)
    return hashlib.sha256(config.prompts[role.prompt].encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- money


def estimate_usd(role: RoleConfig, system_prompt: str, content: str) -> float:
    """A high estimate of one call, every attempt included, at list price."""
    tokens_in = (len(system_prompt) + len(content)) // CHARS_PER_TOKEN + 1
    one = list_price_usd(role.model, tokens_in, 0, OUTPUT_ALLOWANCE)
    if one is None:
        raise SecondJudgeError(f"{role.model} has no list price")
    return one * (role.max_retries + 1)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class Spend:
    """Reserve-before-call accounting: a call runs only if its estimate fits under both the
    command's cap and the ledger's total cap; its actual cost is then added to both."""

    def __init__(self, max_usd: float, ledger: Path = LEDGER, total_cap: float = TOTAL_CAP_USD):
        self.max_usd = max_usd
        self.ledger = ledger
        self.total_cap = total_cap
        self.spent = 0.0
        self.calls = 0

    def recorded(self) -> float:
        if not self.ledger.exists():
            return 0.0
        return float(json.loads(self.ledger.read_text(encoding="utf-8")).get("total_usd", 0.0))

    def reserve(self, estimate: float) -> None:
        if self.spent + estimate > self.max_usd:
            raise BudgetExceeded(
                f"the next call (estimated USD {estimate:.2f}) would take this command over its "
                f"cap (USD {self.spent:.2f} of {self.max_usd:.2f} spent); stopped, finished "
                "batches are kept"
            )
        total = self.recorded()
        if total + estimate > self.total_cap:
            raise BudgetExceeded(
                f"the next call (estimated USD {estimate:.2f}) would take all second-judge "
                f"spend over USD {self.total_cap:.2f} (USD {total:.2f} recorded in "
                f"{self.ledger.name}); stopped"
            )

    def add(self, cost: float | None, label: str) -> None:
        if cost is None:
            raise SecondJudgeError("a call returned no cost; the USD cap cannot be enforced")
        self.spent += cost
        self.calls += 1
        data = (
            json.loads(self.ledger.read_text(encoding="utf-8"))
            if self.ledger.exists()
            else {"total_usd": 0.0, "calls": []}
        )
        data["total_usd"] = round(float(data["total_usd"]) + cost, 6)
        data["calls"].append({"label": label, "usd": round(cost, 6)})
        _write_json(self.ledger, data)


# ----------------------------------------------------------------------------- resume


class BatchStore:
    """One case's (or one acceptance pass's) verdicts per batch, bound to the model, reasoning
    effort and prompt that produced them; a store written by another judge is refused, never
    mixed."""

    def __init__(self, path: Path, model: str, prompt_sha: str, effort: str | None = None):
        self.path = path
        self.data: dict[str, Any] = {
            "model": model,
            "reasoning_effort": effort,
            "prompt_sha256": prompt_sha,
            "batches": {},
        }
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            stored = (data.get("model"), data.get("reasoning_effort"), data.get("prompt_sha256"))
            if stored != (model, effort, prompt_sha):
                prompt = str(data.get("prompt_sha256"))[:12]
                raise SecondJudgeError(
                    f"{path.name} holds verdicts of another judge ({data.get('model')}, effort "
                    f"{data.get('reasoning_effort')}, prompt {prompt}); the frozen judge is not "
                    "mixed with another. Remove the file deliberately to start over"
                )
            self.data = data

    @staticmethod
    def key(role: RoleConfig, prompt_sha: str, content: str) -> str:
        raw = f"{role.model}|{role.reasoning_effort}|{prompt_sha}|{content}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> list[dict] | None:
        return self.data["batches"].get(key)

    def put(self, key: str, verdicts: list[dict]) -> None:
        self.data["batches"][key] = verdicts
        _write_json(self.path, self.data)


# ----------------------------------------------------------------------------- judging


def batches(items: list[JudgeItem], size: int = BATCH) -> list[list[JudgeItem]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def judge(
    items: list[JudgeItem],
    config: DraftingConfig,
    backends: dict[str, LLMBackend],
    spend: Spend,
    store: BatchStore | None = None,
    label: str = "",
) -> dict[str, SecondJudgeVerdict]:
    """A verdict for every item (keyed by item id); an item the model leaves out is unsure."""
    role = role_of(config)
    prompt = config.prompts[role.prompt]
    sha = prompt_sha256(config)
    out: dict[str, SecondJudgeVerdict] = {}
    for batch in batches(items):
        content, ids = user_content(batch)
        key = BatchStore.key(role, sha, content)
        got = store.get(key) if store is not None else None
        if got is None:
            spend.reserve(estimate_usd(role, prompt, content))
            try:
                with tracing_context(enabled=False):
                    result, usage = await backends[role.backend].call(
                        ROLE, prompt, content, SecondJudgeOutput, role
                    )
            except LLMError as exc:
                if exc.usage is not None:
                    spend.add(exc.usage.cost_usd, f"{label} (failed)")
                raise
            spend.add(usage.cost_usd, label)
            got = [
                {"item_id": ids[v.item_id], "verdict": v.verdict, "reason": v.reason}
                for v in result.verdicts
                if v.item_id in ids
            ]
            if store is not None:
                store.put(key, got)
        answers: dict[str, list[dict]] = {}
        for v in got:
            answers.setdefault(v["item_id"], []).append(v)
        for iid, vs in answers.items():
            if len({v["verdict"] for v in vs}) > 1:  # conflicting answers are doubt (plan KD5)
                vs = [{"item_id": iid, "verdict": "unsure", "reason": "conflicting verdicts"}]
            out.setdefault(iid, SecondJudgeVerdict.model_validate(vs[0]))
        for it in batch:
            out.setdefault(
                it.item_id,
                SecondJudgeVerdict(item_id=it.item_id, verdict="unsure", reason="no verdict"),
            )
    return out


def estimate_items(items: list[JudgeItem], config: DraftingConfig) -> tuple[int, float]:
    """(calls, estimated USD) for judging ``items``, with no call."""
    role = role_of(config)
    prompt = config.prompts[role.prompt]
    parts = batches(items)
    return len(parts), sum(estimate_usd(role, prompt, user_content(b)[0]) for b in parts)


# ----------------------------------------------------------------------------- acceptance


def load_regression(path: Path = REGRESSION) -> list[dict[str, Any]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["cases"]


def _published_item(golden_dir: Path, case_id: str, point_id: str) -> tuple[str, list[str]]:
    """(scenario id, provision keys) of a point in its published case; ("", []) if absent."""
    path = golden_dir / f"{case_id}.yaml"
    if not path.exists():
        return "", []
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    for item in [*case.get("expected_impacts", []), *case.get("important_omissions", [])]:
        if point_id in (item.get("expected_id"), item.get("omission_id")):
            return case["scenario_id"], list(item.get("provision_keys", []))
    return case["scenario_id"], []


def acceptance_items(
    cases: list[dict[str, Any]], ia_root: Path, golden_dir: Path
) -> tuple[list[JudgeItem], list[JudgeItem], list[str]]:
    """Condition W (every case, holdout-identical input: the whole point) and condition C (each
    supported case's labelled clause, with the rest of its point as context), both with the anchor
    window and the passages retrieval finds elsewhere, as the holdout is judged; and notes for the
    record. Nothing is taken from a case's ``source``: retrieval must find the evidence.

    The regression set labels one clause per case, not the whole point, and a point labelled
    supported can hold another, unscreened clause the source does not support (plan 2026-10-26-001
    KTD4, revised 2026-11-03 after the first acceptance run). So gate B asks about the clause, and
    gate A keeps the whole point, as the holdout is judged."""
    w_items, c_items, notes = [], [], []
    indexes: dict[str, kc.Index] = {}
    for c in cases:
        inp = c["input"]
        fixture_name = inp["fixture"]
        folder = ia_root / fixture_name
        on_rsb = inp.get("anchored_on") == "rsb"
        anchor_file = folder / ("rsb_opinion.txt" if on_rsb else "ia_full.txt")
        if not anchor_file.exists():
            raise SecondJudgeError(f"{c['id']}: {anchor_file} is not cached; fetch the IA first")
        text = anchor_file.read_text(encoding="utf-8")
        provisions: tuple[tuple[str, str], ...] = ()
        if c.get("case") and c.get("point"):
            scenario_id, keys = _published_item(golden_dir, c["case"], c["point"])
            if keys:
                fixture = load_fixture(fixture_dir(fixture_name))
                provisions = provision_texts(fixture, fixture.scenario(scenario_id), keys)
        if not provisions:
            notes.append(f"{c['id']}: no provisions shown (no published item to take them from)")
        point = inp["point"]
        if fixture_name not in indexes:
            indexes[fixture_name] = index_for(fixture_name, ia_root)
        index = indexes[fixture_name]
        window = passage(inp["anchor"], text)
        base = JudgeItem(
            item_id=c["id"],
            kind="omission" if "description" in point else "effect",
            point=dict(point),
            source="review board opinion" if on_rsb else "official report",
            section=inp.get("section", ""),
            passages=(window, *retrieved(index, dict(point), inp["anchor"], on_rsb)),
            provisions=provisions,
        )
        w_items.append(base)
        if c["expected"] != "supported":
            continue
        clause = {
            "claim_to_judge": c["claim"],
            # the whole point, so a clause that is only a fragment can be read (plan KTD4)
            "context_only": " / ".join(str(v) for v in point.values()),
        }
        found = retrieved(index, {"claim": c["claim"], **point}, inp["anchor"], on_rsb)
        c_items.append(replace(base, point=clause, passages=(window, *found)))
    return w_items, c_items, notes


def _point_key(c: dict[str, Any]) -> str:
    return f"{c.get('case')}/{c.get('point')}" if c.get("point") else c["id"]


def controls(
    cases: list[dict[str, Any]], exclude: frozenset[str] = frozenset()
) -> tuple[dict[str, list[str]], list[str], list[str]]:
    """(unsupported points -> their case ids, W positive controls, E positive controls)."""
    cases = [c for c in cases if c["id"] not in exclude]
    negative: dict[str, list[str]] = {}
    for c in cases:
        if c["expected"] == "not_supported":
            negative.setdefault(_point_key(c), []).append(c["id"])
    pos_w = [
        c["id"]
        for c in cases
        if c["expected"] == "supported" and c["kind"] != "window" and _point_key(c) not in negative
    ]
    pos_e = [c["id"] for c in cases if c["expected"] == "supported" and c["kind"] == "window"]
    return negative, pos_w, pos_e


def score_pass(
    cases: list[dict[str, Any]],
    w: dict[str, SecondJudgeVerdict],
    c: dict[str, SecondJudgeVerdict],
    exclude: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Gate A on W (whole points), gate B on C (labelled clauses) (plan KTD4); unsure counts as
    not kept."""
    negative, pos_w, pos_e = controls(cases, exclude)
    keeps = {cid for cid, v in w.items() if v.verdict == "keep"}
    dangerous = sorted(p for p, ids in negative.items() if keeps & set(ids))
    kept = [cid for cid in pos_w + pos_e if c.get(cid) and c[cid].verdict == "keep"]
    total = len(pos_w) + len(pos_e)
    window_cases = [c["id"] for c in cases if c["kind"] == "window" and c["id"] not in exclude]
    return {
        "gate_a": {
            "points": len(negative),
            "kept_unsupported": dangerous,
            "passed": not dangerous,
        },
        "gate_b": {
            "controls": total,
            "kept": len(kept),
            "missed": sorted(set(pos_w + pos_e) - set(kept)),
            # the gate is set on the full set; without some cases it is reported, not decided
            "required": None if exclude else GATE_B_MIN,
            "passed": None if exclude else len(kept) >= GATE_B_MIN,
        },
        "window_kept_in_w": sum(1 for cid in window_cases if cid in keeps),
        "window_cases": len(window_cases),
    }
