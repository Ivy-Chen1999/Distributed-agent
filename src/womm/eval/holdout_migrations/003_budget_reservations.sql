-- Promotion gate budget reservations (self-evolution plan 2026-10-06-001, U7 review fix).
-- Applied only by womm.eval.holdout, never by womm.api.db.Database.migrate().
--
-- A gate reserves its holdout comparison before running it, under an advisory lock, so two
-- concurrent gates cannot both pass the per-cycle or total budget check. The reservation is
-- 'consumed' when a decision that consumes budget is recorded, 'released' when the comparison
-- aborts (inconclusive) or fails; a 'reserved' row is a comparison in flight, or one whose
-- process stopped (`womm evolve promote --resume` takes it over for the same pair).
CREATE TABLE holdout.budget_reservations (
    gate_id           text PRIMARY KEY,
    cycle_id          text NOT NULL,
    candidate_version text NOT NULL,
    baseline_version  text NOT NULL,
    status            text NOT NULL DEFAULT 'reserved'
                      CHECK (status IN ('reserved', 'consumed', 'released')),
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX budget_reservations_cycle_idx ON holdout.budget_reservations (cycle_id, status);
