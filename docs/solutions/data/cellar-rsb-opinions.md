---
title: RSB opinions are not where the fetcher looked, and the fallback hid it
category: data
tags: [cellar, rsb, impact-assessment, golden-cases, silent-fallback]
signal_ref: c15f474; plan docs/plans/2026-10-12-001-feat-expert-sources-and-onboarding-plan.md
date: 2026-10-12
---

## Observation
The golden cases were meant to take their must-mention points from the Regulatory Scrutiny
Board's opinion on each impact assessment. No opinion was ever downloaded: the fetcher asked
Cellar for the `comnat` resource of the opinion's SEC reference, which does not hold RSB
opinions, got 404 for every law, recorded "opinion none found in Cellar" and used the impact
assessment's own procedural annex instead. The status was written into each cache manifest, but
nothing failed and no summary showed it, so it went unnoticed through drafting and review.

## The route that works
RSB opinions are Cellar works registered with the identifier `pi_com:SEC(YYYY)N` (no space,
no "final"). The SPARQL endpoint finds the work by `cdm:work_id_document`; the work URI serves
the opinion as PDF only (`Accept: application/pdf`, `Accept-Language: eng`). EUR-Lex pages for
the same documents answer automated clients with an empty HTTP 202 (bot protection), so they are
no alternative. Recent opinions (2025-2026 proposals) were not in Cellar yet when checked.

## What the opinions contain
Mostly critique of how the draft assessment was written (problem definition, baselines, option
design, evidence, terms, stakeholder views), usually addressed in the published assessment. Few
points name an effect of the law that the final assessment lacks. In the first pilot the drafter
produced two RSB points for one case; the judges and the tie-break dropped both as generic method
remarks or as not tied to the case's articles.

## Rules
- A fallback that changes what the answer key is made from must fail loudly or appear in every
  summary a person reads (`fetch_rsb_opinions.py` prints a table; drafting prints the status).
- Check a cached status field before claiming a source was used.
- Keep the fallback text for anchors already checked against it when the real source arrives.
