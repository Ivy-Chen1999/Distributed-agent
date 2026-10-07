# v1 self-evolution demo runbook (R30, U10)

The demo story (origin R30): Failure Memory shows a persistent gap no expert owns, the cycle
proposes a new expert as configuration, the promotion gate compares it with the incumbent on
the sealed holdout, and page 4 (Evolution) shows the lineage with the new-expert highlight.

There are three ways to run it. Only the first runs without a person, an api key or the
sealed holdout.

| Path | LLM | Holdout | Gate mode | Needs a person | Cost |
|---|---|---|---|---|---|
| A. Scripted rehearsal | scripted fake backend | synthetic, in memory | dev | no | none |
| B. Dev-mode cycle | `claude_code` (subscription) | sealed holdout | dev | yes: signed policy, sealed cases | subscription usage |
| C. Formal cycle | `api` | sealed holdout | statistical or weak | yes: everything in B plus calibration, R34, api key | API spend |

## Honesty rules (all paths)

- The demo states the gate mode. Dev and weak decisions are **directional, not statistically
  shown**; dev decisions are also **never deployable** (`dev-only, not deployable` on page 4).
- The new expert shown is the one the Improvement Planner proposed. Its raw proposal and the
  proposer's model and prompt hash are archived (`sv_archive.proposer`, `diff.rationale`,
  `diff.target_pattern`). Never present a hand-written expert as Planner-proposed, and never
  seed failures by hand to trigger the topology stage.
- Path A is a rehearsal of the code path on synthetic cases with scripted model outputs. It is
  never evidence that the system improves. Say so whenever it is shown.

## Path A: scripted rehearsal (no human input, no api key)

`womm.demo_cycle` runs the real cycle code end to end with every LLM call scripted:

1. **Failures.** `v1.0-unscoped` with every role on `fake` is replayed on two synthetic train
   cases (proposals `ai_act` and `platform_work`). Its experts never cite the provision a
   synthetic workforce impact follows from, so Failure Memory records the pattern
   `missed_impact / social_environmental / owner none` across 2 proposals.
2. **Prompt stage.** GEPA with a scripted reflector that appends an SME cost-relief
   instruction; only the Fiscal expert reacts, so val coverage rises from 1/3 to 2/3.
3. **Topology stage.** The scripted expert proposer answers the pattern with a Workforce
   expert; it covers the workforce impact on val (2/3 to 3/3) and is chosen for the gate.
4. **Gate.** `promotion.run_gate` in dev mode on a synthetic two-case holdout held in memory
   (`ScriptedHoldoutStore`). It never reads `HOLDOUT_DATABASE_URL` and refuses to start when it
   is set. The policy is the committed one, signed in memory by the harness for the synthetic
   holdout only and publishing its summary; nothing is written to `evals/`. Expected label:
   `promoted (dev-only, not deployable; weak threshold: directional)`, flag
   `insufficient_proposals` (2 proposals, fewer than the 5 a CI needs).
5. **Page 4.** The archive and the decision summary are in `DATABASE_URL`.

The run is deterministic (same version ids on every fresh database) and takes a few seconds.

```bash
# A fresh database (the demo writes the archive and a decision summary into it).
export DATABASE_URL=postgresql://womm:womm@localhost:55432/womm_demo_rehearsal
uv run python -m womm.api.e2e create-db
unset HOLDOUT_DATABASE_URL
uv run python -m womm.demo_cycle scripted

# The console on the same database: Evolution shows the seed, every archived prompt-stage
# child (7; the chosen one is the topology child's parent) and the topology child
# (badges "Topology", "Promoted", "dev-only"; the "New expert: workforce" panel).
npm --prefix web run build
export WOMM_API_TOKEN=demo-rehearsal-token-0123456789
uv run uvicorn --factory womm.api.e2e:create_e2e_app --port 8765
# open http://localhost:8765, paste the token, go to Evolution

uv run python -m womm.api.e2e drop-db   # when done
```

Tests: `tests/evolve/test_demo_cycle.py` (the cycle, the gate, the page-4 API responses, the
refusal with a holdout URL set).

## Path B: dev-mode cycle on `claude_code` (a person runs it)

`uv run python -m womm.demo_cycle dev-plan` prints the sequence with the committed seed's ids
(`v1.0-unscoped` is `sv_73c6a3fdd013`). It uses subscription usage: run it deliberately, with
the Claude CLI logged in and its isolation self-check passing.

```bash
uv run womm evolve seed
uv run womm evolve replay sv_73c6a3fdd013 --split train --repetitions 2
uv run womm evolve failures --sv sv_73c6a3fdd013 --from-db   # is there owner = none in 2+ proposals?
uv run womm evolve cycle --base sv_73c6a3fdd013 --stage both --cycle-id cycle_demo_dev
# <chosen> = the cycle's "candidate for the gate"
uv run womm evolve diffcheck <chosen>              # R37, optional (see below)
uv run womm evolve diffcheck sv_73c6a3fdd013
# Holdout side, in a separate shell with HOLDOUT_DATABASE_URL set:
uv run womm evolve promote <chosen> --incumbent sv_73c6a3fdd013
uv run womm evolve show <chosen>
```

What each step needs:

| Step | Needs |
|---|---|
| `seed`, `replay`, `failures`, `cycle` | `DATABASE_URL`; the Claude CLI (`claude_code`); golden train/val cases. No holdout URL (these commands refuse one). |
| `diffcheck` (R37) | The hand-written reference answers in `evals/diff_regression/demo_penalties_amended.yaml` (status `written`, `written_by`). Until then it reports `not_available`. Never gates. |
| `promote` (dev mode) | **A signed and committed `evals/promotion_policy.yaml`** (user decision 2026-10-06: every gate mode, dev included, reads the sealed holdout and spends its budget). The sealed holdout imported into `HOLDOUT_DATABASE_URL` (golden plan U8). Dev mode skips the coverage-judge calibration and the R34 run. |
| Page 4 shows the holdout decision | `publish_summary: true` in the signed policy (decision 3). With `false`, the decision stays in the holdout audit and only `womm evolve show` reads it. |

## Path C: formal cycle on the `api` backend (when a key exists)

Same as B, but the gate compares api twins:

```bash
uv run womm evolve twin <chosen>        # archives the api twin in the candidate's cycle
uv run womm evolve promote <chosen-api-twin> --incumbent sv_761d872bb18e   # v1.0-unscoped-api
```

Before the first formal comparison, a person must also:

1. Sign and commit the promotion policy (as in B), confirming primary metric, tolerances,
   budget and `expected_gain`.
2. Record a coverage-judge calibration of at least 85% for the gate's judge in
   `evals/promotion_records.yaml` (`judge_calibrations`), and commit it.
3. Run the R34 formal noise run (`uv run womm eval --split val --repetitions 6 --formal`, api
   key required) and record it under `formal_noise_runs`.
4. Optionally record a minimum-detectable-delta report (`mdd_reports`). Without one, or when it
   exceeds `expected_gain`, the gate runs in weak mode (directional).

The gate refuses, before any holdout run, whatever is missing.

## The 2026-11-20 fallback decision (R30)

By **2026-11-20** decide whether the demo uses the new-expert route or falls back to a
prompt-level cycle. Fall back when either holds on the real (path B or C) run:

- `womm evolve failures` shows no persistent `owner = none` pattern spanning 2+ proposals, so
  the topology stage reports "no persistent ... pattern" and proposes nothing; or
- the topology candidate does not beat its parent on val (the cycle keeps the prompt-stage
  choice).

The fallback runs `womm evolve cycle --base <seed> --stage prompt`, gates the prompt-stage
candidate the same way, and the demo says that the new-expert route was attempted and why it
was not shown. Path A still demonstrates the new-expert mechanics, labelled as a scripted
rehearsal.

## After a decision

- `promoted` in dev mode: nothing is deployed. Present it as directional.
- `promoted` in a formal mode: a person records it in `system_versions/PROMOTIONS.md` and
  commits the new default version (plan, human-in-the-loop point 4).
- `rejected (inconclusive)`: the comparison aborted and consumed no budget; a repeated abort on
  the same pair is flagged for a person.
- A summary that fails to reach the main database after the decision is recorded: `promote`
  prints the decision and exits non-zero; the audit row is authoritative.
- A run that stopped mid-comparison keeps its budget reservation; rerun the same pair with
  `womm evolve promote ... --resume`.

## Analyst feedback (R31, U11; optional)

Analysts mark findings of **train/val** eval runs in a LangSmith annotation queue; holdout runs
are never traced, so they can never be marked. The flow is `scripts/import_feedback.py`
(details in its docstring and in `womm.evolve.feedback`):

```bash
# After `womm eval --split train` (or val) with LangSmith tracing and DATABASE_URL set:
uv run python scripts/import_feedback.py queue --sv <version_id>    # fill the review queue
# Analysts pick a womm_analyst mark per finding: accept / reject / edit / missing_impact /
# weak_evidence, with "finding: <id>", "edited: ...", "impact: ..." comment lines.
uv run python scripts/import_feedback.py import --sv <version_id>   # audit rows + Failure Memory
uv run python scripts/import_feedback.py stage                      # candidates into open drafts
uv run womm evolve failures --sv <version_id> --from-db             # source column shows "human"
```

- Every mark is stored in `analyst_feedback`, keyed by its LangSmith feedback id.
- `missing_impact` and `weak_evidence` also become Failure Memory events of the kinds
  `analyst_missing_impact` and `analyst_weak_evidence` (source `human`). The Improvement Planner
  sees them in its patterns. They never satisfy the new-expert trigger, which takes the judge's
  `missed_impact` kind only, so hand-entered feedback cannot force a topology proposal (honesty
  rules above).
- `missing_impact` is never added to a golden case directly. `stage` appends it to the case's
  open draft in `evals/golden/drafts/` as a pending `human_added` candidate, and the review gate
  blocks publication until a reviewer verifies, edits (adding the IA anchor) or rejects it. A
  case without an open draft keeps the candidate queued.
- `accept`, `reject` and `edit` are recorded only; v1 has no consumer for them.
