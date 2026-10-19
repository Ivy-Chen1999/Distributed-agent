"""The full-text key check (plan 2026-10-16-001, F2): is each claim of an answer-key point stated
by its sources?

The model roles have no tools, so the source reaches them through Python:

1. ``key_split`` splits the point into claims (or takes the clauses given) and writes search
   phrasings for each.
2. Passage retrieval (BM25 over ``womm.eval.key_sources`` passages; case- and inflection-
   normalised, and a capitalised "Commission" in mid-sentence never matches "commission")
   returns the top passages per claim. The anchor's own passage is always included.
3. ``key_judge`` judges each claim on its passages: ``supported`` (a verbatim quote, typed
   ``ia``, ``law`` or ``inference``), ``not_found`` or ``cant_tell``. Python then checks every
   quote is in the passage it cites; a quote that is not becomes ``cant_tell``.

A point anchored on the board's opinion can only be supported by the opinion: when the opinion is
not cached, its claims are ``cant_tell`` without a call (the IA's procedural annex is background,
never the board's words). A verdict never edits a draft; edits go to review (plan F5).

``check_version`` hashes everything that decides a verdict (the two prompts, this module, the
sources module, the retrieval settings, the models): a change to any needs a new acceptance run
(F4b).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from womm.eval import key_sources
from womm.eval.drafting import DraftingConfig
from womm.eval.key_sources import Passage, Sources
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel

SPLIT_ROLE, JUDGE_ROLE = "key_check_split", "key_check_judge"
MIN_QUOTE_WORDS = 6  # an empty or token quote never supports a claim
FUSE_DEPTH = 10  # each search's top passages merged by reciprocal rank
RRF_K = 60
MAX_PER_CLAIM = 8
JUDGE_VOTES = 3  # independent judgements per point; they must agree (owner decision 2026-10-17)
BM25_K1, BM25_B = 1.4, 0.75
RETRIEVAL = {
    "prefix": 6,
    "fuse_depth": FUSE_DEPTH,
    "rrf_k": RRF_K,
    "judge_votes": JUDGE_VOTES,
    "keep_each_search_best": True,
    "number_boost": True,
    "split_keeps_point_words": True,
    "quote_modality_rule": True,
    "max_per_claim": MAX_PER_CLAIM,
    "k1": BM25_K1,
    "b": BM25_B,
    "max_passage": key_sources.MAX_PASSAGE,
}
POINT_FIELDS = ("affected_actor", "mechanism", "impact", "description")

Verdict = Literal["supported", "not_found", "cant_tell"]


class KeyCheckError(ValueError):
    """The check could not run: nothing was judged."""


# ----------------------------------------------------------------------------- model schemas


class SplitClaim(StrictModel):
    id: str
    text: str
    searches: list[str]


class SplitOutput(StrictModel):
    claims: list[SplitClaim]


class Evidence(StrictModel):
    passage: str
    quote: str
    type: Literal["ia", "law", "inference"]


Match = Literal["same", "different", "n/a"]
CHECKS = ("group", "item", "kind", "degree", "modality")


class ClaimVerdict(StrictModel):
    id: str
    # The claim against the quote, part by part; filled before the verdict.
    checks: dict[str, Match] = {}
    verdict: Verdict
    evidence: list[Evidence] = []
    reason: str


class JudgeOutput(StrictModel):
    claims: list[ClaimVerdict]


# ----------------------------------------------------------------------------- retrieval

_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d[\d.,%]*")
_SUFFIXES = ("ations", "ation", "ings", "ing", "ies", "ied", "es", "ed", "ly", "s")


PREFIX = 6  # stems are cut to this many letters, so "suspension" meets "suspend"


def _stem(word: str) -> str:
    for suf in _SUFFIXES:
        if len(word) > len(suf) + 3 and word.endswith(suf):
            word = word[: -len(suf)]
            break
    return word[:PREFIX]


def tokens(text: str) -> list[str]:
    """Normalised terms. A capitalised word that does not start a sentence keeps a mark, so
    "the Commission" never matches "commissions" (a fee); numbers keep their digits."""
    out = []
    for m in _TOKEN.finditer(text):
        raw = re.sub(r"['’]s?$", "", m.group(0)).strip(".,'’")  # possessive: "Commission's"
        if not raw:
            continue
        if raw[0].isdigit():
            out.append(raw.replace(",", ""))
            continue
        start = m.start() == 0 or re.search(
            r"[.!?:;(]\s*$", text[max(0, m.start() - 3) : m.start()]
        )
        word = _stem(raw.lower().replace("’", "'"))
        out.append(f"^{word}" if raw[0].isupper() and not start and word == "commis" else word)
    return out


@dataclass
class Index:
    passages: list[Passage]
    docs: list[Counter] = field(default_factory=list)
    df: Counter = field(default_factory=Counter)
    avg: float = 1.0

    def __post_init__(self) -> None:
        self.docs = [Counter(tokens(p.text)) for p in self.passages]
        for d in self.docs:
            self.df.update(d.keys())
        self.avg = sum(sum(d.values()) for d in self.docs) / max(1, len(self.docs))

    def search(self, query: str, k: int) -> list[int]:
        n = len(self.docs)
        q = set(tokens(query))
        scores = []
        for i, d in enumerate(self.docs):
            length = sum(d.values()) or 1
            s = 0.0
            for t in q:
                if t in d:
                    idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    tf = d[t]
                    s += (
                        idf
                        * tf
                        * (BM25_K1 + 1)
                        / (tf + BM25_K1 * (1 - BM25_B + BM25_B * length / self.avg))
                    )
            if s > 0:
                scores.append((s, i))
        return [i for _, i in sorted(scores, reverse=True)[:k]]


def _norm(text: str) -> str:
    return " ".join(
        text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"').split()
    ).lower()


# ----------------------------------------------------------------------------- the check


@dataclass(frozen=True)
class PointInput:
    """What the check reads for one point: its fields, anchor and where it is anchored."""

    key: str  # "<case>/<item>"
    fixture: str
    fields: dict[str, str]
    anchor: str
    anchored_on: Literal["ia", "rsb_opinion"]
    claims: tuple[str, ...] = ()  # the clauses to judge; empty: the check splits the point


def check_version(config: DraftingConfig) -> str:
    """A hash of everything that decides a verdict."""
    parts = []
    for role in (config.roles.key_split, config.roles.key_judge):
        if role is None:
            raise KeyCheckError("the drafting config has no key_split / key_judge role")
        parts.append(f"{role.model}:{config.prompt_hashes.get(role.prompt, '')}")
    for module in (Path(__file__), Path(key_sources.__file__)):
        parts.append(hashlib.sha256(module.read_bytes()).hexdigest())
    parts.append(json.dumps(RETRIEVAL, sort_keys=True))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _allowed(src: str, anchored_on: str) -> bool:
    return True if anchored_on == "ia" else src in ("rsb_opinion", "rsb_annex", "law")


def _retrieve(index: Index, point: PointInput, claim: SplitClaim) -> list[Passage]:
    """The anchor's passage, then the passages all searches rank highest together: each search's
    top ``FUSE_DEPTH`` merged by reciprocal rank, deduplicated, then cut. A passage several
    phrasings find outranks one found once, and a late phrasing's best passage is not crowded out
    by an early phrasing's tail. Retrieval only proposes candidates: whether a passage states the
    claim is the judge's call, on the passage's own words."""
    anchor = _norm(point.anchor)[:120]
    first = next(
        (i for i, p in enumerate(index.passages) if anchor and anchor in _norm(p.text)), None
    )
    fused: dict[int, float] = {}
    bests: list[int] = []
    for q in dict.fromkeys([claim.text, *claim.searches]):
        ranked = [
            i
            for i in index.search(q, FUSE_DEPTH)
            if _allowed(index.passages[i].source, point.anchored_on)
        ]
        if ranked and ranked[0] not in bests:
            bests.append(ranked[0])  # each phrasing's best passage is always kept
        for rank, i in enumerate(ranked):
            fused[i] = fused.get(i, 0.0) + 1.0 / (RRF_K + rank + 1)
    numbers = {t for t in tokens(claim.text) if t[0].isdigit()}
    if numbers:  # a passage that states the claim's figures is a strong candidate
        for i in fused:
            hit = numbers & set(index.docs[i])
            fused[i] += len(hit) / len(numbers) / (RRF_K + 1)
    order = sorted(fused, key=lambda i: (-fused[i], i))
    head = ([first] if first is not None else []) + [i for i in bests if i != first]
    picked = head + [i for i in order if i not in head]
    return [index.passages[i] for i in picked[:MAX_PER_CLAIM]]


# Words a claim may add to the point's own: joining words, never content or modality. Kept as
# tokens, so they compare with tokens().
JOINERS = frozenset(
    tokens(
        "a an the and or of to in for on by with as is are be this that these those it its their "
        "they which who from at than make makes cause causes lead leads result results "
        "impact assessment"  # "the IA" written out
    )
)
HEDGES = ("expect", "could", "may", "might", "likely", "would", "estimat", "potentia", "possib")


def invented(claim: str, point_words: set[str]) -> set[str]:
    """Content words in a claim that the point does not use (the split must not rewrite)."""
    return {w for w in tokens(claim) if w not in point_words and w not in JOINERS
            and not w[0].isdigit()}  # fmt: skip


def _hedged(text: str) -> bool:
    return any(t.startswith(HEDGES) for t in tokens(text))


def _agreed(cid: str, ballots: list[dict[str, ClaimVerdict]]) -> ClaimVerdict:
    """The verdict all independent judgements give, or ``cant_tell`` when they differ (or one
    gave none): a split vote goes to review instead of deciding."""
    got = [b.get(cid) for b in ballots]
    if all(g is not None for g in got) and len({g.verdict for g in got}) == 1:  # type: ignore[union-attr]
        return got[0]  # type: ignore[return-value]
    seen = ", ".join(g.verdict if g else "none" for g in got)
    return ClaimVerdict(id=cid, verdict="cant_tell", evidence=[],
                        reason=f"independent judgements differ ({seen})")  # fmt: skip


def _validated(
    v: ClaimVerdict, shown: dict[str, Passage], board: bool, claim_text: str = ""
) -> ClaimVerdict:
    """A supported verdict stands only if every quote is in the passage it cites, and, for a
    board point, at least one comes from the board's own opinion."""
    if v.verdict != "supported":
        return v
    checks = dict(v.checks)
    if any(_hedged(e.quote) for e in v.evidence) and not _hedged(claim_text):
        checks["modality"] = "different"  # the source predicts; the claim states it as fact
    off = sorted(k for k in CHECKS if checks.get(k) == "different")
    missing = [k for k in CHECKS if k not in checks]
    if off or missing:
        why = f"marked different: {', '.join(off)}" if off else f"no check of {', '.join(missing)}"
        return ClaimVerdict(id=v.id, verdict="cant_tell", evidence=v.evidence,
                            reason=f"supported but {why} ({v.reason})")  # fmt: skip
    bad = [
        e
        for e in v.evidence
        if e.passage not in shown
        or len(e.quote.split()) < MIN_QUOTE_WORDS
        or _norm(e.quote) not in _norm(shown[e.passage].text)
    ]
    if not v.evidence or bad:
        return ClaimVerdict(
            id=v.id,
            verdict="cant_tell",
            evidence=[],
            reason=f"quote not found in its passage ({v.reason})",
        )
    if board and not any(shown[e.passage].source == "rsb_opinion" for e in v.evidence):
        return ClaimVerdict(
            id=v.id,
            verdict="cant_tell",
            evidence=v.evidence,
            reason=f"no quote from the board's opinion ({v.reason})",
        )
    return v


async def check_point(
    point: PointInput,
    sources: Sources,
    config: DraftingConfig,
    backends: dict[str, LLMBackend],
    index: Index | None = None,
) -> dict[str, Any]:
    """Every claim of ``point`` with its verdict, evidence, the passages shown and the source
    digests; a board point without the board's opinion gets ``cant_tell`` for every claim."""
    split_role, judge_role = config.roles.key_split, config.roles.key_judge
    if split_role is None or judge_role is None:
        raise KeyCheckError("the drafting config has no key_split / key_judge role")
    board = point.anchored_on == "rsb_opinion"
    split_in = {"point": point.fields, "claims_to_check": list(point.claims) or None}
    point_words = set(tokens(" ".join(point.fields.values())))
    note, why = "", ""
    for _attempt in range(2):  # one retry when the split rewrites the point
        split, _ = await backends[split_role.backend].call(
            SPLIT_ROLE,
            config.prompts[split_role.prompt],
            "## Point\n\n" + json.dumps(split_in, indent=1, ensure_ascii=False) + note,
            SplitOutput,
            split_role,
        )
        claims = split.claims
        ids = [c.id for c in claims]
        if point.claims:
            break
        added = sorted({w for c in claims for w in invented(c.text, point_words)})
        if not claims:
            why = "the split returned no claims"
        elif len(set(ids)) != len(ids):
            why = "the split repeated a claim id"
        elif added:
            why = f"the split added words the point does not use: {', '.join(added)}"
            note = (f"\n\nYour last split added words the point does not use ({', '.join(added)})."
                    " Use only the point's own words.")  # fmt: skip
        else:
            why = ""
            break
    if why:
        return {
            "key": point.key,
            "sources": sources.digests,
            "opinion_missing": sources.opinion_missing,
            "claims": [],
            "incomplete": why,
        }
    if point.claims:  # the clauses given are judged as given
        claims = [
            SplitClaim(
                id=f"c{n}", text=t, searches=(claims[n - 1].searches if n <= len(claims) else [])
            )
            for n, t in enumerate(point.claims, 1)
        ]
    result: dict[str, Any] = {
        "key": point.key,
        "sources": sources.digests,
        "opinion_missing": sources.opinion_missing,
        "claims": [],
    }
    if board and sources.opinion_missing:
        result["claims"] = [
            {
                "id": c.id,
                "text": c.text,
                "verdict": "cant_tell",
                "evidence": [],
                "reason": "the board's opinion is not cached; the procedural annex "
                "cannot stand in for it",
                "shown": [],
            }
            for c in claims
        ]
        return result
    index = index or Index(sources.passages)
    shown: dict[str, Passage] = {}
    per_claim = []
    for c in claims:
        ids = []
        for p in _retrieve(index, point, c):
            pid = next((k for k, v in shown.items() if v == p), f"p{len(shown) + 1}")
            shown[pid] = p
            ids.append(pid)
        per_claim.append(
            {
                "id": c.id,
                "claim": c.text,
                "passages": [
                    {
                        "id": i,
                        "source": shown[i].source,
                        "where": shown[i].where,
                        "section": shown[i].section,
                        "context_before": shown[i].before,
                        "text": shown[i].text,
                    }
                    for i in ids
                ],
            }
        )
    judge_in = {
        "anchored_on": point.anchored_on,
        "point_context": point.fields,
        "claims": per_claim,
    }
    user = "## Claims\n\n" + json.dumps(judge_in, indent=1, ensure_ascii=False)
    votes = await asyncio.gather(
        *(
            backends[judge_role.backend].call(
                JUDGE_ROLE, config.prompts[judge_role.prompt], user, JudgeOutput, judge_role
            )
            for _ in range(JUDGE_VOTES)
        )
    )
    texts = {c.id: c.text for c in claims}
    ballots = [
        {v.id: _validated(v, shown, board, texts.get(v.id, "")) for v in out.claims}
        for out, _ in votes
    ]
    got = {c.id: _agreed(c.id, ballots) for c in claims}
    for c, pc in zip(claims, per_claim, strict=True):
        v = got.get(c.id) or ClaimVerdict(
            id=c.id, verdict="cant_tell", reason="no verdict returned"
        )
        result["claims"].append(
            {
                "id": c.id,
                "text": c.text,
                "verdict": v.verdict,
                "reason": v.reason,
                "evidence": [
                    e.model_dump()
                    | {"where": shown[e.passage].where, "source": shown[e.passage].source}
                    for e in v.evidence
                    if e.passage in shown
                ],
                "shown": [p["where"] for p in pc["passages"]],
            }
        )
    return result


async def check_points(
    points: list[PointInput],
    config: DraftingConfig,
    backends: dict[str, LLMBackend],
    ia_root: Path | None = None,
) -> list[dict[str, Any]]:
    """``check_point`` over many points, sources loaded and indexed once per fixture."""
    loaded: dict[str, tuple[Sources, Index]] = {}
    for p in points:
        if p.fixture not in loaded:
            s = key_sources.load_sources(p.fixture, ia_root)
            loaded[p.fixture] = (s, Index(s.passages))
    sem = asyncio.Semaphore(config.max_parallel_llm_calls)

    async def one(p: PointInput) -> dict[str, Any]:
        async with sem:
            s, idx = loaded[p.fixture]
            return await check_point(p, s, config, backends, idx)

    return list(await asyncio.gather(*(one(p) for p in points)))
