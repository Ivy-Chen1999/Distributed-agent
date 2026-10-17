-- Promotion decision summaries (self-evolution plan 2026-10-06-001, U7; R28, R36 page 4).
-- Written by `womm evolve promote` only when evals/promotion_policy.yaml sets
-- publish_summary: true; the full comparison and the decision always stay in the holdout
-- database's audit. Aggregates only: no case id, scenario id or per-case value has a column
-- here, and the Improvement Planner never reads this table (womm.evolve.planner_view).
CREATE TABLE promotion_decisions (
    gate_id           text PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    cycle_id          text,
    candidate_version text NOT NULL,
    incumbent_version text NOT NULL,
    mode              text NOT NULL CHECK (mode IN ('statistical', 'weak', 'dev')),
    deployable        boolean NOT NULL,
    decision          text NOT NULL CHECK (decision IN ('promoted', 'rejected')),
    label             text NOT NULL,
    reasons           jsonb NOT NULL DEFAULT '[]'::jsonb,
    notes             jsonb NOT NULL DEFAULT '[]'::jsonb,
    deltas            jsonb NOT NULL DEFAULT '{}'::jsonb,
    n_proposals       integer NOT NULL,
    flags             jsonb NOT NULL DEFAULT '[]'::jsonb,
    policy_sha256     text NOT NULL,
    git_sha           text,
    r37               jsonb,
    CHECK (deployable = (mode <> 'dev'))
);
CREATE INDEX promotion_decisions_candidate_idx ON promotion_decisions (candidate_version);
