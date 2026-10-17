# WOMM Console

React 19 + TypeScript + Vite frontend for the WOMM API. It reproduces the Claude Design prototype in
`design/WOMM Console.dc.html` (kept as the visual reference; `design/support.js` is its preview runtime
and is not shipped) and reads every value from the API described in `../docs/ui/api-contract.md`.

## Develop

```sh
npm install
npm run dev            # http://localhost:5173
```

The dev server proxies `/runs`, `/scenarios`, `/system`, `/health` and `/livez` to the API on
`http://localhost:8000` (override with `WOMM_API_URL=http://host:port npm run dev`). Start the API
from the repo root, for example `uvicorn --factory womm.api.app:create_app --port 8000 --timeout-keep-alive 30`.

On first load the console asks for the bearer token (`WOMM_API_TOKEN`) and keeps it in
`localStorage` under `womm.token`. A 401 clears it and asks again.

## Build, test, typecheck

```sh
npm run build          # tsc --noEmit, then vite build into dist/
npm test               # vitest: view-models, API client, per-screen render smoke tests
npm run typecheck      # tsc for the app, then for e2e/ (e2e/tsconfig.json)
npm run preview        # serve dist/ with the same proxy
```

The API serves the built `dist/` at `/` in production, so all API calls use relative paths.

## End-to-end tests (Playwright)

```sh
npx playwright install chromium   # once
npm run e2e                       # builds dist/, starts the API on a scripted fake LLM, runs e2e/
npx playwright show-report        # HTML report with traces and screenshots of failures
```

Needs `uv` and the local Postgres from `docker-compose up -d` (`postgresql://womm:womm@localhost:55432`;
override the server with `WOMM_E2E_PG_URL`). `playwright.config.ts` starts the API from the repo root
with `uv run uvicorn --factory womm.api.e2e:create_e2e_app --port 8765`, after `npm run build` and
`python -m womm.api.e2e create-db`, which creates a fresh database for the run (Playwright starts its
webServer before `globalSetup`, so creation happens there; `e2e/global-setup.ts` checks that it is empty
and `e2e/global-teardown.ts` drops it; `WOMM_E2E_KEEP_DB=1` keeps it). Specs run one at a time
against that server, because the console always opens the newest run.

`src/womm/api/e2e.py` is the server: every role runs on a scripted backend that waits
`WOMM_E2E_DELAY_S` (the config uses 0.8 s; experts wait 1×, 2× and 3× that) and builds its output from
the request, quoting real sentences of the scenario's sources. The scenario picks the outcome:

| scenario | outcome |
|---|---|
| `eval_sme_impacts` | succeeded, all quotes verified (findings reused from `docs/ui/sample_run.json`) |
| `eval_provider_compliance_costs` | succeeded, one fabricated stakeholder quote: "Quote not found", evidence-unresolved open question |
| `demo_penalties_amended` | degraded: the stakeholder expert times out |

`WOMM_E2E_FAIL_EXPERT=<id>` and `WOMM_E2E_FABRICATE_QUOTE=1` force those failures for every scenario.
Ask answers cite the first impact and finding; a question mentioning "weather" is not covered.

| spec | covers |
|---|---|
| `auth` | token prompt, wrong token (401), correct token, persistence across reload |
| `empty` | every screen before any run (run list mocked empty) |
| `runs` | the three scenarios through the picker; live statuses, badge, strip, router, board, selection |
| `replay` | Replay at 1×/2×/3×/4×/8×, Stop replay, "Watch pipeline" |
| `overview` | KPIs, latency bars, flow pills, "Needs your review" navigation, Recent runs rows |
| `detail` | counts, all five tabs, grouping, provenance, source panel, chains, disagreements, questions, event log, demo diff |
| `comparison` | demo diff provision comparison (proposal / final text): every change, side-by-side columns with struck deletions and underlined additions at 1280/1024 px, exact source texts, axe in both themes, hidden for evaluation scenarios, error + retry |
| `degraded` | the timed-out expert on Overview, Pipeline, Agents, Topology and Run detail |
| `agents-topology` | agent cards, "Open in topology", inspector facts, chips, edges, no clipped nodes (1440/1024/820 px) |
| `ask` | suggestions, typed questions, "Reading the dossier…", citation chips, uncovered answers, new run |
| `settings` | system facts, staged overrides and the derived version, self-check (also mocked with results) |
| `header-errors` | copy version, theme, Run button states; 503/429/500/502/401 via `page.route` |
| `tour` | both themes: design tokens, reference screenshots, clean console; ligatures; 1024/820 px without page scroll |
| `live` | one real run against a deployed server; skipped unless `WOMM_E2E_LIVE=1`, `WOMM_E2E_LIVE_URL` and `WOMM_E2E_LIVE_TOKEN` are set (spends model budget) |

`e2e/screenshots/` is a committed reference set of every screen in both themes. The tour attaches fresh
screenshots to the report on every run and rewrites the reference set only with
`WOMM_E2E_UPDATE_SCREENSHOTS=1` (or for missing files).

## Layout

- `src/api.ts`: typed client, token storage, 401 handling. `src/types.ts`: API shapes.
- `src/model/`: pure view-model derivations (event trace and node status, board feed, dossier
  grouping and provenance, quote highlighting, word-level proposal / final-text comparison,
  overview KPIs, scenario labels). Tested in `src/model/model.test.ts` against
  `../docs/ui/sample_run.json`.
- `src/screens/`: Overview, Run pipeline, Run detail, Agents, Topology, Ask WOMM, Settings.
- `src/components/`: design primitives (hover/focus styles, segmented controls, state cards) and
  overlays (source panel, token prompt, run picker, toast).
- `src/design.ts`: colours, themes, icons and labels from the design.

All LLM-generated text is rendered as plain text through React escaping.
