# UI data contract (demo frontend)

Page requirements: R35 / R36 in `docs/brainstorms/2026-09-28-womm-phased-requirements.md`.
Design prompt for design tools: `docs/ui/design-prompt.md`.

- `schema/*.schema.json`: exported from the backend Pydantic models; the single source of truth for
  field names. Regenerate with `uv run python scripts/export_ui_contract.py`.
- `sample_run.json`: one example run. The content is illustrative (only the quotes are real AI Act
  text); use it as mock data. It will be replaced with a real run once the pipeline (U7) works.

## Page-to-field mapping

| Page | Data source | Key fields |
|---|---|---|
| Run page: agent graph and node status | v0.1 `GET /runs/{id}` and `/events` (U11); until then `status` + `decisions` | `status`, `decisions[].subject / decision / probability / mode` (shadow tag), `board[].agent` (colour per expert) |
| Impact Dossier page | `dossier` | `impacts[].summary`, `impacts[].findings[]` (provenance chain: `provision_key` → `mechanism` → `evidence[].quote` → `evidence[].source_id`), `confidence`, `disagreements` (side by side), `open_questions`, `failed_experts`, `chains` |
| Evaluation page (v1) | LangSmith experiments | TBD |
| Evolution page (v1) | SystemVersion lineage | TBD |

Status values: `queued`, `running`, `succeeded`, `degraded` (some experts failed, or synthesis
failed), `failed`, `no_changes`. Every page needs loading, empty and error states.

Note: Impact Dossier text is LLM-generated. Render it as escaped plain text, never as HTML.
