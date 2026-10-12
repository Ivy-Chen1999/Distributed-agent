# IA cost check, first scored run: dev-only, 2026-10-12

EU cost plan R6/R7, runbook "Cost analysis". **Dev-only**: `v1.0-cost` (`sv_1f12d8afd1e7`) on the
`claude_code` backend, 3 repetitions per version, sweeps made at code `dcde689`. Scored against
the verified IA cost reference at commit `d771489` (11 of 11 figures matched to the IA text;
EU-level FTE basis section 6.2). The plan's defaults (effort-type mapping, EUR band edges,
`not_costed`, R7 = `added` units) were confirmed before this run.

## R6: the proposal against the IA's cost figures

| Metric | Mean (min, max) over 3 repetitions |
|---|---|
| cost_recall | 0.93 (0.80, 1.00) |
| band_exact | 0.26 (0.22, 0.33) |
| band_within_one | 0.74 (0.56, 0.89) |
| payer_recurrence_agreement | 1.00 (1.00, 1.00) |
| rank_tau_b (descriptive, n = 3-5) | 0.29 (0.26, 0.32) |

Payers of the scored records by basis: rule table 264, rule field 198, inferred 40, unknown 41
(recall depends on this fallback). Provisions WOMM costs at medium or higher that no IA item
covers are listed in the JSON and Markdown report under `runs/cost_check/` (local): among them
the prohibitions, penalties, sandboxes and SME measures.

## R7: costs the IA could not see (not scored)

On the adopted act: 276 estimated cost records sit on obligations added after the proposal (111
more not costed); 95 of the 234 records at medium or high (41%) sit in added text. The largest
payers there are the Commission (53), unidentified payers (43), providers (40) and the AI Office
(30).

## Limits

The IA quantifies about 11 cost items covering about a quarter of the proposal's duties; IA
silence is not "no cost". Bands are ordinal classes per entity and item; nothing sums euros.
`rank_tau_b` is over at most five items and never a headline. A formal run needs the api backend
(`v1.0-cost-api`) once a key exists.
