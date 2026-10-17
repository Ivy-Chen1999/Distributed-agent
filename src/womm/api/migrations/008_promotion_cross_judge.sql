-- Cross-judge check on page 4 (plan 2026-10-15-001, U2; owner decision 2026-10-15; R36 page 4).
-- Written by `womm evolve rescore` onto the gate's existing promotion_decisions row only (a gate
-- whose policy did not publish its summary has no row and gets nothing). Aggregates only: the
-- label (agrees / cross-judge-sensitive / inconclusive), the re-scored coverage mean delta and
-- its CI95, the re-scoring judge as backend:model, the gate's own coverage delta and the date
-- of the re-score; never a case id or a per-case value. Report-only: it never changes the
-- decision. NULL: the cross-judge check was not run.
ALTER TABLE promotion_decisions ADD COLUMN cross_judge jsonb;
