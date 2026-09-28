# WOMM

Self-evolving multi-agent Regulatory Impact Assessment (RIA), starting with the EU AI Act.

- Requirements: `docs/brainstorms/2026-09-28-womm-phased-requirements.md`
- v0 plan: `docs/plans/2026-09-28-001-feat-womm-v0-skeleton-plan.md`
- UI data contract and design prompt: `docs/ui/`

## Pipeline (v0)

```
fixture scenario -> provision-key diff -> Impact Planner -> Router (shadow)
  -> Legal | Fiscal | Stakeholder (parallel) -> Shared Impact Board
  -> citation validation -> Synthesis -> deterministic assembly -> Impact Dossier
```

Every impact in the dossier traces back to a provision key, an evidence quote, and a source id.
Quotes are checked verbatim against the source text (grounding = quote existence rate).

## Setup

```bash
uv sync
cp .env.example .env   # then fill in LANGSMITH_API_KEY etc.
claude /login          # the claude_code backend uses your local Claude Code subscription
```

`.env` is loaded automatically by the CLI. WOMM traces to its own LangSmith project
(`LANGSMITH_*`); the `CC_LANGSMITH_*` variables belong to Claude Code's own tracing plugin.

## Usage

```bash
uv run womm scenarios                       # list fixture scenarios
uv run womm selfcheck                       # auth + claude_code isolation self-check
uv run womm run eval_sme_impacts            # one run; writes runs/<run_id>.json
uv run womm eval --baseline                 # golden cases -> scores + LangSmith experiment
uv run womm eval --case case_02_sme_impacts --local
uv run langgraph dev                        # LangGraph Studio (graph view)
```

## LLM backends

Configured per role in `system_versions/*.yaml` (a SystemVersion; its id is a content hash of the
file and the prompts it references):

- `claude_code`: runs `claude -p` in an isolated subprocess (no tools, no MCP, no user settings,
  hooks or CLAUDE.md; temp cwd; env allowlist). An isolation self-check runs before use and the
  backend refuses to run if it fails. Local development only.
- `api`: LangChain `init_chat_model` with structured output (OpenAI / Anthropic keys).
- `fake`: scripted outputs for tests.

## Data

`data/fixtures/ai_act/` is built from EUR-Lex Cellar by `scripts/build_fixture.py` (proposal
COM(2021) 206 and Regulation (EU) 2024/1689). The delivery format for the full dataset is the
R1 data contract: `docs/data-contract.schema.json` (generated from `womm.models.regulation`).
Every provision needs a `provision_key` that stays stable across versions (articles are renumbered
between proposal and final text).

Golden cases (`evals/golden/`) paraphrase the official impact assessment SWD(2021) 84. They are
never shown to the agents.

## Tests

```bash
uv run pytest -q                 # unit + fake end-to-end tests
uv run pytest -m live -q         # real claude CLI calls
uv run ruff check src tests scripts
```
