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
- Name the work plainly. The system **drafts the impacts list of an impact assessment from the
  proposal text**; it does not draft impact assessments or compare policy options. The cycle is
  **automated prompt and config optimization with a sealed holdout gate**; show the literal diff
  (`womm evolve show <candidate>`).
- Say how the answer keys were made: **AI-drafted, read-checked by two people, owner-ruled on
  the differences, holdout spot-checked** (3 items per draft). The judge was calibrated on the
  incumbent's output, and the judge and the system under test are the same model
  (`gpt-6.1-sol`).
- A weak-mode pass is **"not rejected (directional)"**. Quote the A/A null pass rate that
  `womm noise report` prints, with its label: **lower bound: within-run noise only** (it splits
  one version's runs, so it misses how a gain varies between cases).

### Claims the demo never makes

- "The system improved" or "statistically shown", for any weak-mode or dev-mode decision.
- "Human-verified" or "expert-validated" answer keys.
- "Drafts impact assessments" or "approaches official IAs".

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

**Rehearsed 2026-10-12** (local database, `claude_code`, before the 11 practice cases were
published): seed, replay of the 2 AI Act train cases x 2 (4 of 4 runs, 0 errors, about 50
minutes), failures and `diffcheck` (R37 coverage 0.917 over 3 runs) all worked. `cycle` stopped
at its first precondition: "the prompt stage needs at least one train and one val case". So:

- The cycle needs the published practice cases (3 of the 11 are val).
- The two hand-written AI Act cases had no item categories, so their failure patterns were
  "uncategorised". They have categories since 2026-10-13 (the drafting category guide, assigned
  by the assistant), so the cycle can group their misses.
- What the rehearsal's AI Act misses are (coverage 0.60 and 0.71; 8 items missed in both runs):
  four lean on the IA's own economics (the business-as-usual factor, the EUR 100-500 million
  aggregate, the "theoretical maxima" caveat, the sandbox capital study). The drafting
  derivability judge, run on 2026-10-13 on the hand-written items, found the mechanism of three
  of them in the provisions (only the figures are the IA's), so they stay; c02_e05 (sandboxes
  easing access to investors) has no basis in the provisions and was removed. The other four
  misses are real gaps the cycle should learn: a cost one actor avoids because another must
  build something (in-built logs), linking two duties (QMS audited by the notified body), the
  net effect of several support measures, and an explicit limit (sandboxes grant no exemptions).
  The judge also moved c02_e03 to compliance_cost; every other item passed both judges.
- The topology stage needs a pattern with owner none in 2+ proposals: impossible with the AI Act
  alone, possible with the 12 laws of the published cases.
- Budget several hours on `claude_code` for a cycle: 4 full runs took about 50 minutes.

What each step needs:

| Step | Needs |
|---|---|
| `seed`, `replay`, `failures`, `cycle` | `DATABASE_URL`; the Claude CLI (`claude_code`); golden train/val cases. No holdout URL (these commands refuse one). |
| `diffcheck` (R37) | The reference answers in `evals/diff_regression/demo_penalties_amended.yaml`: LLM-drafted on 2026-10-12 (status `llm_drafted`; every quote is machine-checked against the article texts). Run `womm evolve diffcheck <version>` for the candidate and the incumbent so the evolution page can compare them. Never gates. |
| `promote` (dev mode) | **A signed and committed `evals/promotion_policy.yaml`** (user decision 2026-10-06: every gate mode, dev included, reads the sealed holdout and spends its budget). The sealed holdout imported into `HOLDOUT_DATABASE_URL` (golden plan U8). Dev mode skips the coverage-judge calibration and the R34 run. |
| Page 4 shows the holdout decision | `publish_summary: true` in the signed policy (decision 3). With `false`, the decision stays in the holdout audit and only `womm evolve show` reads it. |

## Path C: formal cycle on the `api` backend (when a key exists)

Same as B, but the gate compares api twins:

```bash
uv run womm evolve twin <chosen>        # archives the api twin in the candidate's cycle
uv run womm evolve promote <chosen-api-twin> --incumbent sv_43cee2e14602   # v1.0-unscoped-api
```

Before the first formal comparison, a person must also:

1. Sign and commit the promotion policy (as in B), confirming primary metric, tolerances,
   budget and `expected_gain`. `womm policy sign` refuses unless the policy's last `changelog`
   entry is dated the day of signing: add one saying what is signed and why.
2. Calibrate the coverage judge (golden-case plan, Revision 2026-10-04). The tools draw and
   score the pairs; people only label, then commit the record:

   ```bash
   # a. Train/val eval reports scored by the gate's judge (the incumbent's), with run files.
   uv run womm eval --split train --system-version system_versions/v1.0-unscoped-api.yaml
   uv run womm eval --split val   --system-version system_versions/v1.0-unscoped-api.yaml
   # b. Draw ~30 blind pairs, half judge-covered and half judge-missed, one sheet per annotator.
   uv run womm calibrate sample --sv sv_43cee2e14602 \
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
3. Run the R34 formal noise run (3 repetitions, extended by 3 when the stop rule says so) and
   record it with its minimum-detectable-delta reports:

   ```bash
   uv run womm eval --split val --repetitions 3 --formal \
       --system-version system_versions/v1.0-unscoped-api.yaml     # api key, clean tree
   # Holdout counts from the sealed store's import log (a person passes them in). --record
   # applies the stop rule and records the attempt whatever it says.
   uv run womm noise report --report runs/eval_<first>.json \
       --holdout-cases 8 --holdout-proposals 8 --record
   git add evals/promotion_records.yaml && git commit -m "chore: record R34 attempt"
   # Only when it printed "next step: extend": 3 more on the same code (commits since the first
   # run may touch only evals/promotion_records.yaml), then pool both.
   uv run womm eval --split val --repetitions 3 --formal \
       --system-version system_versions/v1.0-unscoped-api.yaml
   git add evals/promotion_records.yaml && git commit -m "chore: log formal run"
   uv run womm noise report --report runs/eval_<first>.json --report runs/eval_<second>.json \
       --holdout-cases 8 --holdout-proposals 8 --record
   # If the code has changed since the first run: one fresh run of 6 (a fallback) instead, same
   # version, split and stop-rule inputs.
   git add evals/promotion_records.yaml && git commit -m "chore: record R34 noise run and MDD"
   ```

   Every finished formal run appends itself to `formal_runs` in the records file: commit that
   file after each formal run, because the next formal run refuses a dirty tree (`womm eval
   --formal` checks the whole tree, the records file included). A logged run that no attempt
   reads blocks `--record` and the gate. Without `--record` the stop rule is not shown at all.
   `--record` refuses `--repetitions` other than the policy's `repetitions.min`.

   The stop rule (pre-registered): the 80% chi-square interval of the pooled coverage SD lies
   entirely above or below the SD at which the MDD equals `expected_gain` (for the policy's
   `repetitions.min` and the holdout counts): stop; otherwise extend once. It reads the
   incumbent's noise only. Every attempt goes to `noise_attempts`; a second first run for the
   same judge, a missing extension, or changed stop-rule inputs are refused, and the gate refuses
   a broken chain. A pooled run whose halves differ by more than the first half's SD is recorded
   with `batch_shift`, which the gate notes.

   `--record` refuses a report that is not a formal api run (`claude_code` or dev noise runs
   print but never record). The MDD uses the signed policy's `repetitions`, two-sided alpha
   0.05 and power 0.8, Student-t quantiles with (proposals - 1) degrees of freedom, and a
   cluster-by-proposal design effect `1 + (cases/proposals - 1) * icc` (`--icc`, default 1.0,
   conservative); the formula is in `src/womm/eval/noise_report.py`. It covers run-to-run noise
   only, so it is a lower bound. An MDD record for other repetitions than the policy's
   `repetitions.min`, or for other holdout counts than the sealed store's, refuses the gate.
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

1. Done 2026-10-12: the plan's Decision-context defaults (effort-type mapping, EUR band edges,
   `not_costed`, R7 = `added` units only) are confirmed as recorded. Changing them later moves
   the goalposts.
2. Done 2026-10-12 (scripts/check_ia_cost_reference.py, 11 of 11): one person checks `evals/cost_reference/ai_act_swd2021_84.yaml` against SWD(2021) 84 Part 1
   §6.1.3 and §6.2 and Part 2 Annex 3 (Table 5) and Annex 4 (about 1 hour), then sets
   `status: verified`, `verified_by` and `verified_on` and commits it. `womm cost check` refuses
   the file until then. Every report prints the commit sha of the reference.
3. Done 2026-10-12: the user lists `v1.0-cost.yaml` and `v1.0-cost-api.yaml` in `system_versions/PROMOTIONS.md`
   as cost-analysis candidates (not promotions).

Runs (dev on `claude_code`, labelled dev-only; formal on `v1.0-cost-api` when an api key
exists). A proposal sweep is 9 calls per repetition and a final-act sweep 17, so three
repetitions of both are about 80 calls, a few hours on `claude_code` at parallelism 3. Start
them in the background and poll; rerunning the same command resumes (finished batches are
skipped, failed ones are retried). `--max-usd` is a hard cap on the sweep directory's spend,
earlier runs included (read from its batch files): a call starts only if it, plus one reserved
call per call in flight, fits under the cap, so a resumed sweep never spends the cap again. A
backend that reports no USD cost cannot be capped; the sweep then stops after one call and says
so.

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

What each reason and note means (pre-registered 2026-10-14, before any comparison):

| Reason or note | Means | Does not mean |
|---|---|---|
| `coverage_ci_not_above_zero` | Statistical mode: the 95% CI of the coverage gain reaches 0 | The candidate is worse |
| `coverage_below_weak_bar` | Weak or dev mode: the coverage gain is not above 1 SE of the mean delta (the comparison's bootstrap SE; the within-run noise SE, noted, when too few proposals) | The candidate is worse; with 8 cases a real gain can miss the bar |
| `coverage_noise_unavailable` | Weak or dev mode: no run-to-run noise to set the bar (too few repetitions scored) | Anything about the candidate |
| `grounding_regression` | Mean grounding dropped by more than 0.02 | Citations are wrong (grounding checks that a quote exists) |
| `omissions_addressed_regression` | RSB omissions addressed dropped by more than one noise SD | The candidate covers less overall |
| `precision_regression` | The share of dossier impacts matched to an expected impact dropped by more than one noise SD | The extra impacts are wrong (the key is incomplete) |
| `impact_count_drop` | The candidate's mean impact count per dossier fell more than 25% below the incumbent's (bundling findings into a few broad impacts) | The candidate covers less; a rise is never a rejection |
| `impact_count_unavailable` | No paired case has impact counts, so the drop guard cannot be checked (never skipped) | Anything about the candidate's quality |
| `inconclusive` | The comparison aborted (rate limit or a missing score); no budget used | A rejection on the merits |
| note `judge-sensitive: impact_count/impact_chars rose ...` | The candidate writes over 25% more; possible judge gaming; report-only | A rejection |
| note `batch shift` | The R34 halves differ; the noise SD may mix day-to-day variation | The gate is invalid |
| note `weak bar: ...` | The bar the weak decision used | Statistical evidence |

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
  `analyst_missing_impact` and `analyst_weak_evidence` (source `human`). They are for people:
  `womm evolve failures` lists them (source column `human`). The Improvement Planner does not
  see them: its reflective records take the judge's events and patterns only. They never
  satisfy the new-expert trigger either, which takes the judge's `missed_impact` kind only, so
  hand-entered feedback cannot force a topology proposal (honesty rules above).
- `missing_impact` is never added to a golden case directly, and only a mark on a **train** run
  becomes a golden candidate (val is for selection; an analyst must not change what it scores).
  `stage` appends it to the case's open train draft in `evals/golden/drafts/` as a pending
  `human_added` candidate that records the analyst (`provenance.raised_by`), its
  `feedback_id` and an `analyst_digest` over both, so CI fails if either is edited; it refuses val and
  holdout drafts. It edits the draft in place (the counts in `stats` and the appended
  candidates only, so reviewers' comments survive) and writes nothing if the file changed while
  it was staging (rerun `stage`). The review gate blocks publication until a reviewer decides it: it is kept
  only as `edited`, by a reviewer other than that analyst, with `ia_section`, `ia_anchor`,
  `affected_actor`, `mechanism` and a valid `category` filled in. `publish_golden_cases.py`
  checks the anchor against the cached IA when `.cache/ia/<fixture>/` is on the machine and
  otherwise prints a note for the reviewer to confirm it by hand. Run it with `DATABASE_URL`
  set: it then checks each kept analyst item against its `analyst_feedback` row (exists, not
  retracted, train `missing_impact` of this case, made by `raised_by`) and refuses a mismatch;
  without a database it prints a note instead. A case without an open train draft keeps the
  candidate queued.
- A published analyst candidate is an expected impact with `origin: human`. Its misses are
  scored like any other, but their `missed_impact` events carry `golden_origin: human` and the
  new-expert trigger leaves them out, so analysts cannot force a topology change indirectly
  through golden cases either.
- A mark edited in LangSmith after its import replaces the stored one on the next `import` (the
  old version stays in the row's `history`; its Failure Memory event is replaced). A mark
  deleted in LangSmith is reported; `import ... --apply-retractions` retracts it (the row stays
  with `retracted_at`, its event is removed, a queued candidate is dropped). If the candidate was
  already staged into a draft, the script warns and exits 2: update or reject the draft item by
  hand.
- `accept`, `reject` and `edit` are recorded only; v1 has no consumer for them.
