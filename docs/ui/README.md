# UI data contract (demo frontend)

Page requirements: R35 / R36 in `docs/brainstorms/2026-09-28-womm-phased-requirements.md`.
Design prompt for design tools: `docs/ui/design-prompt.md`.
Who the console is for, and which section each person sees: [personas.md](personas.md).

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
| Run detail: Costs tab | `dossier.costs` | `records[]`, `hotspots[]`, `coverage` (see api-contract.md, Costs) |
| Run detail: provision comparison | `GET /scenarios/{id}/sources` | proposal vs final text per provision key |
| Act screen (planned): whole-act comparison | `GET /corpus/{regulation_id}/comparisons`, `GET /corpus/{regulation_id}/compare/{before}/{after}` | `articles[].change`, `paragraphs[]` (before/after text, `change`), `before_obligations` / `after_obligations` (`by_actor`, `date_withheld`); join run findings on `provision_key` |
| Evolution page | `GET /evolution/lineage`, `/evolution/candidates/{id}`, `/evolution/candidates/{id}/diff` | lineage, decisions, R37 flag (api-contract.md, Evolution page) |

Status values: `queued`, `running`, `succeeded`, `degraded` (some experts failed, or synthesis
failed), `failed`, `no_changes`. Every page needs loading, empty and error states.

Note: Impact Dossier text is LLM-generated. Render it as escaped plain text, never as HTML.

## Deviations from the Claude Design source

The console follows `web/design/` except where accessibility requires otherwise
(`web/e2e/a11y.spec.ts` runs an axe-core WCAG 2.1 AA scan of every screen in both themes):

- Light-theme secondary text `--n2` is `#626E71` instead of `#687477`. The original reached only
  4.26:1 on the soft background (AA needs 4.5:1); the new value reaches 4.58–4.85:1.
- Keyboard focus shows a 2 px `--accInk` outline on every focusable element, inputs included.
- The pipeline canvas and the live impact board are focusable, labelled regions, so they can be
  scrolled from the keyboard.

## Interfaces

The console is two interfaces, switched at the top of the sidebar (`web/src/model/roles.ts`):

| Interface | For | Screens | Home |
|---|---|---|---|
| **The law** | people who study a regulation: an impact analyst, an implementer, a reader | Regulations, Act, Analysis (Run detail) | Regulations |
| **The system** (default) | people who study WOMM: the team, an evaluator | Overview, Run pipeline, Run detail, Agents, Topology, Evolution, Settings | Overview |

- The law shows no run machinery: no run status, system-version chip, header Run button, run ids
  or event log. A run reads as an *Analysis*: what was analysed and when, and, if a run is
  degraded, which expert views are missing, in words. Assessments start from the Act screen.
- The law's sidebar card shows the regulation the user chose and keeps it across screens; the
  system's shows the selected run's scenario.
- Run detail is the one screen in both interfaces. Switching interface keeps it; any other screen
  outside the new interface opens that interface's home.
- A link names its screen in the URL hash (`#/act`, `#/agents`, `#/detail?run=…`). Opening a link
  to a screen of the other interface switches to that interface; a link with no screen opens the
  current interface's home. The law's dossier export (Markdown, PDF) leaves out the run id and
  system version.
- The choice is kept in this browser (`localStorage` key `womm.role`; values saved by the earlier
  five-preset version map onto an interface). It is a view, not access control: the API still
  accepts any request with the token.
- The personas behind the split, and which UI section each sees, are in [personas.md](personas.md).

The Act screen has a **Your role in the law** picker (`womm.actor`, default `deployer`): the
implementing actors that carry duties in the loaded comparison, plus *Public body* (duties marked
public sector). Its counts and the "… duties in changed paragraphs" measure follow the choice.

Before outside users get access, the API needs a read-only mode: a separate token that offers only
the law interface, reads regulations, comparisons and dossiers, and cannot start runs. The sealed holdout and golden cases must stay unreachable.

The e2e helper `go()` switches interface when a screen is in the other one, so every screen
stays covered.

## Redesign handoff

The current console (`web/`) has these screens: Overview, Pipeline (agent graph and live board),
Detail (dossier, provision comparison, Costs tab), Agents, Topology, Evolution and Settings, plus
Regulations and Act in the law interface.
A redesign may replace everything visual and keep the data layer:

- **Keep:** `web/src/api.ts` (the typed client), `web/src/types.ts` (types matching
  `docs/ui/schema/*.json`) and `web/src/model/` (pure view models with tests). Then no backend
  change is needed.
- **Free to change:** `web/src/screens/`, `web/src/components/` and the design tokens in
  `web/src/design.ts`.
- **Work against:** `docs/ui/sample_run.json` (a real run) and the e2e fake server
  (`npm run e2e`, backed by `src/womm/api/e2e.py`).
- **Constraints:** keep the WCAG 2.1 AA scan (`web/e2e/a11y.spec.ts`) passing in both themes;
  if Manrope stays, keep `font-variant-ligatures: none` and its e2e check
  (`docs/solutions/frontend/manrope-copyright-ligature.md`); render all model text as escaped
  plain text.
- **Schemas:** `uv run python scripts/export_ui_contract.py --check` fails when the schemas lag
  the backend models; run it without `--check` to regenerate them.
