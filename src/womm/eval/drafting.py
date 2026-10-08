"""LLM drafting of golden cases from a cached impact assessment (U4, plan Revision 2026-10-04).

Pipeline, all through the WOMM backend interface (roles in ``evals/drafting.yaml``):

1. **Draft** (role ``drafter``): 5-10 expected impacts at actor x mechanism level plus important
   omissions, each with an IA anchor quote, a category, a derivability pre-check and provision
   keys.
2. **Recall** (role ``recall``): a second pass lists ``possibly_missing`` candidates, anchored.
3. **Deterministic checks**: every anchor is verified with ``womm.citations.match_quote`` against
   the cached IA text (or the RSB text for ``source: rsb`` omissions); provision keys must be in
   the case's scenario. Failures are flagged, never dropped.
4. **Judges**: three isolated calls, one per dimension (``judge_anchor``,
   ``judge_derivability``, ``judge_category``). Each sees only what its dimension needs and may
   answer ``unknown``. Per item: any ``disagree`` -> disagree, all ``agree`` -> agree, else
   uncertain.
5. **Review blocks**: an item judged ``agree`` with no flags is ``auto_accepted``
   (``provenance.status: llm_judged``); everything else is ``pending`` for a human. A random
   20% (``AUDIT_RATE``, pinned) of the auto-accepted impacts and omissions is marked
   ``audit: true`` and set back to ``pending``. ``possibly_missing`` candidates always go to a
   human, so they are never audit-eligible (``stats.human_decisions_needed`` counts the pending
   items plus every candidate). The seed is ``audit_seed_for(case_id)``; only tests
   may pass another one, and such a draft is marked ``non_publishable_test_seed`` and refused by
   the review gate.
6. **Integrity record**: ``provenance.item_digests`` holds, per item, a sha256 over the canonical
   JSON of its tool-written fields (``item_digest``), and ``provenance.audit.eligible_ids`` the
   audit-eligible ids. The review gate recomputes both and the audit sample, so a tool-written
   field changed on an ``auto_accepted`` or ``verified`` item, or an edited sample, fails CI.

``write_draft`` puts train/val drafts in ``evals/golden/drafts/`` and holdout drafts only under
``.cache/`` (gitignored); it refuses a holdout path anywhere else. Callers run holdout drafting
inside ``langsmith.tracing_context(enabled=False)`` (``scripts/draft_golden_case.py`` does).

No IA or RSB identifier is drafted: ``scrub_identifiers`` replaces the case's own identifiers
(from the local IA index) in every drafted string. A train/val draft then gets ``review_links``,
public EUR-Lex links to its IA and proposal for reviewers (``womm.eval.draft_edits``); they are
not item content, so they are not digested. A holdout draft never carries them.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import math
import random
import re
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import yaml
from pydantic import BaseModel, Field, model_validator

from womm.citations import match_quote, normalize
from womm.config import REPO_ROOT
from womm.data.fixtures import Fixture
from womm.data.ia_index import IaRecord
from womm.llm.base import LLMBackend
from womm.models.base import StrictModel
from womm.models.regulation import Scenario
from womm.models.system_version import RoleConfig

DRAFTS_DIR = REPO_ROOT / "evals" / "golden" / "drafts"
CACHE_DIR = REPO_ROOT / ".cache"
HOLDOUT_DRAFTS_DIR = CACHE_DIR / "drafts"
DEFAULT_CONFIG = REPO_ROOT / "evals" / "drafting.yaml"
EVALS_DIR = REPO_ROOT / "evals"
TOOL = "scripts/draft_golden_case.py"
# Share of auto-accepted impacts and omissions re-checked by a human. Pinned: not configurable,
# and the review gate refuses a draft whose recorded rate differs.
AUDIT_RATE = 0.2
# Public EUR-Lex page of a document by CELEX number; what reviewers of a train/val draft open.
EURLEX_URL = "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:"
EURLEX_LINK = r"^https://eur-lex\.europa\.eu/legal-content/EN/TXT/\?uri=CELEX:[0-9A-Za-z%]+$"

Category = Literal[
    "compliance_cost",
    "administrative_burden",
    "public_enforcement_cost",
    "market_competition",
    "innovation_investment",
    "sme_specific",
    "consumers_users",
    "fundamental_rights",
    "international",
    "social_environmental",
    "other",
]
CATEGORY_GUIDE = {
    "compliance_cost": (
        "substantive costs of meeting the requirements themselves: technical and organisational "
        "measures, equipment, and staff for meeting requirements"
    ),
    "administrative_burden": (
        "costs of information obligations: familiarisation with the new rules, information, "
        "reporting and record-keeping duties, documentation"
    ),
    "public_enforcement_cost": "costs for public authorities, supervision and enforcement",
    "market_competition": "market structure, switching, lock-in, bargaining power, prices",
    "innovation_investment": "innovation, investment, new services, data value creation",
    "sme_specific": "effects specific to SMEs or micro-enterprises (incl. exemptions)",
    "consumers_users": "effects on consumers, end users and their rights or choices",
    "fundamental_rights": "privacy, data protection, other fundamental rights",
    "international": "third countries, international trade, foreign access",
    "social_environmental": "employment, social and environmental effects",
    "other": "none of the above fits",
}
Derivable = Literal["yes", "partly", "no"]
JudgeVerdict = Literal["agree", "disagree", "unknown"]
Overall = Literal["agree", "disagree", "uncertain"]
Decision = Literal["pending", "auto_accepted", "verified", "edited", "rejected", "unclear"]
Split = Literal["train", "val", "holdout"]
DIMENSIONS = ("anchor_faithfulness", "derivability", "category")
JUDGE_ROLES = {
    "anchor_faithfulness": "judge_anchor",
    "derivability": "judge_derivability",
    "category": "judge_category",
}


class DraftingError(RuntimeError):
    pass


# ----------------------------------------------------------------------------- config


class DraftingRoles(StrictModel):
    drafter: RoleConfig
    recall: RoleConfig
    judge_anchor: RoleConfig
    judge_derivability: RoleConfig
    judge_category: RoleConfig

    def as_dict(self) -> dict[str, RoleConfig]:
        return {name: getattr(self, name) for name in type(self).model_fields}


class DraftingConfig(StrictModel):
    roles: DraftingRoles
    max_parallel_llm_calls: int = Field(default=3, ge=1)
    context_chars: int = Field(default=900, ge=100, description="Anchor context per side.")
    prompts: dict[str, str] = Field(default_factory=dict, exclude=True)
    prompt_hashes: dict[str, str] = Field(default_factory=dict)


def load_drafting_config(
    path: Path = DEFAULT_CONFIG, repo_root: Path = REPO_ROOT
) -> DraftingConfig:
    """The drafting roles plus their prompt texts (snapshotted) and hashes."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = DraftingConfig.model_validate(raw)
    prompts, hashes = {}, {}
    for role in config.roles.as_dict().values():
        file = repo_root / role.prompt
        if not file.is_file():
            raise DraftingError(f"prompt file not found: {role.prompt}")
        data = file.read_bytes()
        prompts[role.prompt] = data.decode("utf-8")
        hashes[role.prompt] = hashlib.sha256(data).hexdigest()[:16]
    return config.model_copy(update={"prompts": prompts, "prompt_hashes": hashes})


# ----------------------------------------------------------------------------- LLM schemas


class Derivability(StrictModel):
    verdict: Derivable = Field(
        description="Can the actor and mechanism be derived from the scenario provisions' text "
        "alone (without the IA)? yes / partly / no."
    )
    reason: str


class LlmImpact(StrictModel):
    affected_actor: str
    mechanism: str
    impact: str
    provision_keys: list[str] = Field(min_length=1)
    ia_section: str = Field(description="Heading of the IA section the anchor comes from.")
    ia_anchor: str = Field(description="Verbatim, contiguous quote (>= 6 words) from the IA.")
    category: Category
    derivability: Derivability


class LlmOmission(StrictModel):
    description: str
    provision_keys: list[str] = Field(min_length=1)
    source: Literal["ia", "rsb"] = Field(description="rsb if the point comes from the RSB text.")
    ia_section: str
    ia_anchor: str = Field(description="Verbatim quote from the IA, or the RSB text if rsb.")
    category: Category
    derivability: Derivability


class DraftOutput(StrictModel):
    expected_impacts: list[LlmImpact]
    important_omissions: list[LlmOmission]


class LlmCandidate(LlmImpact):
    why_missing: str = Field(description="Why the draft does not already cover this.")


class RecallOutput(StrictModel):
    possibly_missing: list[LlmCandidate]


class JudgeItemVerdict(StrictModel):
    item_id: str
    verdict: JudgeVerdict
    reason: str


class JudgeOutput(StrictModel):
    verdicts: list[JudgeItemVerdict]


# ----------------------------------------------------------------------------- draft file


class AnchorCheck(StrictModel):
    status: Literal["verified", "anchor_not_found", "too_short", "too_fragmented"]
    against: Literal["ia", "rsb"]


class DimensionVerdict(StrictModel):
    verdict: JudgeVerdict
    reason: str


class JudgeBlock(StrictModel):
    anchor_faithfulness: DimensionVerdict
    derivability: DimensionVerdict
    category: DimensionVerdict
    overall: Overall


class ItemProvenance(StrictModel):
    origin: Literal["llm_drafted", "llm_recall", "human_added"]
    status: Literal[
        "llm_judged", "needs_human", "human_verified", "human_edited", "human_confirmed_candidate"
    ]
    drafting_model: str
    raised_by: str | None = Field(
        default=None,
        description="Who raised a human_added item (an analyst); may not be its reviewer.",
    )
    feedback_id: str | None = Field(
        default=None,
        description="The analyst_feedback row a human_added item was staged from.",
    )
    analyst_digest: str | None = Field(
        default=None,
        description="sha256 binding raised_by and feedback_id to the item (``analyst_digest``).",
    )


class Review(StrictModel):
    decision: Decision = "pending"
    audit: bool = False
    reviewer: str | None = None
    note: str | None = None


class _DraftItem(StrictModel):
    provision_keys: list[str]
    ia_section: str
    ia_anchor: str
    anchor: AnchorCheck
    category: Category
    derivability: Derivability
    flags: list[str] = Field(default_factory=list)
    judge: JudgeBlock
    provenance: ItemProvenance
    review: Review

    @property
    def item_id(self) -> str:
        raise NotImplementedError


class DraftImpact(_DraftItem):
    expected_id: str
    affected_actor: str
    mechanism: str
    impact: str

    @property
    def item_id(self) -> str:
        return self.expected_id


class DraftOmission(_DraftItem):
    omission_id: str
    description: str
    source: Literal["ia", "rsb"]

    @property
    def item_id(self) -> str:
        return self.omission_id


class DraftCandidate(_DraftItem):
    candidate_id: str
    affected_actor: str
    mechanism: str
    impact: str
    why_missing: str

    @property
    def item_id(self) -> str:
        return self.candidate_id


class AuditSample(StrictModel):
    seed: int
    rate: float
    eligible: int = Field(description="Auto-accepted impacts and omissions before sampling.")
    eligible_ids: list[str] = Field(description="Their ids; the sample is drawn from these.")
    sampled: list[str]
    non_publishable_test_seed: bool = Field(
        default=False,
        description="True when a test passed its own seed; the review gate refuses such a draft.",
    )


class DraftProvenance(StrictModel):
    tool: str = TOOL
    drafted_on: dt.date
    drafted_by: str | None = Field(
        default=None, description="Who ran the drafting tool; may not be a named reviewer."
    )
    drafting_model: str
    roles: dict[str, dict[str, str]] = Field(description="role -> {backend, model, prompt}")
    prompt_hashes: dict[str, str]
    ia_sections: list[str]
    rsb_status: str
    audit: AuditSample
    item_digests: dict[str, str] = Field(
        description="item id -> sha256 of its tool-written fields (``item_digest``)."
    )


class DraftStats(StrictModel):
    items: int
    expected_impacts: int
    important_omissions: int
    possibly_missing: int
    anchors_verified: int
    judge_agree: int
    judge_disagree: int
    judge_uncertain: int
    auto_accepted: int
    pending: int
    audited: int
    # Pending items plus every possibly_missing candidate (a human decides those anyway).
    human_decisions_needed: int


class ReviewLinks(StrictModel):
    """Where a reviewer reads the evidence: the IA and the proposal on EUR-Lex. Train/val only."""

    ia: str = Field(pattern=EURLEX_LINK)
    proposal: str = Field(pattern=EURLEX_LINK)
    ia_parts: int = Field(default=1, ge=1, description="The IA's parts (documents) on EUR-Lex.")
    anchor_parts: dict[str, int] = Field(
        default_factory=dict, description="item id -> IA part holding its anchor (multi-part IA)."
    )


class GoldenDraft(StrictModel):
    case_id: str = Field(pattern=r"^case_\d{2,}_[a-z0-9_]+$")
    scenario_id: str
    fixture: str
    split: Split
    ia_reference: str
    notes: str = ""
    review_links: ReviewLinks | None = None
    provenance: DraftProvenance
    stats: DraftStats
    expected_impacts: list[DraftImpact]
    important_omissions: list[DraftOmission] = Field(default_factory=list)
    possibly_missing: list[DraftCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_links_on_holdout(self) -> GoldenDraft:
        if self.split == "holdout" and self.review_links is not None:
            raise ValueError("a holdout draft never carries review_links (IA identifiers)")
        return self

    def items(self) -> list[_DraftItem]:
        return [*self.expected_impacts, *self.important_omissions, *self.possibly_missing]


# ----------------------------------------------------------------------------- helpers


def case_prefix(case_id: str) -> str:
    m = re.match(r"^case_(\d+)_", case_id)
    return f"c{m.group(1)}" if m else case_id


def check_anchor(quote: str, text: str, against: Literal["ia", "rsb"]) -> AnchorCheck:
    reason = match_quote(quote, text) if text else "not_found"
    status = {"ok": "verified", "not_found": "anchor_not_found"}.get(reason, reason)
    return AnchorCheck(status=status, against=against)  # type: ignore[arg-type]


def eurlex_url(celex: str) -> str:
    """EUR-Lex page of a CELEX number, percent-encoded (``52021SC0396(01)``)."""
    return EURLEX_URL + quote(celex, safe="")


def anchor_context(quote: str, text: str, chars: int) -> str | None:
    """The normalised IA text around the anchor's first segment, or None if absent."""
    hay = normalize(text)
    first = re.split(r"\[?\s*\.{3,}\s*\]?", normalize(quote))[0].strip(" .,;:")
    pos = hay.find(first) if first else -1
    if pos < 0:
        return None
    start, end = max(0, pos - chars), min(len(hay), pos + len(first) + chars)
    return ("..." if start else "") + hay[start:end] + ("..." if end < len(hay) else "")


def auto_status(item: _DraftItem) -> Literal["auto_accepted", "pending"]:
    """The tool's own decision for an item: auto-accepted when every judge agreed and no
    deterministic check flagged it."""
    return "auto_accepted" if item.judge.overall == "agree" and not item.flags else "pending"


def audit_eligible(item: _DraftItem) -> bool:
    """Auto-accepted impacts and omissions; candidates always go to a human anyway."""
    return not isinstance(item, DraftCandidate) and auto_status(item) == "auto_accepted"


def tool_fields(item: _DraftItem) -> dict[str, Any]:
    """What the drafting tool wrote for an item: its content, checks, judge verdicts, origin and
    auto status. Not the review block or ``provenance.status``, which a human decision sets."""
    data = item.model_dump(mode="json")
    data.pop("review")
    # Fields added later are left out when unset, so digests of drafts written before they
    # existed hold.
    data["provenance"] = {
        k: v
        for k, v in data["provenance"].items()
        if k != "status" and not (k in _LATER_PROVENANCE and v is None)
    }
    data["auto_status"] = auto_status(item)
    return data


_LATER_PROVENANCE = ("raised_by", "feedback_id", "analyst_digest")


def analyst_digest(
    case_id: str, candidate_id: str, feedback_id: str | None, raised_by: str | None
) -> str:
    """sha256 over who raised a ``human_added`` item and the feedback row it came from, bound to
    the case and item id. Written at staging; the review gate recomputes it, so an edit to
    ``raised_by`` or ``feedback_id`` fails CI while the item's content stays editable. It is not
    a secret: the publish script also checks the stored mark when the database is at hand."""
    fields = {"case_id": case_id, "candidate_id": candidate_id,
              "feedback_id": feedback_id, "raised_by": raised_by}  # fmt: skip
    canonical = json.dumps(fields, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def item_digest(item: _DraftItem) -> str:
    """sha256 over the canonical JSON of ``tool_fields``."""
    canonical = json.dumps(
        tool_fields(item), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def require_under_cache(path: Path, what: str) -> Path:
    """``path`` resolved, refused unless it lies under ``.cache/`` (gitignored)."""
    resolved = path.resolve()
    if not resolved.is_relative_to(CACHE_DIR.resolve()):
        raise DraftingError(
            f"refusing {what} at {path}: holdout material lives only under .cache/ (gitignored)"
        )
    return resolved


def audit_seed_for(case_id: str) -> int:
    return int(hashlib.sha256(case_id.encode()).hexdigest()[:8], 16)


def draw_audit(item_ids: list[str], rate: float, seed: int) -> list[str]:
    """Deterministic sample: ceil(rate x n) of the sorted ids, by ``random.Random(seed)``."""
    if not item_ids or rate <= 0:
        return []
    k = min(len(item_ids), math.ceil(rate * len(item_ids) - 1e-9))
    return sorted(random.Random(seed).sample(sorted(item_ids), k))


def identifier_patterns(record: IaRecord | None) -> list[re.Pattern[str]]:
    """Regexes for the case's own IA and RSB identifiers (CELEX and SWD/SEC spellings). The
    proposal's COM number is public and not included."""
    if record is None:
        return []
    pats: list[re.Pattern[str]] = []
    if record.ia_celex:
        base = record.ia_celex.split("(")[0]
        pats.append(re.compile(re.escape(base)))
        m = re.fullmatch(r"5(\d{4})SC(\d{4})", base)
        if m:
            pats.append(re.compile(rf"(?:SWD|SEC)\s*\(\s*{m[1]}\s*\)\s*0*{int(m[2])}\b", re.I))
    if record.rsb_ref:
        m = re.search(r"\(\s*(\d{4})\s*\)\s*(\d+)", record.rsb_ref)
        if m:
            pats.append(re.compile(rf"SEC\s*\(\s*{m[1]}\s*\)\s*0*{int(m[2])}\b", re.I))
    return pats


def scrub_identifiers(value: Any, patterns: list[re.Pattern[str]]) -> tuple[Any, int]:
    """Replace identifier matches in every string of a JSON-like value; returns (value, hits)."""
    if not patterns:
        return value, 0
    if isinstance(value, str):
        hits = 0
        for p in patterns:
            value, n = p.subn("[identifier withheld]", value)
            hits += n
        return value, hits
    if isinstance(value, list):
        out, total = [], 0
        for v in value:
            v2, n = scrub_identifiers(v, patterns)
            out.append(v2)
            total += n
        return out, total
    if isinstance(value, dict):
        out_d, total = {}, 0
        for k, v in value.items():
            v2, n = scrub_identifiers(v, patterns)
            out_d[k] = v2
            total += n
        return out_d, total
    return value, 0


def _provisions_block(fixture: Fixture, scenario: Scenario) -> str:
    """The scenario's provisions in its after-version; works for a local holdout scenario that
    is not in the public fixture."""
    keys = set(scenario.provision_keys)
    after = fixture.version(scenario.after_version)
    return "\n\n".join(
        f"### {p.provision_key} (Article {p.article})\n{p.text}"
        for p in after.provisions
        if p.provision_key in keys
    )


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


# ----------------------------------------------------------------------------- pipeline


class DraftInputs(BaseModel):
    """Everything the pipeline reads; the IA text never leaves this object except as quotes."""

    model_config = {"arbitrary_types_allowed": True}

    case_id: str
    split: Split
    fixture: Any  # womm.data.fixtures.Fixture
    scenario: Scenario
    ia_full_text: str
    ia_sections_text: str
    ia_section_titles: list[str]
    rsb_text: str = ""
    rsb_status: str = ""
    ia_reference: str
    notes: str = ""
    ia_record: Any = None  # IaRecord | None, only for scrubbing identifiers
    test_audit_seed: int | None = None  # tests only: the draft is then marked non-publishable
    drafted_by: str | None = None
    today: dt.date | None = None


async def _call(
    backends: dict[str, LLMBackend],
    config: DraftingConfig,
    role_name: str,
    user: str,
    schema: type[BaseModel],
) -> Any:
    role = getattr(config.roles, role_name)
    backend = backends[role.backend]
    out, _ = await backend.call(role_name, config.prompts[role.prompt], user, schema, role)
    return out


def _drafter_input(inp: DraftInputs, provisions: str) -> str:
    return (
        f"Case: {inp.case_id}\nScenario: {inp.scenario.scenario_id}: {inp.scenario.description}\n"
        f"Allowed provision keys: {inp.scenario.provision_keys}\n\n"
        f"## Scenario provisions (the only legal text the assessed system sees)\n\n{provisions}\n\n"
        f"## Impact assessment sections (quote anchors verbatim from here)\n\n"
        f"{inp.ia_sections_text}\n\n"
        f"## RSB text ({inp.rsb_status or 'none'}; quote anchors of source: rsb omissions here)\n\n"
        f"{inp.rsb_text or '(none)'}\n"
    )


def _recall_input(inp: DraftInputs, provisions: str, draft: DraftOutput) -> str:
    covered = [
        {"affected_actor": i.affected_actor, "mechanism": i.mechanism}
        for i in draft.expected_impacts
    ]
    return (
        f"Case: {inp.case_id}\nAllowed provision keys: {inp.scenario.provision_keys}\n\n"
        f"## Already drafted impacts (actor x mechanism)\n\n{_dump(covered)}\n\n"
        f"## Scenario provisions\n\n{provisions}\n\n"
        f"## Impact assessment sections (quote anchors verbatim from here)\n\n"
        f"{inp.ia_sections_text}\n"
    )


def _claim(item: dict) -> dict:
    keys = ("affected_actor", "mechanism", "impact", "description")
    return {k: item[k] for k in keys if k in item}


def _judge_anchor_input(rows: list[dict], inp: DraftInputs, chars: int) -> str:
    payload = []
    for r in rows:
        text = inp.rsb_text if r["anchor"]["against"] == "rsb" else inp.ia_full_text
        ctx = anchor_context(r["ia_anchor"], text, chars)
        payload.append(
            {
                "item_id": r["id"],
                "claim": _claim(r),
                "anchor_quote": r["ia_anchor"],
                "anchor_in_context": ctx or "ANCHOR NOT FOUND in the source text",
            }
        )
    return "## Items\n\n" + _dump(payload)


def _judge_derivability_input(rows: list[dict], provisions: str) -> str:
    payload = [
        {
            "item_id": r["id"],
            "claim": _claim(r),
            "provision_keys": r["provision_keys"],
            "drafter_derivability": r["derivability"],
        }
        for r in rows
    ]
    return f"## Scenario provisions\n\n{provisions}\n\n## Items\n\n{_dump(payload)}"


def _judge_category_input(rows: list[dict]) -> str:
    payload = [
        {"item_id": r["id"], "claim": _claim(r), "proposed_category": r["category"]} for r in rows
    ]
    return f"## Categories\n\n{_dump(CATEGORY_GUIDE)}\n\n## Items\n\n{_dump(payload)}"


def _verdict_map(out: JudgeOutput, ids: list[str]) -> dict[str, DimensionVerdict]:
    got = {v.item_id: DimensionVerdict(verdict=v.verdict, reason=v.reason) for v in out.verdicts}
    missing = DimensionVerdict(verdict="unknown", reason="the judge returned no verdict")
    return {i: got.get(i, missing) for i in ids}


def overall(block: dict[str, DimensionVerdict]) -> Overall:
    verdicts = [block[d].verdict for d in DIMENSIONS]
    if "disagree" in verdicts:
        return "disagree"
    if all(v == "agree" for v in verdicts):
        return "agree"
    return "uncertain"


async def run_judges(
    rows: list[dict],
    inp: DraftInputs,
    provisions: str,
    config: DraftingConfig,
    backends: dict[str, LLMBackend],
) -> dict[str, dict[str, DimensionVerdict]]:
    """Three isolated judge calls (one per dimension), run concurrently."""
    ids = [r["id"] for r in rows]
    if not ids:
        return {}
    inputs = {
        "anchor_faithfulness": _judge_anchor_input(rows, inp, config.context_chars),
        "derivability": _judge_derivability_input(rows, provisions),
        "category": _judge_category_input(rows),
    }
    outs = await asyncio.gather(
        *(_call(backends, config, JUDGE_ROLES[d], inputs[d], JudgeOutput) for d in DIMENSIONS)
    )
    maps = {d: _verdict_map(o, ids) for d, o in zip(DIMENSIONS, outs, strict=True)}
    return {i: {d: maps[d][i] for d in DIMENSIONS} for i in ids}


def _row(
    item_id: str,
    item: BaseModel,
    inp: DraftInputs,
    keys: set[str],
    against: Literal["ia", "rsb"],
) -> dict:
    data = item.model_dump(mode="json")
    text = inp.rsb_text if against == "rsb" else inp.ia_full_text
    anchor = check_anchor(data["ia_anchor"], text, against)
    flags = []
    if anchor.status != "verified":
        flags.append(anchor.status)
    unknown = [k for k in data["provision_keys"] if k not in keys]
    if unknown:
        flags.append(f"unknown_provision_keys: {unknown}")
    if data["derivability"]["verdict"] == "no":
        flags.append("not_derivable_from_provisions")
    return data | {"id": item_id, "anchor": anchor.model_dump(), "flags": flags}


async def draft_case(
    inp: DraftInputs, config: DraftingConfig, backends: dict[str, LLMBackend]
) -> GoldenDraft:
    provisions = _provisions_block(inp.fixture, inp.scenario)
    keys = set(inp.scenario.provision_keys)
    draft: DraftOutput = await _call(
        backends, config, "drafter", _drafter_input(inp, provisions), DraftOutput
    )
    recall: RecallOutput = await _call(
        backends, config, "recall", _recall_input(inp, provisions, draft), RecallOutput
    )
    p = case_prefix(inp.case_id)
    rows: list[tuple[str, dict]] = []
    for n, item in enumerate(draft.expected_impacts, 1):
        rows.append(("impact", _row(f"{p}_e{n:02d}", item, inp, keys, "ia")))
    for n, item in enumerate(draft.important_omissions, 1):
        rows.append(("omission", _row(f"{p}_o{n:02d}", item, inp, keys, item.source)))
    for n, item in enumerate(recall.possibly_missing, 1):
        rows.append(("candidate", _row(f"{p}_m{n:02d}", item, inp, keys, "ia")))

    verdicts = await run_judges([r for _, r in rows], inp, provisions, config, backends)
    drafting_model = config.roles.drafter.model
    recall_model = config.roles.recall.model
    for _, r in rows:
        block = verdicts[r["id"]]
        r["judge"] = {d: block[d].model_dump() for d in DIMENSIONS} | {"overall": overall(block)}
        accepted = r["judge"]["overall"] == "agree" and not r["flags"]
        r["review"] = Review(decision="auto_accepted" if accepted else "pending").model_dump()

    test_seed = inp.test_audit_seed is not None
    seed = inp.test_audit_seed if test_seed else audit_seed_for(inp.case_id)
    eligible = [
        r["id"]
        for kind, r in rows
        if kind != "candidate" and r["review"]["decision"] == "auto_accepted"
    ]
    sampled = draw_audit(eligible, AUDIT_RATE, seed)
    for kind, r in rows:
        if r["id"] in sampled:
            r["review"] = {**r["review"], "decision": "pending", "audit": True}
        r["provenance"] = ItemProvenance(
            origin="llm_recall" if kind == "candidate" else "llm_drafted",
            status="llm_judged" if r["review"]["decision"] == "auto_accepted" else "needs_human",
            drafting_model=recall_model if kind == "candidate" else drafting_model,
        ).model_dump()

    patterns = identifier_patterns(inp.ia_record)
    impacts, omissions, candidates = [], [], []
    for kind, r in rows:
        item_id = r.pop("id")
        r, _ = scrub_identifiers(r, patterns)
        if kind == "impact":
            impacts.append(DraftImpact.model_validate(r | {"expected_id": item_id}))
        elif kind == "omission":
            omissions.append(DraftOmission.model_validate(r | {"omission_id": item_id}))
        else:
            candidates.append(DraftCandidate.model_validate(r | {"candidate_id": item_id}))

    all_items: list[_DraftItem] = [*impacts, *omissions, *candidates]
    judged = [i.judge.overall for i in all_items]
    stats = DraftStats(
        items=len(all_items),
        expected_impacts=len(impacts),
        important_omissions=len(omissions),
        possibly_missing=len(candidates),
        anchors_verified=sum(i.anchor.status == "verified" for i in all_items),
        judge_agree=judged.count("agree"),
        judge_disagree=judged.count("disagree"),
        judge_uncertain=judged.count("uncertain"),
        auto_accepted=sum(i.review.decision == "auto_accepted" for i in all_items),
        pending=sum(i.review.decision == "pending" for i in all_items),
        audited=len(sampled),
        human_decisions_needed=sum(
            i.review.decision == "pending" or isinstance(i, DraftCandidate) for i in all_items
        ),
    )
    roles = {
        name: {"backend": r.backend, "model": r.model, "prompt": r.prompt}
        for name, r in config.roles.as_dict().items()
    }
    meta, _ = scrub_identifiers(
        {
            "ia_reference": inp.ia_reference,
            "notes": inp.notes,
            "ia_sections": inp.ia_section_titles,
            "rsb_status": inp.rsb_status,
        },
        patterns,
    )
    return GoldenDraft(
        case_id=inp.case_id,
        scenario_id=inp.scenario.scenario_id,
        fixture=inp.fixture.regulation.regulation_id,
        split=inp.split,
        ia_reference=meta["ia_reference"],
        notes=meta["notes"],
        provenance=DraftProvenance(
            drafted_on=inp.today or dt.date.today(),
            drafted_by=inp.drafted_by,
            drafting_model=drafting_model,
            roles=roles,
            prompt_hashes=config.prompt_hashes,
            ia_sections=meta["ia_sections"],
            rsb_status=meta["rsb_status"],
            audit=AuditSample(
                seed=seed,
                rate=AUDIT_RATE,
                eligible=len(eligible),
                eligible_ids=sorted(eligible),
                sampled=sampled,
                non_publishable_test_seed=test_seed,
            ),
            item_digests={i.item_id: item_digest(i) for i in all_items},
        ),
        stats=stats,
        expected_impacts=impacts,
        important_omissions=omissions,
        possibly_missing=candidates,
    )


# ----------------------------------------------------------------------------- output


LEGACY_DRAFT_HEADER = (
    "# DRAFT golden case written by scripts/draft_golden_case.py; not scored until published.\n"
    "# Review (docs/eval/golden-review-guide.md): decide every item whose review.decision is\n"
    "# 'pending' (judge disagree/uncertain, a deterministic flag, or audit: true) and every\n"
    "# possibly_missing candidate as verified / edited / rejected / unclear, and set\n"
    "# review.reviewer to your GitHub username. Other auto-accepted items need no decision.\n"
    "# Do not change tool-written fields of an item you keep as auto_accepted or verified (CI\n"
    "# checks provenance.item_digests); the drafter (provenance.drafted_by) cannot review.\n"
    "# IA identifiers are kept in the gitignored evals/private/ia_index.yaml, never here.\n"
)
DRAFT_HEADER = (
    "# DRAFT golden case written by scripts/draft_golden_case.py; not scored until published.\n"
    "# Reviewer? Start with docs/eval/reviewer-quickstart.md. The helper lists what to decide:\n"
    "#   uv run python scripts/review_draft.py <this file>\n"
    "# Decide every item whose review.decision is 'pending' and every possibly_missing candidate\n"
    "# as verified / edited / rejected / unclear, with your GitHub username in review.reviewer.\n"
    "# Other auto-accepted items need no decision. Do not change tool-written fields of an item\n"
    "# you keep as auto_accepted or verified (CI checks provenance.item_digests); the drafter\n"
    "# (provenance.drafted_by) cannot review. review_links opens the public IA and proposal on\n"
    "# EUR-Lex (train/val only); holdout identifiers never appear in a tracked file.\n"
)


def draft_path(case_id: str, split: Split, out_dir: Path | None = None) -> Path:
    """Where a draft goes; a holdout draft anywhere but under .cache/ is refused."""
    directory = out_dir or (HOLDOUT_DRAFTS_DIR if split == "holdout" else DRAFTS_DIR)
    path = (directory / f"{case_id}.yaml").resolve()
    if split == "holdout":
        if path.is_relative_to(EVALS_DIR.resolve()):
            raise DraftingError(
                f"refusing to write holdout draft {case_id} under evals/ ({path}); holdout "
                "drafts go only to .cache/drafts/"
            )
        require_under_cache(path, f"holdout draft {case_id}")
    return path


ITEM_KEY_ORDER = (
    "expected_id", "omission_id", "candidate_id", "affected_actor", "mechanism", "impact",
    "description", "source", "why_missing", "provision_keys", "ia_section", "ia_anchor", "anchor",
    "category", "derivability", "flags", "judge", "provenance", "review",
)  # fmt: skip


def _reviewer_order(item: dict) -> dict:
    """Id and claim first, then evidence, checks and the review block."""
    return {k: item[k] for k in ITEM_KEY_ORDER if k in item} | item


def write_draft(draft: GoldenDraft, out_dir: Path | None = None) -> Path:
    path = draft_path(draft.case_id, draft.split, out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = draft.model_dump(mode="json")
    if data["review_links"] is None:
        del data["review_links"]
    for section in ("expected_impacts", "important_omissions", "possibly_missing"):
        data[section] = [_reviewer_order(i) for i in data[section]]
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(DRAFT_HEADER + body, encoding="utf-8")
    return path


def load_draft(path: Path) -> GoldenDraft:
    return GoldenDraft.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
