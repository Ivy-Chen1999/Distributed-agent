# UI data contract (demo frontend)

Page requirements: R35 / R36 in `docs/brainstorms/2026-09-28-womm-phased-requirements.md`.
Design prompt for design tools: `docs/ui/design-prompt.md`.

- `schema/*.schema.json`: exported from the backend Pydantic models; the single source of truth for
  field names. Regenerate with `uv run python scripts/export_ui_contract.py`.
- API: see the README "API" section (bearer token; `POST /runs` then poll).
- `sample_run.json`: a real run of scenario `eval_sme_impacts` (AI Act proposal Art 53-55, 71) on
  the claude_code backend. Use it as mock data. Router decisions come from the stub decider
  (probability is null until Jev is wired in, U8).

## Page-to-field mapping

| Page | Data source | Key fields |
|---|---|---|
| Run page: agent graph and node status | `GET /runs/{id}` (`nodes`: node -> started/finished/failed) and `GET /runs/{id}/events?after=N` (poll; payloads carry findings counts, failures, router decisions, grounding) | `status`, `decisions[].subject / decision / probability / mode` (shadow tag), `board[].agent` (colour per expert) |
| Impact Dossier page | `dossier` | `impacts[].summary`, `impacts[].findings[]` (provenance chain: `provision_key` → `mechanism` → `evidence[].quote` → `evidence[].source_id`), `confidence`, `disagreements` (side by side), `open_questions`, `failed_experts`, `chains` |
| Evaluation page (v1) | LangSmith experiments | TBD |
| Evolution page (v1) | SystemVersion lineage | TBD |

Status values: `queued`, `running`, `succeeded`, `degraded` (some experts failed, or synthesis
failed), `failed`, `no_changes`. Every page needs loading, empty and error states.

Note: Impact Dossier text is LLM-generated. Render it as escaped plain text, never as HTML.
