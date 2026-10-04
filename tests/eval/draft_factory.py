"""Synthetic golden-case drafts for the review, publish and verify tests (no LLM, no IA)."""

from __future__ import annotations

import copy
from typing import Any

import yaml

from womm.eval.drafting import GoldenDraft

KEYS = [f"data_act/proposal/art/{n}" for n in (23, 24, 25, 26, 27, 29)]
SCENARIO = "eval_cloud_switching"


def _judge(overall: str = "agree") -> dict:
    verdict = {"agree": "agree", "disagree": "disagree", "uncertain": "unknown"}[overall]
    return {
        "anchor_faithfulness": {"verdict": "agree", "reason": "r"},
        "derivability": {"verdict": "agree", "reason": "r"},
        "category": {"verdict": verdict, "reason": "r"},
        "overall": overall,
    }


def _common(n: int, decision: str, overall: str, audit: bool, origin: str, flags=None) -> dict:
    return {
        "provision_keys": [KEYS[n % len(KEYS)]],
        "ia_section": "6.2.3. Intervention on widget services",
        "ia_anchor": f"widget providers would face cost number {n} under the option",
        "anchor": {"status": "verified", "against": "ia"},
        "category": "compliance_cost",
        "derivability": {"verdict": "yes", "reason": "r"},
        "flags": flags or [],
        "judge": _judge(overall),
        "provenance": {
            "origin": origin,
            "status": "llm_judged" if decision == "auto_accepted" else "needs_human",
            "drafting_model": "model-x",
        },
        "review": {"decision": decision, "audit": audit, "reviewer": None, "note": None},
    }


def impact(n: int, decision="auto_accepted", overall="agree", audit=False, flags=None) -> dict:
    return {
        "expected_id": f"c90_e{n:02d}",
        "affected_actor": f"Actor {n}",
        "mechanism": f"Mechanism {n}",
        "impact": f"Impact {n}",
        **_common(n, decision, overall, audit, "llm_drafted", flags),
    }


def omission(n: int, decision="auto_accepted", overall="agree", audit=False) -> dict:
    return {
        "omission_id": f"c90_o{n:02d}",
        "description": f"Omission {n}",
        "source": "ia",
        **_common(n, decision, overall, audit, "llm_drafted"),
    }


def candidate(n: int, decision="auto_accepted", overall="agree", audit=False) -> dict:
    return {
        "candidate_id": f"c90_m{n:02d}",
        "affected_actor": f"Candidate actor {n}",
        "mechanism": f"Candidate mechanism {n}",
        "impact": f"Candidate impact {n}",
        "why_missing": "not covered",
        **_common(n, decision, overall, audit, "llm_recall"),
    }


def draft_dict(split: str = "train", case_id: str = "case_90_widget_switching") -> dict[str, Any]:
    """Four auto-accepted impacts, one disagree impact, one audited impact, one omission and
    two candidates: the items needing a human are e05 (judge), e06 (audit), m01 and m02."""
    impacts = [impact(n) for n in range(1, 5)]
    impacts.append(impact(5, decision="pending", overall="disagree"))
    impacts.append(impact(6, decision="pending", audit=True))
    return {
        "case_id": case_id,
        "scenario_id": SCENARIO,
        "fixture": "data_act",
        "split": split,
        "ia_reference": "Impact assessment accompanying the proposal; sections: 6.2.3.",
        "notes": "",
        "provenance": {
            "tool": "scripts/draft_golden_case.py",
            "drafted_on": "2026-10-04",
            "drafting_model": "model-x",
            "roles": {},
            "prompt_hashes": {},
            "ia_sections": ["6.2.3."],
            "rsb_status": "none",
            "audit": {"seed": 1, "rate": 0.2, "eligible": 5, "sampled": ["c90_e06"]},
        },
        "stats": {
            "items": 9,
            "expected_impacts": 6,
            "important_omissions": 1,
            "possibly_missing": 2,
            "anchors_verified": 9,
            "judge_agree": 8,
            "judge_disagree": 1,
            "judge_uncertain": 0,
            "auto_accepted": 5,
            "pending": 4,
            "audited": 1,
        },  # fmt: skip
        "expected_impacts": impacts,
        "important_omissions": [omission(1)],
        "possibly_missing": [candidate(1, decision="pending"), candidate(2)],
    }


def decide(data: dict, item_id: str, decision: str, reviewer="octo-cat", note=None, **edit):
    """Set a human decision on one item of a draft dict (returns a new dict)."""
    data = copy.deepcopy(data)
    for section in ("expected_impacts", "important_omissions", "possibly_missing"):
        for item in data[section]:
            ident = item.get("expected_id") or item.get("omission_id") or item.get("candidate_id")
            if ident == item_id:
                item.update(edit)
                item["review"].update(decision=decision, reviewer=reviewer, note=note)
                return data
    raise KeyError(item_id)


def fully_decided(split: str = "train") -> dict:
    """``draft_dict`` with every item that needs a human decided."""
    data = draft_dict(split)
    data = decide(data, "c90_e05", "edited", impact="Impact 5, corrected")
    data = decide(data, "c90_e06", "verified")
    data = decide(data, "c90_m01", "verified")
    return decide(data, "c90_m02", "rejected", note="already covered by c90_e01")


def to_draft(data: dict) -> GoldenDraft:
    return GoldenDraft.model_validate(data)


def write(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
