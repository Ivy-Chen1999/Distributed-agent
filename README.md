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
uv run womm scenarios --json                # list fixture scenarios
uv run womm selfcheck --json                # auth + claude_code isolation self-check
uv run womm run eval_sme_impacts            # one run; writes runs/<run_id>.json
uv run womm eval --baseline                 # golden cases -> scores + LangSmith experiment
uv run womm eval --split val --repetitions 3   # dev noise check on any backend
uv run womm eval --split val --repetitions 6 --formal   # the R34 noise run (api backend only)
uv run langgraph dev                        # LangGraph Studio (graph view)
```

Every command accepts `--json` (data on stdout, diagnostics on stderr) and `--system-version`.
Exit codes: 0 ok, 1 run failed / eval aborted, 2 bad input, 3 backend error, 4 run degraded
(`womm --help` lists them). With `DATABASE_URL` set, `womm eval` also writes failure records
(R14b) to Postgres.

Reports and LangSmith experiments record the `split` (`train`, `val`, or `mixed`) and give a
summary per split. Holdout cases are never selectable here.

**R34 noise protocol.** Run `womm eval --split val --repetitions 6 --formal`. Single runs are
noise (`docs/solutions/evaluation/single-run-scores-are-noise.md`), so the protocol pools 6
runs per case and reports the per-case spread under `noise` and `summary_by_split`. `--formal`
marks the run as the R34 noise run (`run_kind: r34_noise`). It is the only mode that refuses
non-api backends, and it needs one whole split, at least 6 repetitions and a clean working tree.
`--baseline` on the `claude_code` backend stays allowed and is tagged `smoke`.

## API (v0.1)

```bash
docker-compose up -d                        # local Postgres on :55432
export DATABASE_URL=postgresql://womm:womm@localhost:55432/womm WOMM_API_TOKEN=dev-token
uv run uvicorn --factory womm.api.app:create_app --port 8000 --timeout-keep-alive 30
```

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | liveness, system version, whether backends are ready |
| `GET /scenarios` | bearer | fixture scenarios |
| `POST /runs` `{"scenario_id": ...}` | bearer | start a background run (202, UUID run id) |
| `GET /runs/{id}` | bearer | status, per-node states, error; Impact Dossier once finished |
| `GET /runs/{id}/events?after=N` | bearer | node started/finished/failed events, in order |

Deployed: https://womm-api-production.up.railway.app (`railway up --service womm-api` redeploys).
`GET /livez` is the platform health check (liveness); `/health` is readiness.

The app refuses to start without `WOMM_API_TOKEN` and `DATABASE_URL`. Runs execute in-process
(at most 2 at a time); runs interrupted by a restart are marked `failed` / `orphaned` on the next
start. The `Dockerfile` / `railway.json` build the same app; deployed versions must use the `api`
backend (`system_versions/v0.1-api.yaml`, needs `OPENAI_API_KEY`), since the Claude Code
subscription is local only.

## LLM backends

Configured per role in `system_versions/*.yaml` (a SystemVersion; its id is a content hash of the
file and the prompts it references):

- `claude_code`: runs `claude -p` in an isolated subprocess (no tools, no MCP, no user settings,
  hooks or CLAUDE.md; temp cwd; env allowlist). An isolation self-check runs before use and the
  backend refuses to run if it fails. Local development only.
- `api`: LangChain `init_chat_model` with structured output (OpenAI / Anthropic keys).
- `fake`: scripted outputs for tests.

## Data

`data/fixtures/ai_act/` is built by `scripts/build_fixture.py` (proposal COM(2021) 206 and
Regulation (EU) 2024/1689). Article texts come from the colleague's data pipeline,
[calderonsamuel/course-cs-project-fall-2026-data](https://github.com/calderonsamuel/course-cs-project-fall-2026-data),
pinned by `PIPELINE_COMMIT` in the script and by sha256 in `downloads.json`. Its proposal-to-final
alignment must agree with `crosswalk.yaml`, or the build fails. The explanatory memorandum comes
from EUR-Lex Cellar. `--articles-from cellar` builds the articles from Cellar instead. To move the
pin, change `PIPELINE_COMMIT`, rebuild with `--accept-upstream-changes`, and review the fidelity
report it prints. Runtime code reads only the committed fixture. The delivery format for the full dataset is the
R1 data contract: `docs/data-contract.schema.json` (generated from `womm.models.regulation`).
Every provision needs a `provision_key` that stays stable across versions (articles are renumbered
between proposal and final text).

Golden cases (`evals/golden/`) paraphrase the official impact assessment SWD(2021) 84. They are
never shown to the agents.

New golden cases are drafted by `scripts/draft_golden_case.py` and checked by isolated LLM
judges. Humans review only the items an LLM cannot be trusted with:

- Train/val drafts go to `evals/golden/drafts/` and are reviewed in a GitHub PR, following
  `docs/eval/golden-review-guide.md`. `tests/eval/test_golden_drafts.py` blocks a ready PR
  until every item that needs a human is decided. A draft PR (`WOMM_DRAFT_PR=true` in CI) may
  still have pending items.
- After merge, `scripts/publish_golden_cases.py` writes `evals/golden/case_*.yaml` and deletes
  the draft.
- Holdout drafts stay in `.cache/drafts/`. They are verified locally with
  `scripts/verify_golden_case.py` and handed to the holdout importer. They never go to `evals/`.

## Tests

```bash
uv run pytest -q                 # unit + fake end-to-end tests (+ Postgres tests if it is up)
uv run pytest -m live -q         # real claude CLI calls
uv run ruff check src tests scripts
```
