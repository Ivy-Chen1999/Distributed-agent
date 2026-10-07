# WOMM v1: the steps that need a person — 2026-10-07

Everything else in v1 is automated. This page lists the only steps a person must take, in order,
with who, how long, the exact commands, and what "done" looks like. Commands refer to
`feat/v1-integration` (PR #6).

Total human time: about 9–11 hours, spread over three to four people.

| # | Step | Who | Time | Blocks |
|---|---|---|---|---|
| 1 | Review 11 train/val golden drafts | 2–3 classmates | 65–90 min in total | evolution on real data |
| 2 | Verify 8 holdout drafts and import them | project owner | 2–2.5 h | every promotion |
| 3 | Label about 30 pairs for the coverage-judge calibration | 2 classmates | about 30 min each | statistical and weak gates |
| 4 | Sign the promotion policy | project owner | 15 min | every gate, dev included |
| 5 | Provide an api key, run R34 and the MDD report | project owner | about 1 h plus run time | formal gates |
| 6 | Write the R37 reference answers | project owner | about 1 h | nothing (monitoring only) |
| 7 | Branch protection on `main` | repo owner | 2 min | review gate enforcement |
| 8 | 2026-11-20 route decision | project owner | 15 min | the demo |
| 9 | Cost estimation: confirm defaults, check the IA cost reference, commit the cost versions | project owner (check: anyone careful) | about 1.5 h | scored cost sweeps (R6) |

Steps 1, 3 and 7 can start today. Step 4 should come before any holdout comparison, and step 2
before any promotion.

---

## 1. Review the train/val golden drafts (classmates)

- **What:** draft PRs #7–#17, one case each. Every PR body says how many decisions it needs
  (8–14).
- **How:** follow `docs/eval/golden-review-guide.md`. For each item whose `review.decision` is
  `pending`, and for every `possibly_missing` candidate, choose verified / edited / rejected /
  unclear and set `review.reviewer` to your GitHub username. Do not touch auto-accepted items.
- **Assignment:** split the 11 PRs so nobody reviews a case they drafted. Give #11 (DGA, the
  weakest draft) to the most careful reviewer.
- **Done:** CI on the PR is green with the PR marked ready for review. The owner then publishes
  the case with `scripts/publish_golden_cases.py` and merges.

## 2. Verify and import the holdout drafts (project owner only)

The holdout must never be seen by anyone who also tunes the system, so this is not shared.

- **What:** 8 drafts in `.cache/drafts/` (local only; never commit them).
- **How, per draft:**

  ```
  uv run python scripts/verify_golden_case.py --draft .cache/drafts/<case>.yaml
  HOLDOUT_DATABASE_URL=... uv run python scripts/import_holdout_case.py .cache/holdout_import/<case>.yaml
  ```

  The first command walks every item interactively and writes a handoff. The second imports it
  into the sealed holdout database (`HOLDOUT_DATABASE_URL`) and deletes the plaintext.
- **Done:** `.cache/drafts/` is empty and the holdout store reports 8 cases from 8 proposals.

## 3. Coverage-judge calibration (classmates label, owner records)

The coverage judge decides whether a dossier covers an expected impact. Following Anthropic's
eval guidance, it is trusted only after it agrees with people on a labelled sample.

- **Prepare (owner):**

  ```
  uv run womm calibrate sample --sv <incumbent> --report <train/val eval report> --n 30 \
      --out .cache/calibration/round1/
  ```

  This writes one blind labelling sheet per annotator (`sheet_<name>.md`, with an
  `answers_<name>.yaml` to fill in). Pairs are half judge-covered and half judge-missed, and the
  order is shuffled per annotator. The judge's verdicts stay in `key.private.json`; never send
  that file to annotators.
- **Label (2 classmates, about 30 minutes each):** for each pair, decide whether the dossier
  covers the expected impact: `covered`, `not_covered` or `unsure`, with an optional note.
  Annotators work independently and do not discuss pairs until both are done.
- **Score and record (owner):**

  ```
  uv run womm calibrate score --dir .cache/calibration/round1/ --record
  git commit evals/promotion_records.yaml
  ```

  The score reports agreement per annotator and with the majority, Cohen's kappa and
  inter-annotator agreement. The recorded agreement is the lower of the raw and the
  natural-rate figure, so the stratified sample never flatters the judge.
- **Done:** a `judge_calibrations` record with agreement of at least 0.85 for the incumbent's
  judge version. If agreement is lower, fix the judge prompt and repeat with fresh pairs.

## 4. Sign the promotion policy (project owner)

- **What:** `evals/promotion_policy.yaml`. It fixes the rules before any holdout result exists.
- **Decide before signing:**
  - the primary metric (default: coverage);
  - the tolerances (default: grounding may drop at most 0.02; omissions at most one noise SD);
  - `expected_gain` (placeholder: 0.08);
  - `publish_summary`: `true` shows holdout decisions on page 4 online; `false` keeps them
    local, readable only through `womm evolve show`;
  - whether dev-mode comparisons count against the v1 budget of 6 (current: yes).
- **How:** set `signed_by` and `signed_on`, then commit. Never change the file after a
  comparison; every decision records its sha256.

## 5. Api key, R34 noise run and MDD report (project owner)

- **Key:** set a monthly spend limit in the provider console first. Then put the key in the local
  `.env` file only, never in chat or a commit.
- **Measure the cost first:** one full run to get the cost per run, then a budget estimate for
  v1.
- **R34 and MDD:**

  ```
  uv run womm eval --split val --repetitions 6 --formal
  uv run womm noise report --report <that report> --holdout-cases 8 --holdout-proposals 8 --record
  git commit evals/promotion_records.yaml
  ```

- **Done:** `formal_noise_runs` and `mdd_reports` records for the gate's judge. If the MDD is
  above `expected_gain`, the gate uses the weak threshold, and the demo says so.

## 6. R37 reference answers (project owner, optional)

- **What:** `evals/diff_regression/demo_penalties_amended.yaml`, about 5–10 items on what changed
  between the AI Act proposal and the final text for provider obligations, SME measures and
  penalties.
- **Rule:** written by one person by hand. No LLM drafting, because this file is the yardstick
  for the model.
- **Done:** every TODO replaced, `status: written`, `written_by` and `written_on` filled in.

## 7. Branch protection (repo owner)

GitHub, Settings, Branches, `main`: require a pull request and "Require review from Code Owners".
Without this, CODEOWNERS on golden files does not block merges.

## 8. The 2026-11-20 route decision (project owner)

- **New-expert route:** choose it if `womm evolve failures` shows a persistent unowned pattern
  across 2+ proposals and the topology candidate passes the gate.
- **Prompt-only fallback:** otherwise. The runbook (`docs/demo/v1-self-evolution-runbook.md`)
  has both paths.

## 9. Cost estimation (Stage B, plan 2026-10-07-001)

- **Confirm the defaults (owner, 15 min), before the first scored sweep:**
  - magnitude bands: negligible under €1k, low €1k–5k, medium €5k–25k, high €25k or more, each
    one-off or recurring;
  - the mapping of the seven effort types to administrative burden vs compliance cost;
  - whether `not_costed` (for example prohibitions) is allowed (default: yes);
  - whether R7 counts only added paragraphs (default) or also modified ones (adds 444 duties);
  - whether cost demo runs use `v1.0-cost` (default: yes).
- **Check the IA cost reference (about 1 h, anyone careful who is not tuning the system):** the
  file transcribes about 11 cost items from the AI Act impact assessment (sections 6.1.3 and
  6.2, Annexes 3 and 4). Compare each value and unit with the IA, then set `status: verified`.
  Note that the IA itself gives 10 FTE in section 6.2 and 5 FTE in Annex 3 Table 5 for the EU
  level; record which one is used.
- **Commit the cost versions (owner, 5 min):** add `v1.0-cost` and its api twin to
  `system_versions/PROMOTIONS.md`.
- **What R6 can and cannot show:** the IA prices only high-risk requirements, conformity
  assessment and governance (about 16 articles). Recall over those items and band agreement are
  scored. Rank agreement is descriptive only, and costs the IA does not price are reported, not
  scored.
