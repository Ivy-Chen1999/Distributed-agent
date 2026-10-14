# RSB companions: first run, 2026-10-12

Plan `docs/plans/2026-10-12-001-feat-expert-sources-and-onboarding-plan.md`, U1, U3 and U10.

## Opinions fetched

The RSB opinion was found in Cellar (pi_com work, PDF) for 15 of the 23 imported laws: all 11
practice laws and the 4 background-only laws. None of the 2025-2026 proposals (the sealed set)
has its opinion in Cellar yet, so the sealed cases get no companion now. The AI Act has no RSB
reference in the local index.

## Companions drafted

Drafted locally from the 11 practice drafts as they stand under the live reference check
(not committed; they wait for that check to score ROBUST, plan U5).

| Case | Drafted | Kept by the judges | Kept in all (with the tie-break) |
|---|---|---|---|
| Data Act | 2 | 0 | 0 |
| Cyber Resilience Act | 3 | 1 | 1 |
| Digital Services Act | 3 | 3 | 3 |
| Digital Markets Act | 3 | 2 | 2 |
| Data Governance Act | 3 | 2 | 3 |
| General Product Safety Regulation | 3 | 2 | 2 |
| Batteries Regulation | 3 | 2 | 2 |
| Platform Work Directive | 3 | 2 | 2 |
| Product Liability Directive | 1 | 1 | 1 |
| European Health Data Space | 4 | 0 | 0 |
| Gigabit Infrastructure Act | 3 | 2 | 2 |
| **Total** | **31** | **17** | **18** |

## The pilot gate

The plan's gate (U10) asked for at least 2 kept impact-level points on the pilot case (the Data
Act). It failed there: the judges and the tie-break dropped both points as a generic method remark
and as not tied to the case's articles. The owner decided beforehand to draft every case anyway;
across the 11 cases, 18 points are kept. Kept points include costs for national authorities taken
from the opinion's quantification tables, SME compliance costs, internal-market effects of
national implementation, and competition effects on owners of existing infrastructure. Some may
repeat what the impact assessment already says; that is for the people's check to judge.

## Next

- U5: once the live reference check scores ROBUST, the companion code is merged into each
  `review/case-*` branch and one companion file is committed per branch.
- U4: the classmates label the 14 companion items the tie-break ruled on (about 7 each, blind)
  on the same pages as the reference check (2026-10-13, sample `2026-10-14-3b23c3b9`, built with
  `scripts/reference_check.py sample --with-companions`; the 20 + 10 reference items are the
  same as in `2026-10-11-86dae710`, which was never sent). The 17 judges-accepted items are
  trusted on the reference check's measurement of that stratum, and labelled too only if it
  found an error there. Companions are published only if the tool is wrong on at most 10% of
  them, scored apart from the reference check.

## Re-ruling, 2026-10-14

Reading the disputed points before the check showed the tie-break held companion items to the IA
rule (the text must state the finding): it dropped 12 of the 13 disputed points for being board
requests, which is what a companion point is. The tie-break now knows each item's anchor source
and judges a board item by whether the request asks for that effect (commit `98be5f0`). Re-run
on the same companion drafts (no new drafting): 26 of 31 points kept (18 before); the five
dropped rest on context, legal derivation or a request only about how the report is written.
The re-ruled drafts are in `.cache/rsb_companions_v2/` and are the ones to commit (step 3b).
