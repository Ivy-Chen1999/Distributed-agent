---
title: Target-category note on the holdout decision (report-only)
date: 2026-10-15
status: active (feasibility review 2026-10-15 applied)
origin: demo audit 2026-10-15, finding 5 ("nothing shows whether the new expert's own category moved on the holdout"); docs/brainstorms/2026-09-28-womm-phased-requirements.md R30 (demo: "existing experts repeatedly miss a certain kind of impact -> a new expert is proposed -> holdout improves"), R28 (holdout aggregates only), R36 page 4
---

# Target-category note on the holdout decision (report-only)

## Why

R30's demo story is about one kind of impact: experts keep missing it, so a new expert is
proposed. The gate judges whole-dossier coverage over 8 holdout proposals, so a new expert that
helps on the 1-2 proposals where its category appears will most likely fall under the weak bar.
The first question from an impact-assessment expert will be "did it find the impacts it was
created for?" Today nothing answers it. A report-only note answers it from data the comparison
already stores, without changing the decision rule (signed 2026-10-15).

## Key decisions

- **What is counted.** The target category is the `category` of the topology diff's
  `target_pattern` (`kind/category/owner`, `src/womm/evolve/proposers.py` `pattern_key`). For
  each arm: the holdout expected impacts whose `category` equals it, and per item the share of
  that arm's repetitions in which the judge marked it covered. The note gives the item count,
  the number of proposals they come from, and the summed coverage per arm ("covered 1.7 vs 3.0
  of 6 items, mean over repetitions"). Aggregates only (R28): no case id, item id, or per-case
  value is stored or printed.
- **Counting rule.** A quality failure (scored, no judge verdict, coverage 0) counts every item
  as not covered, as the coverage metric does. Errored runs, runs with a judge error, and items
  without a verdict in a "judge skipped ids" run are left out of that item's denominator. An
  item with no usable repetition on one arm is left out on both arms. Items are joined on the
  case key and `expected_id`, both already inside `compare`.
- **Small counts print no numbers.** With fewer than 3 items, or items from a single proposal,
  the note says only that the category is too thin in the holdout to read, with no coverage
  figures: one proposal's numbers would be per-case values. The counts may stay in the
  holdout-side `compare_audit`.
- **Uncategorised and aborted.** A target of `uncategorised` (failure memory's fallback) gets no
  note. An aborted comparison has `target_category = None` and no note.
- **Report-only.** A note on the decision (like the `judge-sensitive` notes), never a reason, so
  the signed rule is unchanged and the policy needs no re-signing. With fewer than 3 items the
  note says the count is too small to read.
- **When.** Only when the candidate (or the version its api twin was made from) has a topology
  diff with a `target_pattern`. A prompt-only candidate gets no note.
- **Where the data comes from.** `holdout.compare` already has, per arm and repetition, the
  stored `CaseScore.judge.expected` verdicts and the sealed cases' `expected_impacts` with
  `category`. The category string is passed into `compare` by `promotion.run_gate`, which reads
  it from the archive (main database) in `womm evolve promote`, the same way `r37_record` is
  read. The holdout side never reads the archive.
- **Shown on page 4** through the decision's notes, which the page already lists.

## Implementation units

- **U1. Compute.** `holdout.compare(..., target_category: str | None = None)` adds
  `HoldoutComparison.target_category: TargetCategoryCount | None` (`category`, `n_items`,
  `n_proposals`, `candidate_covered`, `baseline_covered`), from the cases paired on both sides.
  Tests: counts with a fake store; no category gives None; a category absent from the holdout
  gives `n_items = 0`; nothing per case in the model dump.
- **U2. Wire and note.** `run_gate(..., target_category=None)` passes it on; `decide` adds the
  note; `womm evolve promote` resolves the category from the candidate's archived diff, or from
  the diff of the version its api twin was made from. Tests: the note text, the small-count
  wording, a prompt-only candidate has no note, the decision and reasons are unchanged.
- **U3. Docs.** Runbook reason table gains the note; the talk tracks (outcomes 2 and 3) use it.

## Risks

- **Small counts.** The holdout key has few items per category; the note says so and is never
  quoted as evidence of a gain. It answers "where did it help", not "did it help".
- **Key categories are AI-drafted.** The same caveat as the answer keys' wording applies.
- **The holdout freeze.** This must land before the first holdout comparison (the branch is
  frozen from then on); landing it changes the code version, which is harmless before any
  comparison.
- **A finer signal for the person.** The Planner never reads it, but the owner sees a
  per-category holdout figure. With one comparison in the demo there is no next cycle to steer
  with it.
