"""API with a scripted, content-driven fake LLM for the console's Playwright suite (web/e2e).

Run with `uvicorn --factory womm.api.e2e:create_e2e_app`. Needs `WOMM_API_TOKEN` and
`DATABASE_URL` like the real API; nothing calls a real model.

Every call waits `WOMM_E2E_DELAY_S` seconds (default 0.4; experts wait a little longer each, so
their latencies differ) so the console can observe queued/running states. The outputs are built
from the request content, so each scenario produces its own findings with real quotes:

- planner: two focus areas over the provision keys in the diff;
- experts: two findings each, quoting whole sentences (>= 8 words) copied verbatim from the
  sources listed in the prompt, so the citation check passes. For `eval_sme_impacts` the findings
  of `docs/ui/sample_run.json` (a real run) are reused per agent;
- synthesis: one impact per provision key, chains between consecutive impacts, one disagreement
  between two agents, two open questions;
- ask: cites an impact and a finding of the dossier (covered); a question mentioning "weather"
  gets covered=false and no cites.

Failure modes are chosen per run by scenario, so one server covers every console state:

| scenario                          | behaviour                                                   |
|-----------------------------------|-------------------------------------------------------------|
| `eval_sme_impacts`                | succeeded, all quotes verified                              |
| `eval_provider_compliance_costs`  | succeeded, one stakeholder quote is fabricated -> "Quote    |
|                                   | not found" / evidence_unresolved open question              |
| `demo_penalties_amended`          | degraded: the `stakeholder` expert raises LLMError timeout  |

Environment overrides (apply to every run): `WOMM_E2E_FAIL_EXPERT=<id>` makes that expert time
out, `WOMM_E2E_FABRICATE_QUOTE=1` fabricates one quote in every scenario.

Besides `fake`, the same scripted backend is registered under the name `api`, so the console's
backend-per-role switch has a second option and a run with that override completes.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from womm.api.app import create_app
from womm.config import DEFAULT_SYSTEM_VERSION, REPO_ROOT, load_settings
from womm.data.fixtures import Fixture, load_fixture
from womm.llm.base import LLMError
from womm.llm.fake import FakeBackend
from womm.models.run import CallUsage
from womm.models.system_version import (
    RoleConfig,
    SystemVersion,
    build_system_version,
    load_system_version,
)

SAMPLE_RUN = REPO_ROOT / "docs" / "ui" / "sample_run.json"
DEGRADED_SCENARIO = "demo_penalties_amended"
DEGRADED_EXPERT = "stakeholder"
FABRICATED_SCENARIO = "eval_provider_compliance_costs"
FABRICATED_EXPERT = "stakeholder"
FABRICATED_QUOTE = (
    "Providers shall publish a quarterly weather report for every artificial intelligence system "
    "they place on the market"
)
MIN_QUOTE_WORDS = 8
MAX_QUOTE_WORDS = 45
SAMPLE_FINDINGS_PER_AGENT = 4
# Steps per script key: enough for any test session (the script is never exhausted).
STEPS = 100_000

ACTORS = {
    "legal": "providers of AI systems",
    "fiscal": "SMEs and start-ups",
    "stakeholder": "national competent authorities",
}
EXPERT_DELAY_FACTOR = {"legal": 1.0, "fiscal": 2.0, "stakeholder": 3.0}

_KEY = re.compile(r"provision_key=(\S+)")
_CHANGE = re.compile(r"^- \[(\w+)\] provision_key=(\S+) \(([^)]*)\)", re.M)
_SOURCE = re.compile(r'<source id="([^"]+)"[^>]*>\n(.*?)\n</source>', re.S)
_SENTENCE = re.compile(r"(?<=[.;:])\s+|\n+")


def fake_system_version() -> SystemVersion:
    """The baseline version with every role on the `fake` backend (as in the graph tests)."""
    base = load_system_version(DEFAULT_SYSTEM_VERSION, REPO_ROOT).spec
    data = base.model_dump()
    for role in ("planner", "synthesis", "judge"):
        data[role]["backend"] = "fake"
    data["experts"] = [{**e, "role": {**e["role"], "backend": "fake"}} for e in data["experts"]]
    data["router"]["mode"] = "shadow"
    data["name"] = f"{base.name}-e2e"
    data["description"] = "Playwright e2e: every role on the scripted fake backend."
    return build_system_version(type(base).model_validate(data), REPO_ROOT)


# ---------- parsing the prompts ----------


def _changes(user: str) -> list[dict[str, Any]]:
    """Changes from the expert prompt's index: kind, key and the before/after source ids."""
    out = []
    for kind, key, refs in _CHANGE.findall(user):
        sources = dict(re.findall(r"(before|after): Art [^,]+, source_id=([^;\s]+)", refs))
        out.append({"kind": kind, "key": key, **sources})
    return out


def _sources(user: str) -> dict[str, str]:
    return dict(_SOURCE.findall(user))


def _sentences(text: str) -> list[str]:
    """Whole sentences (or clauses) of a source, usable as verbatim quotes."""
    out = []
    for raw in _SENTENCE.split(text):
        s = raw.strip()
        s = re.sub(r"^(\(?[0-9a-z]{1,3}[.)]\s+)+", "", s)  # point numbers like "1." or "(a)"
        n = len(s.split())
        if MIN_QUOTE_WORDS <= n <= MAX_QUOTE_WORDS:
            out.append(s)
    return out


def scenario_for(keys: set[str], changes: list[dict[str, Any]], fixture: Fixture) -> str | None:
    """The scenario whose provision keys the prompt covers."""
    modified = any(c["kind"] == "modified" for c in changes)
    for sc in fixture.scenarios.values():
        if set(sc.provision_keys) == keys and bool(sc.before_version) == modified:
            return sc.scenario_id
    for sc in fixture.scenarios.values():
        if set(sc.provision_keys) == keys:
            return sc.scenario_id
    return None


# ---------- scripted roles ----------


def plan(system: str, user: str) -> dict:
    keys = list(dict.fromkeys(_KEY.findall(user)))
    if not keys:
        return {"focus_areas": []}
    half = max(1, (len(keys) + 1) // 2)
    groups = [keys[:half], keys[half:] or keys[:1]]
    return {
        "focus_areas": [
            {
                "provision_keys": g,
                "question": f"Who bears new obligations or costs under {', '.join(g)}?",
                "rationale": "Scripted e2e focus area.",
            }
            for g in groups
        ]
    }


def _sample_board() -> dict[str, list[dict]]:
    """A few real findings per agent from the sample run, spread over provision keys."""
    if not SAMPLE_RUN.is_file():
        return {}
    board = json.loads(SAMPLE_RUN.read_text(encoding="utf-8")).get("board", [])
    fields = ("provision_key", "affected_actor", "impact", "mechanism", "confidence")
    by_agent: dict[str, list[dict]] = {}
    for f in board:
        draft = {k: f[k] for k in fields} | {
            "evidence": [{"source_id": e["source_id"], "quote": e["quote"]} for e in f["evidence"]]
        }
        by_agent.setdefault(f["agent"], []).append(draft)
    out = {}
    for agent, drafts in by_agent.items():
        firsts = list({d["provision_key"]: d for d in reversed(drafts)}.values())[::-1]
        rest = [d for d in drafts if d not in firsts]
        out[agent] = (firsts + rest)[:SAMPLE_FINDINGS_PER_AGENT]
    return out


class Scripts:
    """Content-driven step callables for every role, bound to one fixture."""

    def __init__(self, fixture: Fixture) -> None:
        self.fixture = fixture
        self.sample = _sample_board()

    def fail_expert(self, scenario: str | None) -> str | None:
        forced = os.environ.get("WOMM_E2E_FAIL_EXPERT", "").strip()
        if forced:
            return forced
        return DEGRADED_EXPERT if scenario == DEGRADED_SCENARIO else None

    def fabricate(self, scenario: str | None, agent: str) -> bool:
        if agent != FABRICATED_EXPERT:
            return False
        return os.environ.get("WOMM_E2E_FABRICATE_QUOTE") == "1" or scenario == FABRICATED_SCENARIO

    def expert(self, agent: str, index: int) -> Callable[[str, str], Any]:
        def step(system: str, user: str) -> Any:
            changes = _changes(user)
            keys = {c["key"] for c in changes}
            scenario = scenario_for(keys, changes, self.fixture)
            if self.fail_expert(scenario) == agent:
                return LLMError("timeout", f"{agent} expert timed out (scripted e2e failure)")
            if scenario == "eval_sme_impacts" and self.sample.get(agent):
                findings = [dict(f) for f in self.sample[agent]]
            else:
                findings = self._findings(agent, index, changes, _sources(user))
            if findings and self.fabricate(scenario, agent):
                first = dict(findings[0])
                first["evidence"] = [
                    {"source_id": first["evidence"][0]["source_id"], "quote": FABRICATED_QUOTE}
                ]
                findings[0] = first
            return {"findings": findings}

        return step

    def _findings(
        self, agent: str, index: int, changes: list[dict], sources: dict[str, str]
    ) -> list[dict]:
        out = []
        for k in range(2):
            if not changes:
                break
            change = changes[(index + k) % len(changes)]
            sid = change.get("after") or change.get("before")
            sentences = _sentences(sources.get(sid, ""))
            if not sid or not sentences:
                continue
            quote = sentences[(index * 2 + k) % len(sentences)]
            area = change["key"].rsplit("/", 1)[-1].replace("_", " ")
            actor = ACTORS.get(agent, "affected operators")
            out.append(
                {
                    "provision_key": change["key"],
                    "affected_actor": actor,
                    "impact": f"{agent.capitalize()} reading: {actor} face new {area} duties "
                    f"({change['kind']} provision).",
                    "mechanism": f"The {change['kind']} {area} provision sets out: {quote[:80]}",
                    "evidence": [{"source_id": sid, "quote": quote}],
                    "confidence": round(0.9 - 0.1 * index - 0.05 * k, 2),
                }
            )
        return out

    @staticmethod
    def synthesis(system: str, user: str) -> dict:
        rows = json.loads(user.split("\n", 1)[1])
        by_key: dict[str, list[dict]] = {}
        for r in rows:
            by_key.setdefault(r["provision_key"], []).append(r)
        impacts = []
        for i, (key, fs) in enumerate(by_key.items(), 1):
            area = key.rsplit("/", 1)[-1].replace("_", " ").replace("sme", "SME")
            impacts.append(
                {
                    "impact_id": f"I{i}",
                    "summary": f"{area[0].upper()}{area[1:]}: {fs[0]['impact']}",
                    "finding_ids": [f["finding_id"] for f in fs],
                }
            )
        ids = [i["impact_id"] for i in impacts]
        chains = [
            {"impact_ids": ids[j : j + 2], "description": f"{ids[j]} leads to {ids[j + 1]}."}
            for j in range(min(2, len(ids) - 1))
        ]
        disagreements = []
        pair = _disagreeing_pair(rows)
        if pair:
            a, b = pair
            disagreements.append(
                {
                    "finding_ids": [a["finding_id"], b["finding_id"]],
                    "note": f"{a['agent'].capitalize()} and {b['agent']} read "
                    f"{a['provision_key']} differently.",
                }
            )
        questions = [
            {
                "finding_id": rows[0]["finding_id"] if rows else None,
                "question": "How will competent authorities staff the new supervision duties?",
            },
            {"finding_id": None, "question": "Which thresholds define a small-scale provider?"},
        ]
        return {
            "impacts": impacts,
            "chains": chains,
            "disagreements": disagreements,
            "open_questions": questions,
            "discarded": [],
        }

    @staticmethod
    def ask(system: str, user: str) -> dict:
        head, _, question = user.rpartition("\n\nQuestion: ")
        dossier = json.loads(head.removeprefix("Dossier:\n"))
        if "weather" in question.lower():
            return {
                "answer": "This dossier does not cover that. It only assesses the changed "
                "provisions of this run.",
                "cites": [],
                "covered": False,
            }
        impact = (dossier.get("impacts") or [{}])[0]
        finding = (impact.get("findings") or [{}])[0]
        cites = [c for c in (impact.get("impact_id"), finding.get("finding_id")) if c]
        return {
            "answer": f"{impact.get('impact_id', 'The dossier')}: {impact.get('summary', '')} "
            f"({finding.get('agent', 'an expert')} finding {finding.get('finding_id', '')}).",
            "cites": cites,
            "covered": True,
        }


def _disagreeing_pair(rows: list[dict]) -> tuple[dict, dict] | None:
    """Two findings of different agents, preferably on the same provision."""
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            if a["agent"] != b["agent"] and a["provision_key"] == b["provision_key"]:
                return a, b
    for i, a in enumerate(rows):
        for b in rows[i + 1 :]:
            if a["agent"] != b["agent"]:
                return a, b
    return None


def e2e_script(fixture: Fixture, experts: list[str]) -> dict[str, list[Any]]:
    s = Scripts(fixture)
    script: dict[str, list[Any]] = {
        "planner": [plan] * STEPS,
        "synthesis": [s.synthesis] * STEPS,
        "ask": [s.ask] * STEPS,
    }
    for i, e in enumerate(experts):
        script[f"expert/{e}"] = [s.expert(e, i)] * STEPS
    return script


class DelayedFakeBackend(FakeBackend):
    """FakeBackend that waits before answering and reports plausible token usage."""

    def __init__(self, script: dict[str, list[Any]], *, delay_s: float, name: str = "fake") -> None:
        super().__init__(script)
        self.delay_s = delay_s
        self.name = name  # type: ignore[assignment]

    async def _invoke(
        self,
        role_name: str,
        system_prompt: str,
        user_content: str,
        schema: type[BaseModel],
        role_cfg: RoleConfig,
        agent: str | None,
    ) -> tuple[Any, CallUsage]:
        await asyncio.sleep(self.delay_s * EXPERT_DELAY_FACTOR.get(agent or "", 1.0))
        step, usage = await super()._invoke(
            role_name, system_prompt, user_content, schema, role_cfg, agent
        )
        tin = len(user_content) // 4
        tout = 350 + 50 * len(role_name)
        usage = usage.model_copy(
            update={
                "input_tokens": tin,
                "output_tokens": tout,
                "cost_usd": round((tin * 3 + tout * 15) / 1_000_000, 4),
                "latency_s": self.delay_s,
            }
        )
        return step, usage


def create_e2e_app() -> FastAPI:
    settings = load_settings()
    delay = float(os.environ.get("WOMM_E2E_DELAY_S", "0.4"))
    fixture = load_fixture()
    sv = fake_system_version()
    script = e2e_script(fixture, [e.id for e in sv.spec.experts])
    backends = {
        "fake": DelayedFakeBackend(script, delay_s=delay),
        "api": DelayedFakeBackend(script, delay_s=delay, name="api"),
    }
    return create_app(settings, sv=sv, fixture=fixture, backends=backends)


# ----------------------------------------------------------------------------- evolution page

EVOLUTION_CYCLE = "cycle_e2e_demo"
WORKFORCE_PROMPT = (
    "You are the Workforce expert. For each provision in scope, identify effects on workers and "
    "employment: skills and training needs created by new obligations, staffing of compliance "
    "and oversight roles, changes in working conditions for people who operate or are subject "
    "to AI systems, and labour-market effects on SMEs. Quote the provision text for every "
    "finding and name the affected group precisely."
)
WORKFORCE_GLOSS = "effects on workers, skills, staffing and employment"


def _split_rows(case_means: dict[str, list[float]], fixture: str = "ai_act") -> list[dict]:
    """Archive metric rows (case, proposal, split level) from per-case run values."""
    from statistics import fmean, stdev

    rows: list[dict] = []
    for metric, values in case_means.items():
        sd = stdev(values) if len(values) > 1 else None
        case = {"level": "case", "subject": "case_02_sme_impacts", "metric": metric,
                "n": len(values), "mean": fmean(values), "sd": sd}  # fmt: skip
        rows += [case,
                 {**case, "level": "proposal", "subject": fixture, "n": 1, "sd": None},
                 {**case, "level": "split", "subject": "", "n": 1, "sd": None}]  # fmt: skip
    return rows


async def seed_evolution(db: Any) -> dict[str, str]:
    """A scripted self-evolution story for the evolution page (U9): the v1.0-unscoped seed and
    its api twin, a prompt-stage child, a topology child adding a Workforce expert (promoted on
    its api twin, weak mode) and a rejected dev candidate with an R37 regression. Decision
    summaries are inserted as published (aggregates only). Returns the version ids by role."""
    from womm.evolve.archive import Archive, archive_child, archive_twin, seed_archive
    from womm.evolve.edits import build_candidate, render_diff, validate_diff

    archive = Archive(db)
    seed, seed_api = await seed_archive(archive, REPO_ROOT)

    def fiscal_edit(sv: SystemVersion, extra: str) -> list[dict]:
        role = next(e for e in sv.spec.experts if e.id == "fiscal").role
        return [{"op": "edit_prompt", "role": "expert:fiscal",
                 "new_text": sv.prompt_text(role) + extra}]  # fmt: skip

    prompt_child = await archive_child(
        archive, seed, validate_diff(seed, fiscal_edit(seed, "\nQuantify every cost you name.")),
        origin="gepa", cycle_id=EVOLUTION_CYCLE,
        proposer={"model": "claude-opus-5-5", "prompt_hash": "ph_reflect"},
    )  # fmt: skip
    add = [{"op": "add_expert", "id": "workforce", "domain": "workforce",
            "prompt_text": WORKFORCE_PROMPT, "router_gloss": WORKFORCE_GLOSS}]  # fmt: skip
    diff = validate_diff(prompt_child, add)
    topology = build_candidate(prompt_child, diff)
    target = {"kind": "missed_impact", "category": "social_environmental", "owner": "none"}
    await archive.archive(
        topology, origin="topology", parent_id=prompt_child.version_id, cycle_id=EVOLUTION_CYCLE,
        diff={"ops": diff.ops_json(), "rendered": render_diff(prompt_child, topology),
              "rationale": "Workforce impacts are missed in 2 proposals and no expert owns them.",
              "target_pattern": target},
        proposer={"model": "claude-opus-5-5", "prompt_hash": "ph_expert"},
    )  # fmt: skip
    topology_api = await archive_twin(archive, topology, cycle_id=EVOLUTION_CYCLE)
    rejected = await archive_child(
        archive, seed, validate_diff(seed, fiscal_edit(seed, "\nIgnore enforcement costs.")),
        origin="gepa", cycle_id="cycle_e2e_rejected",
        proposer={"model": "claude-opus-5-5", "prompt_hash": "ph_reflect"},
    )  # fmt: skip

    metrics = {
        seed.version_id: ({"coverage": [0.5, 0.6, 0.55], "grounding": [0.9, 0.92, 0.91]},
                          [0.6, 0.62, 0.58]),
        prompt_child.version_id: ({"coverage": [0.6, 0.62, 0.64], "grounding": [0.9, 0.9, 0.92]},
                                  None),
        topology.version_id: ({"coverage": [0.7, 0.72, 0.68], "grounding": [0.91, 0.9, 0.92]},
                              [0.61, 0.63, 0.6]),
        rejected.version_id: ({"coverage": [0.66, 0.64, 0.65], "grounding": [0.8, 0.82, 0.81]},
                              [0.3, 0.32, 0.31]),
    }  # fmt: skip
    for vid, (val, diff_runs) in metrics.items():
        await archive.record_metrics(
            vid,
            "val",
            _split_rows(val),
            batch_id=f"rb_val_{vid[-6:]}",
            judge_version="jv_e2e",
            git_sha="e2e",
            full_split=True,
        )
        if diff_runs:
            await archive.record_metrics(
                vid, "diff_check", [r for r in _split_rows({"coverage": diff_runs})
                                    if r["level"] == "case"],
                batch_id=f"rb_diff_{vid[-6:]}", judge_version="jv_e2e", git_sha="e2e",
                full_split=True,
            )  # fmt: skip

    def delta(mean, low=None, high=None, sd=0.05):
        return {"mean_delta": mean, "ci95_low": low, "ci95_high": high, "n_cases": 8,
                "noise_sd": sd}  # fmt: skip

    decisions = [
        ("gate_e2e_promoted", EVOLUTION_CYCLE, topology_api.version_id, seed_api.version_id,
         "weak", True, "promoted", "promoted (weak threshold: directional)", [],
         ["weak mode: no minimum-detectable-delta report for this judge"],
         {"coverage": delta(0.06), "grounding": delta(-0.01), "omissions_addressed": delta(0.0)},
         3, ["insufficient_proposals"]),
        ("gate_e2e_rejected", "cycle_e2e_rejected", rejected.version_id, seed.version_id, "dev",
         False, "rejected", "rejected (dev-only, not deployable; weak threshold: directional)",
         ["grounding_regression"], [],
         {"coverage": delta(0.1), "grounding": delta(-0.1), "omissions_addressed": delta(0.0)},
         3, ["insufficient_proposals"]),
    ]  # fmt: skip
    async with db.pool.connection() as conn:
        for row in decisions:
            (gate, cycle, cand, inc, mode, deployable, decision, label, reasons, notes, deltas,
             n_proposals, flags) = row  # fmt: skip
            await conn.execute(
                "INSERT INTO promotion_decisions (gate_id, cycle_id, candidate_version,"
                " incumbent_version, mode, deployable, decision, label, reasons, notes, deltas,"
                " n_proposals, flags, policy_sha256, git_sha) VALUES (%s, %s, %s, %s, %s, %s, %s,"
                " %s, %s, %s, %s, %s, %s, 'sha_e2e_policy', 'e2e') ON CONFLICT DO NOTHING",
                (gate, cycle, cand, inc, mode, deployable, decision, label, json.dumps(reasons),
                 json.dumps(notes), json.dumps(deltas), n_proposals, json.dumps(flags)),
            )  # fmt: skip
    return {"seed": seed.version_id, "seed_api": seed_api.version_id,
            "prompt": prompt_child.version_id, "topology": topology.version_id,
            "topology_api": topology_api.version_id, "rejected": rejected.version_id}  # fmt: skip


async def _seed_evolution_db(database_url: str) -> None:
    from womm.api.db import Database

    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        await seed_evolution(db)
    finally:
        await db.close()


def _admin_url(database_url: str) -> tuple[str, str]:
    """(URL of the server's maintenance database, name of the target database)."""
    base, _, name = database_url.rpartition("/")
    return f"{base}/postgres", name.split("?", 1)[0]


def create_database(database_url: str) -> None:
    """Create the (empty) database `database_url` points at; fail if it already exists."""
    import psycopg

    admin, name = _admin_url(database_url)
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"unsafe database name {name!r}")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')


def drop_database(database_url: str) -> None:
    import psycopg

    admin, name = _admin_url(database_url)
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(f"unsafe database name {name!r}")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def main(argv: list[str] | None = None) -> None:
    """`python -m womm.api.e2e create-db|drop-db|seed-evolution`: manage the database
    DATABASE_URL points at (seed-evolution archives the scripted evolution story).

    Playwright starts its webServer before globalSetup runs, so the webServer command creates
    the fresh database right before starting uvicorn; globalTeardown drops it.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m womm.api.e2e")
    parser.add_argument("action", choices=["create-db", "drop-db", "seed-evolution"])
    args = parser.parse_args(argv)
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL must be set")
    if args.action == "seed-evolution":
        asyncio.run(_seed_evolution_db(database_url))
        return
    (create_database if args.action == "create-db" else drop_database)(database_url)


if __name__ == "__main__":
    main()
