---
title: Cross-family judge re-score of a holdout comparison (report-only)
date: 2026-10-15
status: draft (doc review pending)
origin: docs/status/2026-10-07-v1-human-steps.md step 6 ("the coverage delta re-scored by a Claude judge ... on the same saved runs; disagreeing signs label the decision judge-sensitive. To build before the first comparison."); docs/brainstorms/2026-09-28-womm-phased-requirements.md R28 (audit record readable only by the promotion process)
---

# Cross-family judge re-score of a holdout comparison (report-only)

## Why

The system under test and the judge are both `gpt-6.1-sol`, and candidates are evolved against
that judge. Calibration only covers the incumbent's output. A second judge from another model
family, applied to the same saved runs, shows whether the gain depends on the GPT judge. It gates
nothing; it labels the decision.

## Key decisions

- **Same runs, other judge.** The comparison's runs are re-scored, not re-run: no system calls,
  only judge calls. The judge is the coverage judge role of `system_versions/v1.0-unscoped.yaml`
  (`claude_code`, same prompt file as the api judge), so prompt and rubric are identical and only
  the model family differs.
- **Runs must be stored.** Today `holdout.compare_progress` keeps the score only. A migration adds
  a nullable `run jsonb` (the RunResult's dossier and grounding, the judge input) written with each
  score. Comparisons made before the migration cannot be re-scored (refused with that reason).
  The holdout DB is local, so the dossiers stay on the owner's machine.
- **Output.** `womm evolve rescore <gate_id>`: per arm, coverage per case re-judged (the stored
  repetitions), the paired mean delta and its cluster bootstrap CI (same function and seed as the
  gate), and the sign agreement with the gate's coverage delta. Written to a new
  `holdout.rescore_audit` row keyed by gate_id; printed locally. Never written to the main
  database or Failure Memory (R28), never read by the Improvement Planner.
- **Label.** If the re-scored mean delta has the opposite sign to the gate's, or the gate
  promoted and the re-scored delta is at or below 0, the rescore marks the decision
  `judge-sensitive`. The decision itself is unchanged.
- **Cost and data path.** Judge calls on the Claude subscription (no API spend): 8 cases x up to
  6 repetitions x 2 arms, at most 96 judge calls, about 1-2 hours. Holdout dossiers and key items
  go to Anthropic through the isolated CLI; the human-steps data boundary gains that sentence.
- **Order.** Run after each formal comparison, before the demo shows it. Skipping it is visible:
  the runbook's decision display shows "cross-judge check: not run".

## Implementation units

- **U1. Store runs.** `src/womm/eval/holdout_migrations/004_compare_progress_run.sql`
  (`ALTER TABLE ... ADD COLUMN run jsonb`); `src/womm/eval/holdout.py` writes it with each score
  and exposes `stored_runs(gate_id)`. Tests: a comparison stores runs; old rows give `None`.
- **U2. Re-score.** `src/womm/evolve/rescore.py`: load the gate's comparison rows, re-judge each
  stored run with the given judge backend (`score_case` reused with the run passed in), compute
  per-case means, paired delta, bootstrap CI, sign agreement, label. Tests with a fake backend:
  agreeing signs, opposite signs label, promoted with re-scored delta <= 0 labels, missing runs
  refused, judge errors counted and reported (a case whose re-scored runs all fail is dropped from
  the pairing and listed).
- **U3. Audit and CLI.** Migration `005_rescore_audit.sql`; `womm evolve rescore <gate_id>
  [--judge-sv system_versions/v1.0-unscoped.yaml]`; `womm evolve show` prints the rescore line or
  "cross-judge check: not run". Tests: row written once per gate (a second run refuses unless
  `--replace`, which keeps the old row as superseded); show output.
- **U4. Docs.** Runbook: when to run it and what `judge-sensitive` means; human steps (on
  `feat/provision-comparison`): data boundary sentence and the command after each comparison.

## Risks

- **Claude judge is not neutral.** The dev cycle tuned the candidate against a Claude judge, so the
  re-score leans toward the candidate. Agreement is therefore weak evidence; disagreement is the
  useful signal. The runbook says so.
- **Subscription limits.** A usage limit mid-rescore stops it; the command resumes from stored
  partial results (keyed by gate, arm, case, repetition).

## Changes after feasibility review (2026-10-15)

These override the units above where they differ.

1. **What is stored.** A named subset of `RunResult`: run_id, scenario_id, status, system_version,
   code_identity, error, synthesis_error, failures, decisions, grounding, dossier. Not retrievals or
   citable_sources (large, holdout text).
2. **Gate to rows.** A new table `holdout.compare_runs(gate_id, version_id, case_key, repetition)`
   written in `run_reps` for every repetition a comparison uses (cached or fresh), plus
   `judge_version` and per-arm `code_version` on `compare_audit`. Rescore reads only
   `repetition <= audit.repetitions`.
3. **Cache.** `load_progress` treats a row with `run IS NULL` as not done and re-runs it;
   `save_progress` uses `ON CONFLICT DO UPDATE SET run = EXCLUDED.run WHERE run IS NULL`.
4. **Resume.** `holdout.rescore_progress(gate_id, rescore_judge_version, version_id, case_key,
   repetition)` checkpoints scored results; a rate limit stops the run; the `rescore_audit` row is
   written only on completion. `--replace` marks the old audit row superseded and clears progress.
5. **Boundary.** `rescore` joins the holdout-side modules and commands
   (`tests/evolve/test_planner_boundary.py`, `HOLDOUT_SIDE_EVOLVE` in `cli.py`).
6. **Aggregates only.** Output and storage carry per-arm means, delta, CI, sign agreement, errored
   and dropped counts; no case ids or per-case values (holdout module contract).
7. **Label.** `cross-judge-sensitive` (distinct from the diagnostics' `judge-sensitive`).
   `womm evolve show` joins the rescore audit.
8. Rescore applies the gate's `failure_policy`, refuses aborted comparisons, maps case keys through
   `case_key(case)` over the sealed store, and refuses rows whose case key no longer exists.
