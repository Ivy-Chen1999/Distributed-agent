-- Promotion gate (self-evolution plan 2026-10-06-001, U7; R28, R29). Applied only by
-- womm.eval.holdout, never by womm.api.db.Database.migrate().

-- Resumable holdout comparisons: one scored run per (system version, judge, code, internal case
-- key, repetition). A restarted comparison reuses finished runs, and a later comparison reuses
-- the incumbent's runs for the same judge and code. Keys are internal hashes, never case ids.
CREATE TABLE holdout.compare_progress (
    version_id    text NOT NULL,
    judge_version text NOT NULL,
    code_version  text NOT NULL,
    case_key      text NOT NULL,
    repetition    integer NOT NULL CHECK (repetition >= 1),
    score         jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (version_id, judge_version, code_version, case_key, repetition)
);

-- The gate's decision on a comparison sits next to the comparison it was made from. A gate
-- comparison has a gate_id; comparisons run outside the gate have none and no decision.
ALTER TABLE holdout.compare_audit
    ADD COLUMN gate_id  text UNIQUE,
    ADD COLUMN cycle_id text,
    ADD COLUMN decision jsonb;
