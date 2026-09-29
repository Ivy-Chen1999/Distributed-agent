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
from the repo root, for example `uvicorn --factory womm.api.app:create_app --port 8000`.

On first load the console asks for the bearer token (`WOMM_API_TOKEN`) and keeps it in
`localStorage` under `womm.token`. A 401 clears it and asks again.

## Build, test, typecheck

```sh
npm run build          # tsc --noEmit, then vite build into dist/
npm test               # vitest: view-models, API client, per-screen render smoke tests
npx tsc --noEmit
npm run preview        # serve dist/ with the same proxy
```

The API serves the built `dist/` at `/` in production, so all API calls use relative paths.

## Layout

- `src/api.ts`: typed client, token storage, 401 handling. `src/types.ts`: API shapes.
- `src/model/`: pure view-model derivations (event trace and node status, board feed, dossier
  grouping and provenance, quote highlighting, overview KPIs, scenario labels). Tested in
  `src/model/model.test.ts` against `../docs/ui/sample_run.json`.
- `src/screens/`: Overview, Run pipeline, Run detail, Agents, Topology, Ask WOMM, Settings.
- `src/components/`: design primitives (hover/focus styles, segmented controls, state cards) and
  overlays (source panel, token prompt, run picker, toast).
- `src/design.ts`: colours, themes, icons and labels from the design.

All LLM-generated text is rendered as plain text through React escaping.
