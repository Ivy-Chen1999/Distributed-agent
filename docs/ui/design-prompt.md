# UI Design Prompt — WOMM demo frontend

> Copy the "Prompt" section below into a design tool (Claude Design, v0, Figma AI, etc.). Field names match `docs/ui/schema/*.json` and `docs/ui/sample_run.json`, so generated pages can use the mock data directly.

---

## Prompt

Design a web app called **WOMM**, a multi-agent system that assesses the impact of EU regulations (starting with the EU AI Act). The audience is policy analysts and demo viewers (technical and non-technical). The UI must make two things visible: **how multiple AI agents collaborate**, and **why every conclusion can be trusted** (each impact traces back to the exact legal text).

**Tone & style:** clean, calm, information-dense but readable — think audit tool / research notebook, not marketing site. Light and dark themes. Desktop-first (1440px), usable down to tablet. Neutral base palette with one accent; each agent gets a consistent colour used everywhere (Legal = indigo, Fiscal = amber, Stakeholder = teal, Planner = slate, Synthesis = violet, Router/Jev = gold). Status colours: succeeded green, degraded amber, failed red, running blue pulse, queued grey. Monospace only for IDs and provision keys.

**Global:** top bar with product name, current scenario, run status badge, and the system version id (e.g. `sv_44a681965332`) shown as a small copyable chip. Every page needs loading (skeletons), empty and error states. All AI-generated text is plain text (never render as HTML).

### Page 1 — Run

1. **Scenario picker + Run button.** Two kinds: *Evaluation* ("no prior version → AI Act proposal", e.g. "Provider compliance costs", "SME impacts") and *Demo diff* ("proposal → final text"). Show scenario description and number of provisions.
2. **Agent graph (main visual, ~60% width).** A left-to-right flow: `Regulatory diff → Impact Planner → Router (Jev) → [Legal | Fiscal | Stakeholder in parallel] → Shared Impact Board → Citation check → Synthesis → Impact Dossier`. Each node is a card showing status (queued / running / done / failed), elapsed time, and token count. The Router node shows, per expert, the decision (`relevant` / `not_relevant` / `error`) with a probability bar and a **"shadow"** tag meaning "logged but not enforced — all experts still run". A failed expert card turns red with its `error_kind` (auth, rate_limit, timeout, schema_invalid, process_error) and the run continues (overall status becomes *degraded*).
3. **Live Board feed (right rail).** Findings stream in as compact cards coloured by agent: provision key, affected actor, one-line impact, confidence. After citation check, each card gets a badge: ✓ grounded or ⚠ evidence unresolved.
4. **Run summary strip:** status, total time, total tokens/cost, grounding rate ("quote existence rate", e.g. 3/4 = 75%).

### Page 2 — Impact Dossier

1. **Header:** scenario, status, run id, system version, counts (impacts, open questions, disagreements, failed experts).
2. **Impacts list**, groupable by *affected actor* or by *domain (agent)*. Each impact card: summary sentence, contributing agents as coloured dots, confidence, "merged from N findings". If synthesis failed, show a banner "Synthesis unavailable — findings listed unmerged".
3. **Expandable provenance chain** (the key interaction) for each finding inside an impact, shown as a vertical chain of 5 steps:
   `Regulatory change (provision key, article, change type added/modified/removed)` → `Affected actor` → `Mechanism` → `Evidence quote` → `Source`.
   The evidence step shows the quote, and clicking it opens a side panel with the **full source text with the quote highlighted** in context.
4. **Impact chains:** small horizontal chain diagrams linking impacts in causal order (e.g. "Reporting requirement → New compliance process → Additional staffing → Higher cost → Greater SME burden").
5. **Disagreements:** side-by-side comparison of conflicting findings (two columns, agent colour headers, a note explaining the conflict). Never hide one side.
6. **Open questions** section (with reason tag: "evidence unresolved" or "raised by synthesis") and **Failed experts** section.
7. **Demo diff scenario only:** a two-column provision comparison view (proposal vs final text, with inline change highlighting; note that article numbers may differ but the provision key is the same).

### Page 3 — Evaluation (later version; design for consistency)

Table of system versions × metrics: coverage, grounding (quote existence), omissions, efficiency (time, tokens, cost), with a "baseline kind" tag (smoke / reference) and backend/model labels. Drill-down per case: expected impacts from the official impact assessment, each marked hit / missed, with the matching dossier impact.

### Page 4 — Evolution (later version)

Version lineage tree (parent → child). Selecting a candidate shows a diff vs the current version (prompt changes, added/removed agents), scores on train/validation vs **hidden holdout**, and the decision **PROMOTE** / **REJECT** with reason. When a candidate adds a new specialist agent (e.g. "Administrative Burden Agent"), highlight it prominently in the lineage and in the agent graph.

### Sample content to use

- Scenario: "Provider compliance costs (IA §6.1.3)", status *degraded*, system version `sv_44a681965332`.
- Impact I1: "Providers of high-risk AI systems must run a documented risk management system, creating recurring compliance cost." — merged from Legal + Fiscal findings; provision `ai_act/high_risk/risk_management` (Art. 9); quote: "A risk management system shall be established, implemented, documented and maintained in relation to high-risk AI systems."
- Impact I2: "SMEs get priority access to regulatory sandboxes, partly offsetting the burden." — Stakeholder; Art. 55; quote: "provide small-scale providers and start-ups with priority access to the AI regulatory sandboxes".
- Disagreement: Fiscal sees a net burden on SMEs; Stakeholder sees sandboxes as offsetting.
- Router (shadow): Legal relevant 0.97, Fiscal relevant 0.88, Stakeholder not_relevant 0.35.
- Failed expert: Workforce — timeout.
- Open question: "Does the conformity assessment cost fall on importers as well?" (evidence unresolved).

Deliver: the 4 pages (pages 1–2 fully detailed, 3–4 lighter), the node card, finding card, provenance chain, and disagreement components as reusable components, plus the state variants (loading / empty / error / degraded).
