# WOMM Console ↔ API contract

The console (`web/`) is served by the API itself at `/` (same origin), so it calls relative paths.
In development it runs on the Vite dev server and proxies these paths to `http://localhost:8000`.

All endpoints except `/livez`, `/health` and static files need `Authorization: Bearer <token>`.
The console asks for the token once and keeps it in `localStorage` (`womm.token`). A 401 clears it
and asks again.

Field shapes come from the Pydantic models; JSON Schemas are in `docs/ui/schema/`.

## Endpoints

### `GET /health` (public)
`{ "status": "ok"|"degraded", "system_version": "sv_…", "backend_ready": bool }` — 503 when not ready.

### `GET /system`
```json
{
  "version_id": "sv_3d8f3a48f955",
  "name": "v0-baseline",
  "source_path": "system_versions/v0-baseline.yaml",
  "description": "…",
  "roles": {
    "planner":   {"backend": "claude_code", "model": "claude-sonnet-5", "prompt": "prompts/planner.md", "prompt_hash": "…"},
    "synthesis": {…}, "judge": {…},
    "expert:legal": {…}, "expert:fiscal": {…}, "expert:stakeholder": {…}
  },
  "experts": [{"id": "legal", "domain": "legal", "backend": "claude_code", "model": "claude-sonnet-5"}],
  "router": {"mode": "shadow"|"active"|"off", "decider": "jev"|"stub"},
  "max_parallel_llm_calls": 3,
  "available_backends": ["claude_code", "api"],
  "backend_ready": true,
  "backend_error": null,
  "code": {"git_sha": "…", "dirty": false, "claude_cli_version": "2.1.284 (Claude Code)"},
  "self_check": {"passed": true, "checks": {"auth_preflight": true, "no_tools": true, …}, "problems": []} | null
}
```

### `GET /scenarios`
List of Scenario (`scenario_id`, `kind` evaluation|demo, `description`, `before_version`,
`after_version`, `provision_keys`, `ia_reference`).

### `GET /scenarios/{scenario_id}/sources`
```json
{
  "scenario_id": "eval_sme_impacts",
  "changes": [{"provision_key": "…", "kind": "added"|"modified"|"removed",
               "before": {"article": "71", "source_id": "…"} | null,
               "after":  {"article": "99", "source_id": "…"} | null}],
  "sources": [{"source_id": "com2021_206/art_55", "title": "…", "kind": "provision"|"memorandum"|"annex", "text": "…"}]
}
```
Used for the provenance side panel: find the quote inside `text` and highlight it. Matching must
tolerate whitespace/quote-style differences (the backend matched after normalisation).

### `POST /runs`
Body: `{"scenario_id": "eval_sme_impacts", "overrides": {"router_mode": "shadow"|"active", "backends": {"planner": "api", "expert:legal": "claude_code"}}}`
(`overrides` optional). Overrides produce a derived SystemVersion with its own `version_id`.
→ 202 `{"run_id": "run_<uuid>", "status": "queued", "system_version": "sv_…"}`.
Errors: 404 unknown scenario, 422 invalid override, 429 queue full, 503 backend unavailable.

### `GET /runs?limit=20`
```json
{"runs": [{"run_id": "…", "scenario_id": "…", "status": "…", "system_version": "…",
           "created_at": "…", "started_at": "…", "finished_at": "…", "duration_s": 287.5,
           "impacts": 25, "grounding": {"passed": 64, "total": 64}, "error_kind": null}]}
```
Newest first.

### `GET /runs/{run_id}`
```json
{
  "run_id": "…", "scenario_id": "…", "status": "queued|running|succeeded|degraded|failed|no_changes",
  "system_version": "…", "error_kind": null, "error": null,
  "created_at": "…", "started_at": "…", "finished_at": "…",
  "nodes": {"planner": "finished", "expert_legal": "started", …},
  "decisions": [DecisionRecord], "grounding": {"passed": 64, "total": 64},
  "board": [ImpactFinding], "failures": [ExpertFailure], "usage": [CallUsage],
  "code_identity": {…},
  "citable_sources": [{"source_id": "…", "title": "…", "kind": "provision|annex|memorandum|obligations", "text": "…"}],
  "dossier": ImpactDossier
}
```
`decisions`, `grounding`, `board`, `failures`, `usage`, `code_identity`, `citable_sources` appear
once the run has finished; `citable_sources` (every source an expert could cite, including
obligation views) is null for runs saved before it existed, so fall back to the scenario sources.
An `ExpertFailure` with `error_kind` `no_data_in_scope` is a scoped expert that was not called
because nothing was within its scope; it does not degrade the run on its own. `dossier` only for succeeded/degraded/no_changes. While running, use the events.
`error_kind` on a failed run is the failure class (`timeout`, `auth`, `orphaned`, `cancelled`,
`process_error`, …; `pipeline_failed` when the pipeline failed without a tagged cause).

### `GET /runs/{run_id}/events?after=N&limit=500`
`{"run_id": "…", "events": [{"seq": 1, "node": "planner", "event": "started"|"finished"|"failed", "payload": {…}, "at": "ISO"}], "next_after": N}`
Nodes: `planner`, `router`, `expert_<id>`, `validate` (citation check), `synthesis`, `assemble`
(dossier). Payload summaries: `findings` {agent: n}, `failures` {agent: error_kind},
`decisions` [...], `dispatched` [...], `focus_areas` n, `grounding` {passed,total}, `supported`,
`unsupported`, `error`, `status`, `impacts`.
Poll every ~1s while running. For a finished run the same events drive a replay at N× speed using
the `at` timestamps.

### `POST /runs/{run_id}/ask`
Body `{"question": "Who carries the penalty risk?"}` →
`{"answer": "…", "cites": ["I19", "f_…"], "covered": true}`.
Answers only from that run's dossier, with that run's synthesis backend when this server has it; `covered=false` when the dossier does not address it.
409 if the run has no dossier, 429 when 4 questions are already in flight, 502 when the backend
fails, 504 when no answer arrives within 120 s.

## Evolution page (R36 page 4)

Read-only views of the self-evolution archive (self-evolution plan U9). Schemas:
`docs/ui/schema/evolution_lineage.schema.json`, `evolution_candidate.schema.json`,
`evolution_diff.schema.json`. The page carries no promotion logic: labels, reasons and the
R37 message come from the API as the gate wrote them.

Holdout results reach these endpoints only as published decision summaries
(`promotion_decisions`, written by `womm evolve promote` when `evals/promotion_policy.yaml` sets
`publish_summary: true`): per-metric mean delta, CI and noise, the mode and the reasons. No
response field is a case id or a scenario id. The API never reads the holdout database.

### `GET /evolution/lineage`
```json
{
  "publish_summary": false,
  "nodes": [{
    "version_id": "sv_…", "name": "v1.0-unscoped+…", "parent_id": "sv_…" | null,
    "cycle_id": "cycle_…" | null, "origin": "seed"|"gepa"|"topology"|"twin"|"manual",
    "created_at": "ISO", "badges": ["topology", "promoted", "dev-only"],
    "twins": ["sv_…"], "decision": "promoted"|"rejected"|null,
    "label": "promoted (weak threshold: directional)" | null,
    "new_expert": "workforce" | null, "r37_regression": true|false|null
  }]
}
```
Oldest first. api twins (`twin_of`) are collapsed into their dev candidate's `twins`; a
decision on a twin is shown on its dev node. `badges`: the origin, then the latest published
decision (`promoted`/`rejected`) and `dev-only` for a dev-mode decision. `publish_summary` is
null when the policy file is not deployed (the API image has no `evals/`).
`r37_regression` compares the version's R37 diff-check score with the incumbent of its latest
decision, else its parent; null when either was not checked.

### `GET /evolution/candidates/{version_id}`
```json
{
  "node": LineageNode,
  "experts": [{"id": "legal", "domain": "legal", "router_gloss": null}],
  "summary": {"experts_added": [], "added_experts": {}, "prompts_changed": ["expert:fiscal"],
              "router_gloss_changed": {}, "retrieval": null} | null,
  "rationale": "…" | null, "proposer": {"model": "…", "prompt_hash": "…"} | null,
  "new_expert": {"id": "workforce", "domain": "workforce", "router_gloss": "…",
                 "prompt_text": "…", "target_pattern": {"kind": "missed_impact",
                 "category": "social_environmental", "owner": "none"}, "rationale": "…"} | null,
  "metrics": [{"split": "train"|"val", "judge_version": "jv_…",
               "metrics": {"coverage": {"mean": 0.7, "sd": null, "n": 3, "noise_sd": 0.02}}}],
  "holdout": {"status": "published"|"not_submitted"|"sealed", "message": "…",
              "decisions": [{"gate_id": "gate_…", "created_at": "ISO",
                             "candidate_version": "sv_…", "incumbent_version": "sv_…",
                             "mode": "statistical"|"weak"|"dev", "deployable": true,
                             "decision": "promoted"|"rejected", "label": "…",
                             "reasons": ["grounding_regression"], "notes": ["…"],
                             "deltas": {"coverage": {"mean_delta": 0.06, "ci95_low": null,
                                                     "ci95_high": null, "n_cases": 8,
                                                     "noise_sd": 0.05}},
                             "n_proposals": 3, "flags": ["insufficient_proposals"]}]},
  "r37": {"status": "available"|"not_run", "score": {"mean": 0.6, "sd": 0.02, "n": 3} | null,
          "reference_version": "sv_…" | null, "reference_score": {…} | null,
          "regression": true|false|null, "message": "…"}
}
```
An api twin's id opens its dev candidate. `metrics` holds split-level aggregates of the latest
full-split replay batch per (split, judge); `noise_sd` is the pooled within-case run-to-run SD
(the noise band). `holdout.status`: `published` when a summary exists; otherwise
`not_submitted` when the policy publishes summaries, else `sealed` (the decision, if any, is
only in the holdout audit; `womm evolve show <id>` reads it locally). The R37 check is
monitoring only: a regression never changes a decision. 404 for an unknown version.

### `GET /evolution/candidates/{version_id}/diff`
`{"version_id": "sv_…", "parent_id": "sv_…" | null, "summary": {…} | null,
"prompts": {"expert:fiscal": "--- prompts/…\n+++ prompts/evolved/…\n@@ …"}}` — one unified diff
per changed prompt (a new expert's prompt diffs against `/dev/null`). 404 for an unknown
version.
