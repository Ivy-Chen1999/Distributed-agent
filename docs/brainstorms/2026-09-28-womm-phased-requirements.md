---
date: 2026-09-28
topic: womm-phased-requirements
source: brainstorm/Plan.docx
---

# WOMM — Phased Requirements (v0 this Friday / v1 in two months)

## Problem Frame

WOMM is a self-evolving multi-agent system for EU regulatory impact assessment (RIA). The full vision is in `brainstorm/Plan.docx`. This document does not repeat the vision. It only cuts it into two deliverable phases and locks in the technical trade-offs:

- **v0 — 2026-10-02 (Friday)**: end-to-end skeleton. Prove that the chain "Regulatory Change → Provision → Finding → Evidence → Source" and the evaluation baseline can run end to end online. Self-evolution is left as interfaces only.
- **v1 — around 2026-11-27**: all of the Plan's Must build + Adaptive items land. Demo one full improvement cycle. The strongest form is "a new expert is created and promoted via holdout" (Plan §16).

Constraint: the EU AI Act structured data is owned by a colleague and has not been delivered yet. v0 must not be blocked by it.

Tech stack (decided): LangGraph · Pydantic v2 · LangSmith · Jev (TypeSafe AI) · Railway · pluggable LLM backend (local Claude Code subscription during development, later OpenAI / Anthropic API keys).

---

## Actors

- A1. Analyst / demo audience: submits a proposal, reads the Impact Dossier, and (v1) gives accept/reject/edit feedback.
- A2. Colleague (data owner): delivers multi-version structured EU AI Act data that must meet the data contract defined by this project.
- A3. LLM agents (pluggable backend): Impact Planner, experts, Synthesis, (v1) Improvement Planner, LLM judge.
- A4. Jev: bounded decisions (routing, publish/deliver/relate, escalation). Returns typed answers with calibrated probabilities.
- A5. Evaluation / promotion process: runs golden cases, produces metrics, and (v1) decides promote/reject for candidate versions.

---

## Key Flows

- F1. RIA run (from v0)
  - **Trigger:** A1 submits a proposal via the API (a fixture in v0)
  - **Actors:** A1, A3, A4
  - **Steps:** structured versions → provision-level diff → Impact Planner sets investigation focus → Jev Router decision (recorded only, in shadow) → experts produce ImpactFindings in parallel → written to the Shared Impact Board → Synthesis → citation validation → Impact Dossier
  - **Outcome:** the Impact Dossier is queryable; every finding traces back to a provision and a source; every Jev decision has a DecisionRecord
  - **Failure path:** when a single expert fails, degrade (the Impact Dossier marks the missing expert) instead of failing the whole run; findings that fail citation validation are downgraded to open questions
  - **Covered by:** R1–R10, R15–R16, R32

- F2. Evaluation (v0 baseline, extended in v1)
  - **Trigger:** manual or scheduled; runs golden cases against a given SystemVersion
  - **Actors:** A5, A3
  - **Steps:** run F1 for each case (the official IA is not visible to agents) → compare with the reference IA subsection → compute coverage / grounding / omissions / reasoning / efficiency → record the experiment, tagged by system_version → write failures to Failure records
  - **Outcome:** the version has a comparable metrics baseline
  - **Covered by:** R11–R13, R14a, R14b, R22–R24, R34, R37

- F3. Self-evolution cycle (v1)
  - **Trigger:** a recurring pattern appears in Failure Memory
  - **Actors:** A3, A5
  - **Steps:** Improvement Planner diagnoses → proposes a config diff (prompt / routing / research policy / agent composition) → generates a candidate SystemVersion (parent recorded) → replays on train/val → evaluates on the sealed holdout → promote or reject (result written to the promotion audit record, not visible to the Planner)
  - **Outcome:** version lineage is traceable; only candidates that are significantly better on holdout are promoted
  - **Covered by:** R25–R30

---

## Requirements

### v0 (from 2026-10-02, in two steps)

Deliver by priority. **P1–P2 must be done by Friday.** Do P3–P5 on Friday if there is time; otherwise they move to next week's v0.1. Fallback for each item:

1. P1 Run the RIA chain locally and produce an Impact Dossier (R1–R9, R32)
2. P2 Evaluation baseline (R11–R13, R14a). On Friday it runs on the `claude_code` backend and is classified as a development-time smoke baseline. Fallback: only 1 golden case
3. P3 Railway deployment (R15). Fallback: local demo
4. P4 Jev shadow (R10). Fallback: write DecisionRecords with a stub recorder
5. P5 Failure records (R14b) and noise measurement (R34). Can be deferred to v0.1; the formal baseline and R34 run on the api backend and belong to v0.1

R16 tracing is on by default from P1.


**Data contract and fixtures**
- R1. Define the Regulation → Version → Provision data contract (fields per Plan §4) and use it as the delivery format for the colleague's data. Every provision carries a provision key that is stable across versions (the AI Act was heavily renumbered from proposal to final text, e.g. penalties Art.71 → Art.99): in v0 the mapping table is maintained by hand; in the colleague's data contract it is a required field. Once the colleague's data arrives, only the data source needs replacing, with no change to agent code.
- R2. v0 builds its own minimal fixture, with two kinds of scenarios:
  - **Evaluation scenario**: the input is "no prior version → COM(2021)206" (the whole proposal is treated as a new change). This matches the object assessed by the official IA SWD(2021)84 and is used for golden case scoring.
  - **Demo scenario**: a diff of a small number of corresponding provisions from COM(2021)206 → Reg (EU) 2024/1689 (candidates: SME / compliance obligations / penalty-related), to show the provision-level diff capability. The official IA did not assess these amendments, so this scenario gets no IA-based coverage scoring.
- R3. The provision-level diff is a deterministic tool. It matches by provision key (not by article number) and outputs lists of added / removed / modified provisions as Planner input.

**RIA main chain**
- R4. The Impact Planner (LLM) outputs investigation focus based on the diff.
- R5. The v0 experts are Legal, Fiscal, and Stakeholder. They run in parallel, and each outputs structured ImpactFindings (provision, affected_actor, impact, mechanism, evidence, confidence, per Plan §6; agent is filled in by the system in provenance, see R6). Each expert catches its own exceptions (LLM errors, timeouts, structured-output retries exhausted) and writes them to the Board as ExpertFailure entries instead of raising them upward. This guarantees the F1 degradation path.
- R6. `finding_id` and provenance (agent, system_version, prompt version, model, round) are filled in by the system, not generated by the LLM.
- R7. The v0 Shared Impact Board is an append-only shared collection of findings. There is no Jev publish/deliver/relate; Synthesis reads everything.
- R8. Synthesis merges duplicates, keeps disagreements, links impact chains, and outputs the Impact Dossier (impacts · evidence · open questions).
- R9. v0 evidence sources are limited to the COM(2021)206 provisions + its explanatory memorandum (excluding sections that cite IA conclusions, e.g. "Results of impact assessments"). Each source is assigned a source_id. Experts may only cite these sources; the official IA is not among them. Citation validation in v0 is deterministic verbatim matching: every evidence item must carry a source_id + a verbatim quote, and it counts as supported only if the quote can be found (after normalization) in the source text; otherwise it is downgraded to an open question.

**Jev and decision log**
- R10. The Jev Router runs in **shadow** mode: for each expert it gives "relevant or not" plus a probability, written to a DecisionRecord (decision_point, input, decision, probability, mode, system_version), but all experts run as usual. Router mode supports off / shadow / active configuration. In shadow, Jev calls have a short timeout; on timeout or error the run continues as usual, and the DecisionRecord records `decision=error`, an empty probability, and the error reason.

**Evaluation baseline**
- R11. 2–3 golden cases. The input is the R2 evaluation scenario. Reference answers come from manageable subsections of the official AI Act impact assessment (the approach suggested in Plan §9). The reference IA does not enter any data source that agents can access.
- R12. v0 metrics: coverage (LLM judge compares against expected impacts), grounding (R9 pass rate), efficiency (latency, tokens, cost). omissions gets a numeric score from the judge in the same form as the v1 threshold; reasoning can be qualitative at first. v0 grounding only measures citation presence rate, not whether citations support the conclusions.
- R13. Each evaluation is one LangSmith experiment, tagged with system_version metadata, and can be compared side by side in the UI.
- R14a. Introduce the SystemVersion concept (prompts, backend and model, router mode, agent composition, parent). v0 has only one version.
- R14b. Cases that fail are recorded as Failure entries (reserved for v1).
- R34. Measure run-to-run noise: on the api backend and model that v1 evaluation will use, run the same golden case 3 times and record the variation in coverage / grounding / omissions, as input to the v1 promotion threshold. Data from `claude_code` is for development reference only.

**Runtime and deployment**
- R15. Deploy to Railway: API service + Postgres. One RIA run executes as a background task; the API returns a task ID that can be polled for status and results (no reliance on a single long HTTP request).
- R16. LangSmith tracing across the full chain.

**LLM backend (from v0)**
- R32. All LLM calls go through a unified backend interface. v0 has at least two implementations: `claude_code` (local Claude Code subscription, structured output via the CLI, available only for local development) and `api` (OpenAI / Anthropic key, used online and for formal evaluation). The backend and model for each role (Planner, experts, Synthesis, judge) are written in the SystemVersion; switching only changes config. Each LangSmith experiment is tagged by backend and model; metrics across backends are not compared directly.
  - The `claude_code` backend must be called in isolation mode: disable all built-in tools and MCP, do not load user or project settings and CLAUDE.md, use an explicit system prompt, and run in a temporary directory that contains no golden reference answers, to guarantee the data isolation of R9 / R11.
  - Each call is wired into a LangSmith trace manually, and the usage and cost from the CLI output are written to metadata.

### v1 (around 2026-11-27)

**Data and evidence**
- R17. Integrate the multi-version AI Act data delivered by the colleague (proposal → amendments → consolidated version), covering all provisions, with on-demand retrieval rather than putting the full text into context.
- R18. Evidence expert and Workforce expert go live (5 in total). External evidence is discovered via Exa; each item keeps its source and provenance.
- R19. Three-layer citation validation: verbatim matching → support scoring (a MiniCheck-style small model) → only ambiguous cases go to the LLM judge.

**Collaboration layer**
- R20. Jev publish / deliver / relate go live: whether a finding is published, which experts it is delivered to, and its relation to existing findings (supports / contradicts / supersedes); contradictions are kept, not deleted. Experts that receive deliveries enter the next round, until there are no new deliveries or the round limit is reached.
- R21. Router and collaboration decisions start in shadow and switch to active after reaching a reliability threshold. The threshold is based on the calibration curve of DecisionRecords against evaluation results.

**Evaluation**
- R22. Expand golden cases to about 15–30, sourced from "COM proposal + official IA" pairs in EU Better Regulation / Cellar. RSB opinions serve as the annotation source for omissions.
- R23. Golden cases are split into train / val / holdout. Holdout reference answers are stored only in our own database, accessible only to the promotion process, and not visible to the Improvement Planner.
- R24. Strip sections that cite IA conclusions from the input proposal (e.g. "Results of impact assessments" in the explanatory memorandum) to prevent answer leakage. For holdout, prefer IAs published after the model's training cutoff.
- R33. The AI Act has only one official IA; the other golden cases come from other EU proposals. These proposals are imported automatically under the R1 contract (Cellar / Formex parsing), with the input form unified as "no prior version → proposal". An owner must be assigned during v1 planning (the colleague's data scope covers only the AI Act).

**Self-evolution**
- R25. Failure Memory: collects only failures on train/val, as structured records (which agent, which impact type, which cases, frequency), and can be aggregated by pattern.
- R26. The Improvement Planner can only modify config (prompts, routing table, research policy, agent registry), not code.
- R27. Every candidate SystemVersion is archived (parent, diff, metrics at each level), not just the current best.
- R28. Promotion threshold: the overall improvement on holdout exceeds repeated-run noise (bootstrap CI), and regressions in coverage / grounding / omissions each stay within their own tolerance. The minimum holdout size, repetitions per case, and tolerances are set before v1 planning based on R34 data. If the data volume cannot support a statistical threshold, switch to a weak threshold (directional improvement + regressions within tolerance) and state this honestly in the demo. Candidates that miss the threshold are rejected. Promotion results (candidate id, pass/fail, aggregate delta) are written only to an audit record readable only by the promotion process. They do not enter the Failure Memory readable by the Improvement Planner, to avoid holdout information leakage.
- R29. Batch replay runs as a background task and can resume after a service restart.
- R37. Provision-level diff regression check: write reference answers by hand for the R2 demo scenario (proposal → final text), run it for every SystemVersion, and record the score. This check does not take part in the promotion decision, but a worse score must be visible on the evaluation page and in the promotion record.
- R30. Demo: one full cycle, targeting "existing experts repeatedly miss a certain kind of impact → a new expert is proposed → holdout improves → promotion". If the new-expert route is not stable before the deadline, fall back to a prompt-level improvement cycle.

**Demo frontend** (design owned by the user, simple version; v0.1 implements pages 1–2, v1 implements pages 3–4)
- R35. The backend provides the frontend with a run event stream (node start/complete/fail, Jev decisions, findings written to the Board) and query endpoints (Impact Dossier, evaluation results, version lineage). The frontend only displays; it carries no business logic.
- R36. Pages and what they must show:
  1. **Run page**: choose a fixture scenario (evaluation scenario / diff demo scenario) and submit; a multi-agent structure diagram where each node shows its status in real time (waiting / running / done / failed); the Router node shows Jev's judgment and probability, with a shadow marker; the Board shows the findings stream in real time, color-coded by expert.
  2. **Impact Dossier page**: impacts grouped by affected party or domain; each impact can expand its trace chain: regulatory change → provision (article number + verbatim text) → mechanism → evidence quote (highlighted in the source text) → source; show confidence; contradictory findings shown side by side; open questions in their own section; failed experts must be marked (from ExpertFailure). The diff demo scenario additionally provides a proposal / final-text provision comparison view.
  3. **Evaluation page**: coverage / grounding / omissions / efficiency for each SystemVersion; per case, show the expected impacts that were hit and missed.
  4. **Evolution page (v1)**: version lineage tree; config diff of candidate vs incumbent (prompts, agent composition); train/val and holdout scores; promote / reject result; highlight separately when a new expert is created.
  - Every page must have loading, empty, and error states.

**Human review**
- R31. Analysts can mark a finding as Accept / Reject / Edit / Missing impact / Weak evidence (Plan §14). Feedback flows into golden cases and Failure Memory.

---

## Acceptance Examples

- AE1. **Covers R9.** Given a finding's quoted text does not exist in the source, when citation validation runs, then the finding does not appear in the Impact Dossier's impacts, but appears in open questions marked "evidence unresolved".
- AE2. **Covers R10.** Given the Router is in shadow mode and judges Fiscal not relevant, when the run executes, then Fiscal still runs and produces findings, and the DecisionRecord records `decision=not_relevant, mode=shadow` and the probability.
- AE3. **Covers R23.** Given the Improvement Planner is generating a candidate, when it queries evaluation data, then it can only see train/val failure summaries, not holdout cases or reference answers.
- AE4. **Covers R25, R28.** Given a candidate has coverage +8% on holdout but grounding drops, when the promotion decision runs, then reject, with the reason written to the promotion audit record; the Improvement Planner cannot see this holdout result.

---

## Success Criteria

- **v0 (Friday)**: submit a fixture proposal locally and get an Impact Dossier with citations; LangSmith shows the full trace and at least one smoke baseline experiment tagged with system_version (`claude_code` backend).
- **v0.1**: the same flow can be demoed on Railway; the formal baseline and R34 noise data are produced on the api backend; Jev shadow decision records exist; demo pages 1–2 (R36) are usable.
- **v1**: one full improvement cycle can be demoed reproducibly, and the promotion decision is backed by holdout data; integrating the colleague's data needs no change to the agent layer (validates the R1 contract).
- **Handoff quality**: `ce-plan` does not need to invent product behavior; it only needs to decide module boundaries, schema details, and task breakdown.

---

## Scope Boundaries

- Not in v0: Jev publish/deliver/relate, Workforce/Evidence experts, Exa external retrieval, Improvement Planner and any automatic improvement, human review UI, MiniCheck.
- Not in v1 (from the Plan's Experimental items, except R30): agent merge/deletion, graph structure reorganization, evaluator self-improvement.
- No full product interface; only the R36 demo pages. v1 human review reuses LangSmith annotation queues first. During development, use LangGraph Studio to observe graph structure and state.
- Promotion only optimizes whole-proposal evaluation. The provision-level diff capability is monitored only through the R37 regression check and does not take part in the promotion decision.
- Analysis features and full multi-version data focus on the AI Act. Other EU proposals serve only as evaluation corpus (R33); no multi-version support for them.
- The system does not rewrite its own code (config-level evolution only).

---

## Key Decisions

- **v0 = end-to-end skeleton, no evolution, delivered in steps**: Friday only guarantees the local main chain and the evaluation baseline; deployment and Jev can slip to v0.1. The pace favors doing it solidly. An evolution demo has no statistical meaning with too few cases.
- **Own fixture + data contract decouple the colleague's data**: avoids blocking, and makes the R1 contract the interface between both sides.
- **Custom LangGraph StateGraph, not the supervisor pattern**: the flow is fixed and the Router is a single classification decision; expert parallelism uses Send + reducer. v0 experts are single structured-output calls through the R32 interface, without tools, compatible with the `claude_code` backend. `create_agent` is introduced only in v1 when retrieval tools are needed, and on the api backend. (Context7: LangChain multi-agent docs, "Custom workflow" pattern)
- **The graph is built from the SystemVersion**: agent composition determines graph structure; prompts / models / router mode are injected via runtime context; replaying candidate versions needs no code change.
- **Shared Impact Board lives in graph state**, not in a cross-thread Store: it must be checkpointed and replayed.
- **Structured output uses strict Pydantic v2 models** (extra fields forbidden), with automatic retry on validation failure; IDs and provenance are filled by the system.
- **Pluggable LLM backend, no vendor lock-in**: during development use the local Claude Code subscription (saves API cost); after getting an OpenAI key, switch via config. The two vendors' main-tier prices are close (Sonnet 5 $2/$10 vs GPT-5.6 Terra $2/$12 per million tokens); the difference is mainly in the lightweight tier (suitable for the judge), so choose by results. Limits: the subscription backend cannot be deployed to Railway, has rate limits, and only supports single calls + structured output (no LangChain-native tool calling); using a personal subscription for development needs confirmation against company policy.
- **Jev sits behind a unified decision interface**: the product was released only two weeks ago, and performance and calibration data are mostly vendor-reported. It must be switchable to an alternative implementation (e.g. open-source openjev or small-model logprob scoring), and calibration is validated in shadow first.
- **SystemVersion / DecisionRecord / Failure Memory / holdout answers are stored in our own Postgres**; LangSmith handles traces, experiments, judge scoring, prompt commits, and the annotation UI. Reason: LangSmith dataset splits do not provide access isolation, and retention is limited.
- **FastAPI + background worker deployed on Railway, not LangGraph Agent Server**: avoids Redis and a license. Railway's per-request limit is about 15 minutes, so long tasks must be async. Keep `langgraph.json` so Studio can be used locally.
- **v1 prompt candidates borrow from GEPA first** (reflect on failure traces → propose edits → Pareto selection). The promotion threshold and candidate archive are built in-house (referencing the archive + lineage of ADAS / DGM, and MASS's order of prompts first, then topology).

---

## Dependencies / Assumptions

- The colleague's AI Act data is expected to be delivered during v1 and can be provided under the R1 contract; if fields do not match, one mapping-layer adaptation is needed.
- **Before P1 starts**: confirm company policy allows using a personal Claude Code subscription for development; if not, an API key must be obtained before P1.
- OpenAI / Anthropic API keys must be in place before v0.1 (P3 deployment, the formal baseline, and R34 all depend on them).
- The Jev API is available and reachable from Railway; its calibration quality for RIA scenarios is unverified (assumption).
- Subsections of the official AI Act impact assessment SWD(2021)84 are enough to support v0's 2–3 golden cases (assumption; which subsections to use needs confirmation during planning).
- v1's 15–30 IA pairs can be obtained from Cellar / Better Regulation (research shows it is feasible; data format and year coverage not actually verified).
- `gh` is not installed on this machine, so GitHub code search was not run this time; prior-art research came from web search and Context7.

---

## Outstanding Questions

### Resolve Before Planning

(None)

### Deferred to Planning

- [Affects R9, R24][Needs research] Besides "Results of impact assessments", sections of the explanatory memorandum such as stakeholder consultation, proportionality, and budgetary implications also restate IA conclusions. The exclusion scope needs to be decided.
- [Affects R12][Technical] Matching granularity of the coverage judge: the official IA describes impacts by policy option and affected group, not by provision.
- [Affects R2, R11][Needs research] Which groups of provisions in the evaluation scenario map to a subsection of SWD(2021)84; which provisions in the demo scenario produce a meaningful amendment diff.
- [Affects R10][Technical] Whether Jev routing uses a single Choice question or one yes/no question per expert; which fits the multi-label case better.
- [Affects R12][Technical] Rubric and matching granularity for the coverage judge (semantic matching by impact vs matching by actor+mechanism).
- [Affects R15][Technical] Whether the v0 task queue uses Postgres `SKIP LOCKED` or FastAPI in-process background tasks (the latter may be enough at v0 scale).
- [Affects R6, R32][Technical] Structured-output strategy per backend (CLI `--json-schema` / native / tool calling), and `claude_code` backend compatibility when v1 experts need to call retrieval tools.
- [Affects R20][Technical] Termination condition and round limit for the multi-round board.
- [Affects R28, R34][Needs research] Based on measured v0 noise, set the minimum holdout size, number of repetitions, and per-metric tolerance (done before v1 planning).
- [Affects all][Needs research] Whether the newer APIs reported by Context7 (Send timeout policy, node error_handler, DeltaChannel, graceful drain) are available in the installed langgraph / langchain versions.

---

## Next Steps

→ `/ce-plan` generates the v0 implementation plan (v1 is planned separately after v0 is delivered)
