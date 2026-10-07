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
2. Calibrate the coverage judge (golden-case plan, Revision 2026-10-04). The tools draw and
   score the pairs; people only label, then commit the record:

   ```bash
   # a. Train/val eval reports scored by the gate's judge (the incumbent's), with run files.
   uv run womm eval --split train --system-version system_versions/v1.0-unscoped-api.yaml
   uv run womm eval --split val   --system-version system_versions/v1.0-unscoped-api.yaml
   # b. Draw ~30 blind pairs, half judge-covered and half judge-missed, one sheet per annotator.
   uv run womm calibrate sample --sv sv_761d872bb18e \
       --report runs/eval_<train>.json --report runs/eval_<val>.json \
       --n 30 --seed 20261007 --annotator alice --annotator bob \
       --out .cache/calibration/2026-10-jv/
   # c. Send each annotator sheet_<name>.md and answers_<name>.yaml (never key.private.json).
   #    They fill in label (covered | not_covered | unsure) and an optional note per pair id,
   #    and return the answers file into the same directory.
   # d. Score: per-annotator and majority agreement, Cohen's kappa, inter-annotator agreement.
   uv run womm calibrate score --dir .cache/calibration/2026-10-jv/
   # e. Append the aggregate judge_calibrations record, then commit it.
   uv run womm calibrate score --dir .cache/calibration/2026-10-jv/ --record
   git add evals/promotion_records.yaml && git commit -m "chore: record coverage-judge calibration"
   ```

   The sample is stratified by the judge's verdict so misses are well represented; agreement is
   reported raw and reweighted to the judge's natural covered rate, and the recorded
   `agreement` is the lower of the two. `unsure` labels and ties are excluded and counted.
   Holdout reports and non-public cases are refused. The gate needs at least 85%; below that,
   fix the judge rubric and recalibrate (also after any change of the judge prompt or model,
   which changes `judge_version`).
3. Run the R34 formal noise run and record it with its minimum-detectable-delta reports:

   ```bash
   uv run womm eval --split val --repetitions 6 --formal \
       --system-version system_versions/v1.0-unscoped-api.yaml     # api key, clean tree
   # Holdout counts from the sealed store's import log (a person passes them in).
   uv run womm noise report --report runs/eval_<formal>.json \
       --holdout-cases 8 --holdout-proposals 8
   uv run womm noise report --report runs/eval_<formal>.json \
       --holdout-cases 8 --holdout-proposals 8 --record
   git add evals/promotion_records.yaml && git commit -m "chore: record R34 noise run and MDD"
   ```

   `--record` refuses a report that is not a formal api run (`claude_code` or dev noise runs
   print but never record). The MDD uses the signed policy's `repetitions`, two-sided alpha
   0.05 and power 0.8, Student-t quantiles with (proposals - 1) degrees of freedom, and a
   cluster-by-proposal design effect `1 + (cases/proposals - 1) * icc` (`--icc`, default 1.0,
   conservative); the formula is in `src/womm/eval/noise_report.py`. It covers run-to-run noise
   only, so it is a lower bound. An MDD record for other repetitions than the policy's is
   ignored by the gate.
4. Without an MDD report, or when its coverage MDD exceeds `expected_gain`, the gate runs in
   weak mode (directional).

The gate refuses, before any holdout run, whatever is missing.

## Cost analysis (EU cost plan R4-R7, U9; separate from the cycle)

Cost records (who pays, effort type, ordinal one-off and recurring bands, dates), the IA cost
check (R6) and the report of costs the ex-ante IA could not see (R7). This is not part of the
self-evolution story: the evolution base stays `v1.0-unscoped`, the cost prompt is not
evolvable, and cost scores are never GEPA scores, `sv_metrics` rows, Failure Memory rows or
promotion metrics. The holdout and the promotion policy are untouched.

Before the first scored sweep (once):

1. The user confirms the plan's Decision-context defaults (effort-type mapping, EUR band edges,
   `not_costed`, R7 = `added` units only). Changing them later moves the goalposts.
2. One person checks `evals/cost_reference/ai_act_swd2021_84.yaml` against SWD(2021) 84 Part 1
   §6.1.3 and §6.2 and Part 2 Annex 3 (Table 5) and Annex 4 (about 1 hour), then sets
   `status: verified`, `verified_by` and `verified_on` and commits it. `womm cost check` refuses
   the file until then. Every report prints the commit sha of the reference.
3. The user lists `v1.0-cost.yaml` and `v1.0-cost-api.yaml` in `system_versions/PROMOTIONS.md`
   as cost-analysis candidates (not promotions).

Runs (dev on `claude_code`, labelled dev-only; formal on `v1.0-cost-api` when an api key
exists). A proposal sweep is 9 calls per repetition and a final-act sweep 17, so three
repetitions of both are about 80 calls, a few hours on `claude_code` at parallelism 3. Start
them in the background and poll; rerunning the same command resumes (finished batches are
skipped, failed ones are retried). `--max-usd` caps api spend.

```bash
# Whole-version sweeps (R6 needs the proposal, R7 the adopted act).
nohup uv run womm cost sweep --sv v1.0-cost --version com2021_206 --repetitions 3 \
  > runs/cost_sweep_proposal.log 2>&1 &
nohup uv run womm cost sweep --sv v1.0-cost --version reg2024_1689 --repetitions 3 \
  > runs/cost_sweep_final.log 2>&1 &
# Each prints its directory: runs/cost_sweeps/<sv id>/<version>/git-<sha>[-dirty-<hash>]/

# R6: the five metrics with min-max over repetitions, payer bases, IA-silent costly provisions.
uv run womm cost check --sweep runs/cost_sweeps/<sv id>/com2021_206/<code>
# R7: records on obligations added after the proposal (first line: not scored).
uv run womm cost late-added --sweep runs/cost_sweeps/<sv id>/reg2024_1689/<code>

# Cheap in-run check next to case 01: score only the IA items whose keys the scenario has.
# --runs takes the saved run JSON files `womm run` writes; every run must carry cost records of
# the proposal (com2021_206). A run on the adopted or consolidated text is refused.
uv run womm run eval_provider_compliance_costs --system-version system_versions/v1.0-cost.yaml
uv run womm cost check --runs runs/<run id>.json [runs/<run id 2>.json ...]
```

`womm cost check` writes JSON and Markdown under `runs/cost_check/`. The console shows cost
sections of runs made with a cost-enabled version (`WOMM_SYSTEM_VERSION=
system_versions/v1.0-cost.yaml` for the API): the Costs tab on Run detail and the cost column
on the pipeline. `final_vs_proposal` (explore, proposal to adopted act) and
`demo_penalties_amended` show records added after the proposal marked; R6 and R7 reports stay
CLI-only in v1.

Honesty rules for cost results:

- Record the results in the v1 progress report, labelled dev-only or formal, with the reference
  sha and the IA figure audit's limits next to the numbers: the IA quantifies about 11 cost
  items covering about a quarter of the proposal's duties; IA silence is not "no cost";
  `rank_tau_b` is descriptive over at most five items (those with a prediction; the report
  gives n) and never a headline.
- `payer_recurrence_agreement` is the strict form: a recurring IA item agrees only when a record
  carries the IA's payer, a recurring band and the expected primary effort type
  (`effort_types` in the reference, for example `human_oversight` for the oversight item).
- Recall depends on the payer fallback (rule table and inferred payers); quote the payer-basis
  breakdown with it.
- Nothing sums euros: bands are ordinal classes per entity and item.

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
