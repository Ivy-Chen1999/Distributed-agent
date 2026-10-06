"""Scripted demo cycle (self-evolution plan U10 rehearsal; R30): one reproducible cycle from a
base version to a promotion decision, on a scripted fake backend.

It runs the real code path end to end, with every LLM call scripted and no api key:

1. **Failures.** The base (``v1.0-unscoped`` with every role on ``fake``) is replayed on train.
   Its experts never cite the provisions a workforce impact follows from, so Failure Memory
   records a persistent ``missed_impact / social_environmental / owner none`` pattern across two
   proposals (ai_act and platform_work).
2. **Prompt stage.** GEPA with the scripted reflector, which appends an SME cost-relief
   instruction to the component it is asked about; only the Fiscal expert reacts to it.
3. **Topology stage.** The scripted expert proposer answers the pattern with a Workforce expert
   (archived with the proposer's raw proposal, origin ``topology``).
4. **Gate (dev mode).** ``promotion.run_gate`` on a SYNTHETIC holdout held in memory
   (``ScriptedHoldoutStore``): two synthetic cases, never the sealed holdout, never
   ``HOLDOUT_DATABASE_URL``. The policy is the committed one, signed in memory by this harness
   and publishing its summary, so page 4 shows the decision. Dev mode: the label says
   "dev-only, not deployable" and the threshold is directional.
5. **Page 4.** The archive and the summary are in ``DATABASE_URL``; the evolution page shows the
   lineage, the prompt child and the new-expert highlight.

Every case, the holdout and every model output are synthetic. Nothing here is evidence that the
system improves: it rehearses the demo path (docs/demo/v1-self-evolution-runbook.md). The
claude_code dev-mode path is the CLI sequence ``dev_commands`` prints (``python -m
womm.demo_cycle dev-plan``); it costs subscription usage and is run by a person.

Run: ``DATABASE_URL=... uv run python -m womm.demo_cycle scripted`` on a fresh database
(``python -m womm.api.e2e create-db``), then serve the console on the same database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from womm.api.e2e import _changes, _sentences, _sources, plan
from womm.config import REPO_ROOT
from womm.data.fixtures import fixture_dir, load_fixture
from womm.eval.golden import ExpectedImpact, GoldenCase
from womm.llm.fake import FakeBackend
from womm.models.run import CodeIdentity
from womm.models.system_version import (
    RoleConfig,
    SystemVersion,
    build_system_version,
    load_system_version,
)

DEMO_CYCLE = "cycle_demo_scripted"
SEED_FILE = REPO_ROOT / "system_versions" / "v1.0-unscoped.yaml"
CODE = CodeIdentity(git_sha="scripted-demo", dirty=False)
CATEGORY = "social_environmental"
TARGET = f"missed_impact/{CATEGORY}/none"
# The two proposals of the demo: (fixture, public scenario).
PROPOSALS = (
    ("ai_act", "eval_sme_impacts"),
    ("platform_work", "eval_employment_status_algorithmic_management"),
)
# Per proposal, the provision its synthetic workforce impact follows from. No base expert ever
# cites it, so the miss has no owner (each has quotable sentences for the Workforce expert).
WORKFORCE_KEYS = {
    "ai_act": "ai_act/penalties/penalties",
    "platform_work": "platform_work/proposal/art/7",
}
STEPS = 100_000
# What the scripted reflector adds to a prompt, and what the scripted Fiscal expert looks for.
RELIEF_INSTRUCTION = (
    "\n\nFor every provision that supports small and medium-sized enterprises, also report the "
    "cost relief it gives them and whether it offsets their compliance burden, as a separate "
    "finding with its own mechanism."
)
RELIEF_SIGNAL = "cost relief it gives them"
FISCAL_MARK = "SME cost relief"
WORKFORCE_MARK = "workforce effect"
WORKFORCE_PROMPT = (
    "You are the Workforce Analyst in a regulatory impact assessment system for EU legislation. "
    "Your lens: effects on workers, employment, skills, staffing and working conditions, "
    "including people whose work is organised or monitored by automated systems. Report "
    "impacts as findings with provision_key, affected_actor, mechanism, impact, verbatim "
    "evidence of at least 8 words and confidence; an empty list is better than an unsupported "
    "finding."
)
WORKFORCE_GLOSS = "effects on workers, skills, staffing and employment"


# ----------------------------------------------------------------------------- versions, cases


def demo_base() -> SystemVersion:
    """``v1.0-unscoped`` with every role on the scripted ``fake`` backend (dev, never
    deployable)."""
    spec = load_system_version(SEED_FILE, REPO_ROOT).spec
    data = spec.model_dump()
    for role in ("planner", "synthesis", "judge"):
        data[role]["backend"] = "fake"
    data["experts"] = [{**e, "role": {**e["role"], "backend": "fake"}} for e in data["experts"]]
    data["name"] = f"{spec.name}-scripted-demo"
    data["description"] = (
        "Scripted demo cycle (U10 rehearsal): v1.0-unscoped with every role on the scripted "
        "fake backend. Synthetic, dev-only, never deployable."
    )
    return build_system_version(type(spec).model_validate(data), REPO_ROOT)


def workforce_keys() -> set[str]:
    return set(WORKFORCE_KEYS.values())


def _scenario_id(fixture: str, scenario: str, split: str) -> str:
    """Holdout scenarios get their own ids: a sealed scenario never clashes with a public one."""
    return f"eval_demo_holdout_{fixture}" if split == "holdout" else scenario


def demo_cases(split: str) -> list[GoldenCase]:
    """One synthetic case per demo proposal: a base impact every version covers, an SME
    cost-relief impact only a Fiscal prompt with the relief instruction covers, and a workforce
    impact only a Workforce expert covers."""
    if split not in ("train", "val", "holdout"):
        return []
    out = []
    for fixture, scenario in PROPOSALS:
        keys = load_fixture(fixture_dir(fixture)).scenario(scenario).provision_keys
        p = f"demo_{split}_{fixture}"

        def item(suffix: str, key: str, category: str, actor: str, impact: str,
                 p: str = p) -> ExpectedImpact:  # fmt: skip
            return ExpectedImpact(
                expected_id=f"{p}_{suffix}", affected_actor=actor,
                mechanism="Synthetic scripted-demo mechanism.", impact=impact,
                provision_keys=[key], ia_section="synthetic (scripted demo, no IA)",
                category=category,
            )  # fmt: skip

        out.append(GoldenCase(
            case_id=p, scenario_id=_scenario_id(fixture, scenario, split),
            ia_reference="Synthetic scripted-demo case; no impact assessment.",
            fixture=fixture, split=split,
            notes="Synthetic: used only by the scripted demo cycle (womm.demo_cycle).",
            expected_impacts=[
                item("base", keys[0], "legal", "Regulated operators", "New obligations apply."),
                item("fiscal", keys[0], "economic", "SMEs", "SME cost relief offsets costs."),
                item("wf", WORKFORCE_KEYS[fixture], CATEGORY, "Workers",
                     "Workforce effects on staff."),
            ],
        ))  # fmt: skip
    return out


# ----------------------------------------------------------------------------- scripted LLM


def _finding(change: dict, sources: dict[str, str], actor: str, impact: str) -> dict | None:
    sid = change.get("after") or change.get("before")
    sentences = _sentences(sources.get(sid or "", ""))
    if not sid or not sentences:
        return None
    return {
        "provision_key": change["key"], "affected_actor": actor, "impact": impact,
        "mechanism": f"The provision sets out: {sentences[0][:80]}",
        "evidence": [{"source_id": sid, "quote": sentences[0]}], "confidence": 0.8,
    }  # fmt: skip


def expert(agent: str, wf_keys: set[str]) -> Callable[[str, str], dict]:
    """A base expert cites one non-workforce provision; the Fiscal expert adds SME cost relief
    when its prompt carries the relief instruction; a Workforce expert cites the workforce
    provision."""

    def step(system: str, user: str) -> dict:
        changes, sources = _changes(user), _sources(user)
        if agent == "workforce":
            picked = [c for c in changes if c["key"] in wf_keys][:1]
            impact = f"Workers: {WORKFORCE_MARK} on staffing, skills and working conditions."
        else:
            picked = [c for c in changes if c["key"] not in wf_keys][:1]
            impact = f"{agent.capitalize()} reading: operators face new duties."
            if agent == "fiscal" and RELIEF_SIGNAL in system:
                impact += f" {FISCAL_MARK} offsets part of the compliance burden."
        findings = [f for c in picked if (f := _finding(c, sources, agent, impact))]
        return {"findings": findings}

    return step


def synthesis(_system: str, user: str) -> dict:
    rows = json.loads(user.split("Validated findings:\n", 1)[1])
    return {
        "impacts": [
            {"impact_id": f"I{i}", "summary": r["impact"], "finding_ids": [r["finding_id"]]}
            for i, r in enumerate(rows, 1)
        ],
        "chains": [], "disagreements": [], "open_questions": [], "discarded": [],
    }  # fmt: skip


def judge(_system: str, user: str) -> dict:
    """Covers an expected impact when a dossier impact carries its marker: the base impact by
    any impact, the SME relief by ``FISCAL_MARK``, the workforce impact by ``WORKFORCE_MARK``."""
    head, rest = user.split("\n\nExpected impacts:\n", 1)
    expected_raw, omissions_raw = rest.split("\n\nImportant omissions:\n", 1)
    impacts = json.loads(head.removeprefix("Dossier impacts:\n"))
    marks = {"base": "", "fiscal": FISCAL_MARK, "wf": WORKFORCE_MARK}

    def match(expected_id: str) -> str | None:
        mark = marks[expected_id.rsplit("_", 1)[-1]]
        return next((i["impact_id"] for i in impacts if mark in json.dumps(i)), None)

    verdicts = []
    for e in json.loads(expected_raw):
        hit = match(e["expected_id"])
        verdicts.append({"expected_id": e["expected_id"], "covered": hit is not None,
                         "impact_id": hit, "justification": "scripted demo judge"})  # fmt: skip
    omissions = [{"omission_id": o["omission_id"], "addressed": True, "impact_id": None,
                  "justification": "scripted demo judge"}
                 for o in json.loads(omissions_raw)]  # fmt: skip
    return {"expected": verdicts, "omissions": omissions}


def reflect(_system: str, user: str) -> dict:
    """The scripted reflector: append the relief instruction to the component it is asked
    about (only the Fiscal expert reacts to it)."""
    role = user.split("Component: ", 1)[1].split("\n", 1)[0]
    current = user.split("Current prompt:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
    return {"role": role, "new_text": current + RELIEF_INSTRUCTION,
            "rationale": f"scripted: {role} misses the SME cost relief"}  # fmt: skip


def propose_expert(_system: str, user: str) -> dict:
    """The scripted expert proposer: a Workforce expert for the unowned workforce pattern."""
    return {
        "id": "workforce", "domain": "workforce", "prompt_text": WORKFORCE_PROMPT,
        "router_gloss": WORKFORCE_GLOSS, "target_pattern": TARGET,
        "rationale": "Workforce impacts are missed in 2 proposals and no expert owns them "
                     "(scripted demo proposal).",
    }  # fmt: skip


def graph_backend() -> FakeBackend:
    wf = workforce_keys()
    script: dict[str, list[Any]] = {
        "planner": [plan] * STEPS,
        "synthesis": [synthesis] * STEPS,
        "judge": [judge] * STEPS,
        "expert/workforce": [expert("workforce", wf)] * STEPS,
    }
    for agent in ("legal", "fiscal", "stakeholder"):
        script[f"expert/{agent}"] = [expert(agent, wf)] * STEPS
    return FakeBackend(script)


def proposer_backend() -> FakeBackend:
    return FakeBackend({
        "improvement_planner/topology": [propose_expert] * STEPS,
        "improvement_planner": [reflect] * STEPS,
    })  # fmt: skip


# ----------------------------------------------------------------------------- synthetic holdout


class ScriptedHoldoutStore:
    """An in-memory SYNTHETIC holdout for the scripted gate: the store interface
    ``holdout.compare`` and ``promotion.run_gate`` use, over ``demo_cases('holdout')``. It never
    reads ``HOLDOUT_DATABASE_URL`` or the sealed cases."""

    def __init__(self) -> None:
        self.progress: dict[tuple, Any] = {}
        self.audits: list[dict] = []
        self.decisions: dict[str, dict] = {}
        self.reservations: dict[str, str] = {}

    async def _sealed(self):
        out = []
        for case in demo_cases("holdout"):
            public = dict(PROPOSALS)[case.fixture]
            scenario = load_fixture(fixture_dir(case.fixture)).scenario(public)
            out.append((case, scenario.model_copy(update={"scenario_id": case.scenario_id})))
        return out

    async def load_progress(self, version_ids, judge_version, code_version):
        return {(v, k, r): s for (v, j, c, k, r), s in self.progress.items()
                if v in version_ids and j == judge_version and c == code_version}  # fmt: skip

    async def save_progress(self, version_id, judge_version, code_version, key, rep, score):
        self.progress[(version_id, judge_version, code_version, key, rep)] = score

    async def record_audit(self, result, git_sha, *, gate_id=None, cycle_id=None) -> int:
        self.audits.append({"result": result, "gate_id": gate_id, "cycle_id": cycle_id})
        return len(self.audits)

    async def record_decision(self, gate_id: str, decision: dict) -> None:
        self.decisions[gate_id] = decision
        self.reservations[gate_id] = "consumed" if decision["consumes_budget"] else "released"

    async def budget_used(self, cycle_id: str) -> tuple[int, int]:
        used = [g for g, s in self.reservations.items() if s != "released"]
        return len(used), len(used)  # one cycle per demo run

    async def prior_aborts(self, candidate_version: str, baseline_version: str) -> int:
        return 0

    async def reserve_budget(self, gate_id, cycle_id, candidate_version, baseline_version, *,
                             per_cycle, total, resume=False) -> str:  # fmt: skip
        self.reservations[gate_id] = "reserved"
        return gate_id

    async def release_budget(self, gate_id: str) -> None:
        if self.reservations.get(gate_id) == "reserved":
            self.reservations[gate_id] = "released"


def demo_policy():
    """The committed policy, signed IN MEMORY by this harness for the synthetic holdout only,
    with the summary published so page 4 shows the decision. Never written to disk and never
    usable by ``womm evolve promote``."""
    from womm.evolve.promotion import load_policy

    policy, _ = load_policy()
    return policy.model_copy(update={
        "signed_by": "scripted demo harness (synthetic holdout only; not a pre-registration)",
        "signed_on": "2026-10-06", "publish_summary": True, "repetitions": 2,
    })  # fmt: skip


# ----------------------------------------------------------------------------- the demo


@dataclass
class DemoResult:
    base: str
    prompt_best: str | None
    topology: str | None
    topology_reason: str | None
    chosen: str
    decision: dict
    patterns: list[dict]


def _config():
    from womm.evolve.proposers import load_evolution_config

    cfg = load_evolution_config()
    fake = {"backend": "fake", "model": "fake-scripted-demo"}
    roles = cfg.roles.model_copy(update={
        "reflect": RoleConfig(**fake, prompt=cfg.roles.reflect.prompt),
        "propose_expert": RoleConfig(**fake, prompt=cfg.roles.propose_expert.prompt),
    })  # fmt: skip
    return cfg.model_copy(update={"roles": roles})


async def run_scripted_demo(database_url: str, workdir: Path) -> DemoResult:
    """Failures, prompt stage, topology stage and a dev-mode gate, all scripted, on
    ``database_url`` (a fresh database: the archive and the published summary land there)."""
    from womm.api.db import Database
    from womm.decisions.stub import StubDecisionService
    from womm.evolve import promotion as pm
    from womm.evolve.archive import Archive
    from womm.evolve.cycle import ReplayEvaluator, run_cycle
    from womm.evolve.diff_regression import r37_record
    from womm.evolve.planner_view import PlannerView
    from womm.evolve.proposers import Budget, Proposer
    from womm.evolve.replay import ReplayStore, ReplayWorker

    base = demo_base()
    graph = graph_backend()
    db = Database(database_url)
    await db.open()
    try:
        await db.migrate()
        archive = Archive(db)
        await archive.archive(base, origin="seed")
        store = ReplayStore(db, load_cases=demo_cases)
        worker = ReplayWorker(store=store, archive=archive, judge_sv=base,
                              backends={"fake": graph}, decisions=StubDecisionService(),
                              code=CODE, runs_dir=workdir / "runs")  # fmt: skip
        evaluator = ReplayEvaluator(archive_store=archive, store=store, worker=worker)
        budget = Budget(max_metric_calls=60, minibatch_size=2, train_repetitions=2,
                        val_repetitions=2)  # fmt: skip
        # 1. Failures: the base on train, two repetitions, recorded in Failure Memory.
        await evaluator.evaluate(base, "train", [c.case_id for c in demo_cases("train")], 2)
        async with PlannerView(database_url, runs_dir=workdir / "runs", env={},
                               load_cases=demo_cases) as view:  # fmt: skip
            patterns = await view.failure_patterns(base.version_id)
            # 2-3. Prompt stage, then the topology stage, then one candidate for the gate.
            result = await run_cycle(
                base=base, view=view, evaluator=evaluator,
                proposer=Proposer(proposer_backend(), _config()), budget=budget,
                stage="both", cycle_id=DEMO_CYCLE, run_dir=str(workdir / "gepa"),
            )  # fmt: skip
        chosen = result.chosen
        # 4. Dev-mode gate on the synthetic holdout; the summary is published to page 4.
        decision = await pm.run_gate(
            candidate=chosen, incumbent=base, policy=demo_policy(),
            policy_sha256="scripted-demo-policy", records=pm.PromotionRecords(),
            store=ScriptedHoldoutStore(), backends={"fake": graph},
            decisions=StubDecisionService(), code=CODE, cycle_id=DEMO_CYCLE,
            policy_committed=True,  # in-memory policy for the synthetic holdout (see above)
            r37=await r37_record(archive, chosen.version_id, base.version_id), summary_db=db,
        )  # fmt: skip
    finally:
        await db.close()
    topo = result.topology
    return DemoResult(
        base=base.version_id,
        prompt_best=result.prompt.best.version_id if result.prompt else None,
        topology=topo.candidate.version_id if topo and topo.candidate else None,
        topology_reason=topo.reason if topo else None,
        chosen=chosen.version_id, decision=decision.model_dump(mode="json"),
        patterns=[p for p in patterns if p.get("kind") == "missed_impact"],
    )  # fmt: skip


# ----------------------------------------------------------------------------- claude_code path


def dev_commands() -> list[str]:
    """The claude_code dev-mode demo, as the CLI sequence a person runs (subscription usage;
    the gate needs the signed, committed policy and HOLDOUT_DATABASE_URL). The ids are the
    committed seed's."""
    dev = load_system_version(SEED_FILE, REPO_ROOT).version_id
    return [
        "uv run womm evolve seed",
        f"uv run womm evolve replay {dev} --split train --repetitions 2",
        f"uv run womm evolve failures --sv {dev} --from-db",
        f"uv run womm evolve cycle --base {dev} --stage both --cycle-id cycle_demo_dev",
        "# chosen = the cycle's 'candidate for the gate'; R37 (optional, after the reference "
        "answers are written):",
        "uv run womm evolve diffcheck <chosen>; uv run womm evolve diffcheck " + dev,
        "# HOLDOUT side only (a separate shell with HOLDOUT_DATABASE_URL; the policy signed and "
        "committed):",
        f"uv run womm evolve promote <chosen> --incumbent {dev}",
        "uv run womm evolve show <chosen>",
    ]


def _summary(r: DemoResult) -> str:
    d = r.decision
    return "\n".join([
        "scripted demo cycle (synthetic cases, scripted fake LLM, synthetic in-memory holdout)",
        f"base: {r.base}",
        "failure patterns: " + ", ".join(
            f"{p['kind']}/{p['category']}/{p['owner']} ({len(p.get('proposals', []))} proposals)"
            for p in r.patterns),
        f"prompt stage best: {r.prompt_best}",
        f"topology stage: {r.topology_reason} ({r.topology or 'none'})",
        f"candidate for the gate: {r.chosen}",
        f"gate: {d['label']}",
        "open the console's Evolution page on this database to see the lineage",
    ])  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m womm.demo_cycle")
    parser.add_argument("action", choices=["scripted", "dev-plan"])
    args = parser.parse_args(argv)
    if args.action == "dev-plan":
        print("\n".join(dev_commands()))
        return 0
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("DATABASE_URL must be set (a fresh database)")
    if os.environ.get("HOLDOUT_DATABASE_URL"):
        raise SystemExit("the scripted demo never runs with HOLDOUT_DATABASE_URL set")
    with tempfile.TemporaryDirectory(prefix="womm_demo_") as tmp:
        result = asyncio.run(run_scripted_demo(database_url, Path(tmp)))
    print(_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
