---
title: Jev relevance scores depend on spelling out what an expert does
category: llm-backends
tags: [jev, typesafe, router, calibration]
signal_ref: 8ba9f1c; src/womm/decisions/jev.py DOMAIN_GLOSS / relevance_question
date: 2026-09-29
---

## Problem
Asked "Is the fiscal specialist (fiscal) needed…?", Jev scored fiscal 0.19–0.49 on runs where the
fiscal expert contributed covered impacts 5 times out of 6. In active mode it would have been
skipped.

## Cause
"fiscal" was read as public finance; our Fiscal expert covers compliance costs, administrative
burden, fees and fines.

## Fix
Put a one-line gloss of each domain in the question. Probed on the same states: fiscal
0.29–0.32 → 0.89–0.95 on real scenarios, 0.45 on a governance-only control, 0.89 on a fees-only
control. Adding "answer yes if plausibly relevant" inflated every score and hurt separation, so
it was not used.

## Applies when
Any bounded-decision model asked about roles by name. Keep the router in shadow mode until the
decisions are labelled and calibrated (and include negative cases — ours had none).
