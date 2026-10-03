---
date: 2026-10-02
topic: next-stage-cost-and-methodology
origin: docs/brainstorms/2026-09-28-womm-phased-requirements.md
---

# Next Stage: Cost Estimation and the Rest of the Colleague's Methodology

## Problem Frame

The colleague's repository
([calderonsamuel/course-cs-project-fall-2026-data](https://github.com/calderonsamuel/course-cs-project-fall-2026-data),
commit `16b5807`) is a full methodology, not only data. The two current plans adopt part of it:

- `docs/plans/2026-10-02-001-feat-pipeline-data-import-plan.md`: their provisions and alignment as
  our data layer.
- `docs/plans/2026-10-02-002-feat-scoped-provision-retrieval-plan.md`: data-access separation, the
  single-agent baseline, and obligations as structured evidence.

Everything else in their method is collected here, so that nothing is lost. The largest piece is
**cost estimation**: predicting where a law's implementation cost will land, and checking that
prediction against independent estimates. WOMM's Fiscal expert today describes cost impacts in
prose. It does not classify effort, estimate magnitude, or say who pays.

This document is a staged backlog with product decisions, not a plan. Each stage is planned
separately with `/ce-plan` when it starts.

Sources read in full: their `README.md`, `docs/project-status.md`, `docs/org-graph-schema.md`, and
`docs/schemas/01–06` plus the validation report.

---

## What the colleague's method contains, and where it lands in WOMM

| # | Element of their method | Their doc | WOMM status |
|---|---|---|---|
| 1 | Provisions, unit ids, proposal-to-final alignment | schemas 02, 03 | Plan 001 |
| 2 | Rule-based obligations (addressee, condition, action, timing, references, applies_from, delta) | schema 04 | Plan 002 (as evidence) |
| 3 | Agents split by visible data, with a stated "why the split is real" | status §5 | Plan 002 |
| 4 | Single-agent baseline with the same output schema | status §5 | Plan 002 |
| 5 | Claims must cite unit, obligation or register ids, checked against files | status §5 rules | Plan 002 (verbatim check) |
| 6 | Validation data never in any agent's input | status §5, §6 | Already in WOMM (the IA is hidden) |
| 7 | LLM obligation enrichment: implied addressee for the 430 `unspecified` duties, normalised action, **effort type** (new process, documentation, registration, notification, human oversight, training, assessment), resolved cross-references | status §5 "Legal extraction" | **Stage A** |
| 8 | Agent output extends the schemas (`source = agent:<name>`) and never overwrites a rule field. The validator checks agent output like deterministic layers | status §5 rules | **Stage A** |
| 9 | Adversarial reviewer: sees the other agents' outputs plus the proposal-to-final delta, looks for costs the ex-ante assessment could not see, and rejects claims without citations | status §5 | **Stage A** |
| 10 | Cost estimation per obligation: who pays, effort type, magnitude class, when (applies_from) | status §5 "Cost location" | **Stage B** |
| 11 | "Late-added obligations" lens: 387 of 975 final duties sit in text added after the proposal, and 24 of the 33 deployer duties are new | status §3 | **Stage B** |
| 12 | Validation of cost hotspots against independent estimates: overlap with the obligations experts flag as costly, rank correlation with standard-cost-model effort | status §6 | **Stage B** (EU level), **Stage C** (municipal) |
| 13 | Organization graph: municipalities, departments, systems, suppliers, CBS budget lines, with provenance and confidence on every node and edge | org-graph-schema, schema 05 | **Stage C** |
| 14 | System classification: AI system or not, Annex III point, deployer or provider role. It must not see other municipalities' self-classification | status §5 | **Stage C** |
| 15 | Applicability: obligation → system `applies_to` edges, with each condition checked (high-risk, public authority, Annex III 5(b)/(c) for the FRIA) | status §5 | **Stage C** |
| 16 | Cost location: domain budget line vs overhead (0.4) vs supplier. Economic split into own staff, hired staff and suppliers. Existing capacity lowers effort (an existing DPIA lowers Art 26(9)) | org-graph-schema "Where compliance cost lands" | **Stage C** |
| 17 | Validation against VNG (qualitative, about 60 municipalities) and the PBLQ standard cost model | status §6 | **Stage C** |
| 18 | Field-level validator with a written report (702 checks) | schemas README | **Stage D** |
| 19 | Point-level ids for citations (`…art26.par9`) | schema 02 | **Stage D** |
| 20 | Application dates moved by the Digital Omnibus (Regulation (EU) 2026/1744); support for consolidated versions | status §4, §7.5 | **Stage D** |
| 21 | Per-act configuration (actor categories, renames, dates), so other acts and policies can follow | status §4, §7.5 | **Stage D** (with origin R33) |
| 22 | Sequential pipeline topology (extraction → classification → applicability → cost) | status §5 | Not a stage of its own. Stage C adds these steps as nodes inside WOMM's graph, not as a separate pipeline |

---

## Stages and requirements

**Stage A: Obligation enrichment and the adversarial reviewer** (v1.1; moved from v1 on 2026-10-03)
- R1. An offline LLM pass enriches every duty and prohibition. It adds the implied addressee where
  the rule pass says `unspecified`, a normalised action, one or more effort types from a fixed list,
  and resolved cross-references. These are added as new fields with `source = agent:<name>` and a
  citation to the unit. Rule fields are never overwritten.
- R2. The corpus validator checks the enrichment like any other layer: every field is present,
  values are from the allowed lists, and every cited unit exists. When enrichment lands, the
  Stakeholder span fallback from plan 002 is removed.
- R3. An adversarial reviewer joins the expert roster. It sees the other experts' validated findings
  plus the delta. It challenges claims without support, and it adds findings about costs that only
  appear in text added after the proposal. It is the first candidate for the origin R30 "new expert
  is created and promoted" demo.
- Open decision: run enrichment on all 975 duties or only the 430 `unspecified` ones (their open
  decision §7.1). It is one offline run, and its cost is measured before deciding.

**Stage B: EU-level cost estimation**
- R4. A cost estimation step turns each relevant obligation into a cost record: who pays (actor
  category, public or private), effort type, magnitude class (one-off or recurring, plus an
  ordinal size), and when (applies_from). Every record cites the obligation and the unit.
- R5. The dossier gets a cost-hotspot view: which obligations, actors and effort types concentrate
  cost. Obligations added or modified after the proposal are marked.
- R6. The cost records are scored against the cost sections of the official impact assessment
  SWD(2021) 84. Golden case 01 already covers 6.1.3, costs and administrative burdens. The
  scoring follows the colleague's idea: overlap of the obligations flagged as costly, plus rank
  agreement where the IA gives figures. The IA stays hidden from every agent.
- R7. The proposal-to-final run reports "costs the ex-ante IA could not see": the cost records on
  obligations added after the proposal. Our IA-based scoring cannot judge them, so they are
  reported, not scored. This is where the colleague's late-added thesis meets origin R37.

**Stage C: Organization case study (Dutch municipalities)**
- R8. The colleague's organization graph is imported as a separate, optional "deployment context".
  It holds 3 municipalities, 127 systems, 42 suppliers and 207 budget lines, keeps provenance and
  confidence, and is pinned like the corpus.
- R9. System classification, applicability (`applies_to`) and cost location run as WOMM experts.
  Each has the data scope from the colleague's table. Classification never sees other
  municipalities' self-classification or the obligations.
- R10. Cost location places each applicable obligation's effort on a domain budget line, on
  overhead (0.4) or on a supplier. It gives a magnitude class and takes existing capacity into
  account.
- R11. Results are validated against VNG and the PBLQ standard cost model, which are never in any
  agent's input. The scoring measure is defined before the stage starts (their open item §6).

**Stage D: Data quality and generality**
- R12. A field-level validator report for the corpus and the enrichment layers.
- R13. Point-level source ids for citations, introduced together with a planned re-baseline.
- R14. Application dates updated for the Digital Omnibus. Consolidated versions become a supported
  version status.
- R15. A per-act configuration file, so another act can be imported without code changes. This
  ties in with origin R33 (other EU proposals as evaluation corpus).

---

## Success Criteria

- Nothing in the colleague's methodology is silently dropped. Every element is either implemented
  or has a stage here.
- After Stage B, the Fiscal side of a dossier answers "who pays, for what kind of effort, how much,
  and from when", with every claim traceable to an obligation. It is scored against the official
  IA.
- After Stage C, the same obligations can be placed on a real organization's budget lines and
  suppliers, and checked against VNG and PBLQ.

---

## Scope Boundaries

- Stages are sequential. Stage C does not start before Stage B's cost records exist.
- VNG and PBLQ material is never put in any agent input, and is never put in `data/corpus/` or
  `data/fixtures/`.
- Whether the Commission IA is ever shown to the adversarial reviewer, which is the colleague's
  open decision §7.2: in WOMM the answer is no, because the IA is our benchmark (origin R11).

---

## Dependencies / Assumptions

- Plans 001 and 002 are done first.
- Stage A needs an API key for the offline enrichment pass, or a long run on the `claude_code`
  backend.
- Stage C depends on the colleague's organization data staying available. The VNG analysis is
  located but not read. Whether the PBLQ model and figures are available is unknown (their §6).
- Assumption: SWD(2021) 84 gives enough cost figures per obligation group to score Stage B. This
  must be checked when Stage B is planned.

---

## Outstanding Questions

### Resolve Before Planning (per stage, when it starts)

- [Stage A] Enrichment on all 975 duties or on the 430 `unspecified` only.
- [Stage B] The effort-type list and the magnitude scale: take the colleague's seven effort types
  as they are, or adapt them to the IA's cost categories.
- [Stage C] Unit of cost location: budget line or department (their open question). Systems out of
  use: keep or drop. Reference figure: budget or annual accounts.

---

## Next Steps

-> Implement plans 001 and 002. Then `/ce-plan` Stage A from this document.
