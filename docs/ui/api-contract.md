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
  "dossier": ImpactDossier
}
```
`decisions`, `grounding`, `board`, `failures`, `usage`, `code_identity` appear once the run has
finished; `dossier` only for succeeded/degraded/no_changes. While running, use the events.

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
Answers only from that run's dossier; `covered=false` when the dossier does not address it.
409 if the run has no dossier.
