---
title: Precision guard, a higher weak bar, and data-driven repetitions in the promotion gate
date: 2026-10-14
status: revised after doc review round 1 (coherence, feasibility, adversarial) and a hostile-audience demo review; owner decisions 2026-10-14
origin: docs/brainstorms/2026-09-28-womm-phased-requirements.md (R28, R30, R34, AE4)
amends: docs/plans/2026-10-06-001-feat-self-evolution-cycle-plan.md (U7) and docs/status/2026-10-07-v1-human-steps.md (steps 6, 7); both live on feat/provision-comparison and are edited there in U7 below, merged with or after this code
---

# Precision guard, a higher weak bar, and data-driven repetitions in the promotion gate

## Overview

Changes to the promotion gate before any holdout comparison has run (the policy may change and
be re-signed until then; `evals/promotion_policy.yaml` header).

1. **Precision guard (binding).** Coverage is recall only, and candidates are evolved against the
   same judge that grades them. Listing junk raises recall without raising precision. The gate
   rejects a candidate whose precision (share of dossier impacts the judge matched to an expected
   impact) drops by more than one noise SD. Impact count and total impact text length are
   reported, not gated.
2. **A weak bar above zero.** Weak mode promoted on "mean delta > 0", which a candidate with no
   real effect passes about half the time. The bar becomes "mean delta > `weak_min_delta_se` x SE"
   (default 1.0), and the null pass rate of that bar is measured from the R34 runs (A/A).
3. **Data-driven repetitions**, for the R34 noise run and for each holdout comparison: start at 3,
   extend to 6 only when the noise says the extra runs can change the answer. Neither rule ever
   reads a candidate-minus-incumbent difference. Every attempt is recorded.
4. **Demo wording, a reject-reason table and a policy changelog.**

## Requirements trace

- **R28.** "the overall improvement on holdout exceeds repeated-run noise ... regressions in
  coverage / grounding / omissions each stay within their own tolerance ... If the data volume
  cannot support a statistical threshold, switch to a weak threshold (directional improvement +
  regressions within tolerance) and state this honestly." Precision is a fourth tolerance; the
  weak bar makes "directional improvement" exceed one SE of noise instead of zero, which is closer
  to R28's "exceeds repeated-run noise".
- **AE4.** Higher coverage with a regression elsewhere is rejected with the reason in the audit
  record: precision regressions reject as `precision_regression`.
- **R34.** "run the same golden case 3 times ... on the api backend and model that v1 evaluation
  will use". The data-driven rule starts at R34's 3 (today's code requires 6).
- **R30.** The demo shows a new expert; precision does not penalise a new expert whose impacts the
  judge matches, unlike an impact-count cap (adversarial review: a real fix raises count with
  coverage; `docs/status/2026-09-29-v0-status.md:34-41`, counts 24/16/16/9 on one case,
  "fewer impacts meant lower coverage").

## Key decisions

- **Precision.** `precision = |distinct dossier impact_ids the judge matched to any expected
  impact| / |dossier impacts|`, from the judge's existing `ExpectedVerdict.impact_id` (no extra LLM
  call). `None` when the run has no dossier or the judge failed. Added to the holdout `METRICS`, so
  the comparison bootstraps its delta and pools its noise like the others; the guard is
  `precision: {max_drop_noise_sd: 1.0}`. Known limit: the answer key is incomplete, so a correct but
  unkeyed impact lowers precision too; both arms face the same key, so the paired delta is fair.
- **Report-only diagnostics.** `impact_count` and `impact_chars` (total characters of impact text)
  per run; the comparison reports both arms' means; the gate adds a note when either rises by more
  than 25%. Bundling (fewer, longer impacts) shows in `impact_chars`.
- **Weak bar.** `SE = sqrt(sd_c^2 / (r_c n) + sd_b^2 / (r_b n))` from the comparison's own pooled
  coverage noise per arm (`r` repetitions per case, `n` paired cases). Weak mode promotes only when
  `mean_delta > weak_min_delta_se x SE`. The R34 report adds the A/A null pass rate: for every
  split of each case's runs into two halves, the share of splits whose mean difference exceeds the
  bar (incumbent against itself).
- **R34 stop rule.** After the first formal run (3 repetitions): `sd_star` is the coverage SD at
  which the MDD equals `expected_gain`, using the policy's `repetitions.min` and the holdout counts
  passed as `--holdout-cases` / `--holdout-proposals` (not the 3 val cases). With the 80%
  chi-square interval of the pooled coverage SD (exact quantiles by bisection on the regularised
  gamma, not Wilson-Hilferty): entirely above or below `sd_star`, stop; otherwise extend by 3 at
  the first run's git sha and pool. The pooled result is final.
- **R34 attempt log (closes the restart path).** `womm noise report` appends every formal attempt
  to `evals/promotion_records.yaml` (`noise_attempts`: experiment, git sha, judge version,
  repetitions, `next_step`, and the stop-rule inputs: policy sha256, `expected_gain`,
  `repetitions.min`, holdout cases and proposals). For a judge version, only the first chain
  counts: the gate refuses when a later first attempt exists, and an extension refuses when its
  stop-rule inputs differ from the first attempt's. If the first sha can no longer be checked out
  cleanly, the fallback is one fresh 6-repetition run recorded as such (no stop rule), logged too.
  A pooled run whose two halves differ in mean coverage by more than the 3-repetition SD is
  recorded with a `batch_shift` flag that the gate reports.
- **Comparison stop rule (blinded re-estimation).** Each comparison first runs `repetitions.min`
  (3) per case and arm. From the pooled within-case coverage SD over both arms (no delta computed),
  it recomputes the MDD at 3 and at `repetitions.max` (6); it extends both arms to 6 only when the
  MDD at 3 exceeds `expected_gain` and the MDD at 6 does not. Deltas are computed only after this
  decision. The decision records the repetitions used. The incumbent arm's cached runs are reused,
  so a later comparison's extension costs only candidate runs.
- **Policy.** New required fields: `guards.precision`, `weak_min_delta_se: 1.0`,
  `repetitions: {min: 3, max: 6}` (replaces the integer), `changelog` (dated entries with reasons).
  Re-signing: the change sets `signed_by`/`signed_on` to null; the owner runs `womm policy sign`,
  whose summary now lists every guard, the weak bar and the repetitions.

## Not adopted (owner, 2026-10-14) and how the demo words it

- Full human verification of every holdout item (the golden-case plan's requirement): the owner's
  spot check of 3 items per draft stays. The demo says "AI-drafted, read-checked by two people,
  owner-ruled on the differences, holdout spot-checked", never "human-verified".
- Extra calibration pairs from the candidate's output: not added. The demo says the judge was
  calibrated on the incumbent's output.

## Implementation units

- **U1. Per-run measures.** `src/womm/eval/evaluators.py`: `CaseScore.precision`,
  `impact_count`, `impact_chars`; set after the judge on scored runs; count and chars also on the
  empty-dossier path (0); all `None` on errored runs (the base is built before the infra check, so
  clear them there). Tests: matched ids counted once each; several expected impacts on one dossier
  impact count once; no dossier / judge failure / errored give `None`.
- **U2. Comparison.** `src/womm/eval/holdout.py`: `precision` in `METRICS`;
  `HoldoutComparison.diagnostics: {impact_count, impact_chars: {candidate_mean, baseline_mean,
  n_cases}}` (required, means null when nothing is paired); the blinded stop rule in `compare`
  (U5). Update the `cmp()` test helper in `tests/evolve/test_promotion.py` and
  `tests/api/test_holdout_db.py`.
- **U3. Policy and gate.** `src/womm/evolve/promotion.py`: `Metric` gains `precision`; the new
  policy fields and validation; weak bar with SE from the comparison; reasons
  `precision_regression`, `coverage_below_weak_bar`; notes for the 25% diagnostics and
  `batch_shift`; refuse when the R34 chain rule is broken. `src/womm/cli.py`: sign summary.
  `evals/promotion_policy.yaml`: fields, changelog, signature cleared. Tests: AE4 shape for
  precision; weak bar passes above and rejects at or below; diagnostics note; policy without a field
  refused; signed summary lists the new fields.
- **U4. Data-driven R34.** `src/womm/eval/run_eval.py`: `FORMAL_MIN_REPETITIONS = 3` and the
  strings that say 6 (`formal_problems` hint, `--formal` help, `cli.py` usage, docstring).
  `src/womm/eval/noise_report.py`: chi-square interval, `sd_star`, stop rule, multi-report pooling
  (same version, judge, sha, split), batch-shift check, A/A null pass rate, attempt log. `cli.py`:
  `--report` repeatable. `FormalNoiseRun.experiment` joins both experiment names. Tests: interval
  against tabulated chi-square values (1e-3); stop above, stop below, extend when straddling; pooling
  refuses mismatches; extension refuses changed inputs; a second first-attempt is refused by the
  gate; batch shift flagged; A/A rate on a synthetic report; `test_formal_problems_and_records`
  moves to a 2-repetition report.
- **U5. Data-driven comparison.** `src/womm/eval/holdout.py` `compare`: run `min`, decide on the
  pooled noise, extend to `max` when the rule says so, then summarise. Tests: stop when MDD at 3 is
  within `expected_gain`; stop when 6 cannot reach it; extend when it can; the bootstrap is not
  called before the decision (spy); repetitions used recorded; cached incumbent runs reused.
- **U6. Reject-reason table and wording.** `docs/demo/v1-self-evolution-runbook.md`: a table of
  every reason and note (`precision_regression`, `coverage_below_weak_bar`,
  `coverage_ci_not_above_zero`, `grounding_regression`, `omissions_addressed_regression`,
  `inconclusive`, `judge-sensitive`, `batch_shift`) with what it means and what it does not mean;
  the scope sentence ("drafts the impacts list of an IA from the proposal text"); "automated prompt
  and config optimization with a sealed holdout gate"; the claims never to make.
- **U7. Docs on `feat/provision-comparison`.** Human steps 6 and 7 (fields, re-sign, R34 commands
  with the possible extension, the report-only impact bullet replaced by the precision guard and
  diagnostics); self-evolution plan U7 guard list and weak bar.

## Risks

- **Precision and an incomplete key.** Correct unkeyed impacts lower precision in both arms; a new
  expert that finds many correct unkeyed impacts could trip the guard. One noise SD of tolerance
  and the paired design limit this; the reason is explicit.
- **Few degrees of freedom.** 3 val cases x 3 repetitions give 6 dof; most R34 runs will extend.
- **Weak bar raises false rejections.** A real 0.08 gain at SE about 0.03 clears a 1-SE bar most
  of the time; the A/A rate shows the other side.
- **Scope.** About 3 developer-days; the critical path is still golden cases and calibration.

## Cost

R34 $4 or $8; a comparison $22 at 3 repetitions or $44 at 6 (later comparisons about half). No
new LLM calls for precision or diagnostics.
