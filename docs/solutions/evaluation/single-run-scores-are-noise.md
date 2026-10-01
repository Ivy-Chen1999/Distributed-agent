---
title: Identical pipeline runs move coverage by ~0.1 — size promotion gates for it
category: evaluation
tags: [evaluation, noise, promotion, llm-judge]
signal_ref: 8ba9f1c; system_versions/PROMOTIONS.md (pooled v0.3 evidence)
date: 2026-09-29
---

## Observation
The same system version on the same golden case: coverage 0.77 over three runs, then 0.63
(0.7 / 0.5 / 0.7) over the next three (router in shadow mode, so execution was identical).
The baseline's case_02 once scored 0.86 and then 0.43 / 0.43 / 0.57. Variance tracked the
number of dossier impacts (9–41), i.e. expert output volume and synthesis merging.

## Consequence
Three repetitions resolve only differences above ~0.1 per case. A promotion decided on one batch
of three runs (v0.3's case_01 "gain") turned out to be noise once pooled.

## Practice
- Pool at least 6 runs per case before promoting; report mean ± stdev per case and metric.
- Judge-derived omission scores with 3–4 items move in steps of 0.25–0.33: treat single-item
  changes as noise.
- Keep an immutable record of every promotion and its evidence (`PROMOTIONS.md`).
