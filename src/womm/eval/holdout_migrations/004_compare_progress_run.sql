-- Cross-family judge re-score (plan 2026-10-15-001, U1). Applied only by womm.eval.holdout.
--
-- Each checkpointed holdout run keeps the part of its RunResult the coverage judge reads (dossier,
-- grounding, status and identity; no retrievals or source texts), so a second judge can re-score
-- the same runs. Rows saved before this migration have run NULL: the cache re-runs them, and a
-- re-score of a comparison that used them is refused.
ALTER TABLE holdout.compare_progress ADD COLUMN run jsonb;

-- Which checkpointed runs a gate comparison used (cached or fresh), so a re-score reads exactly
-- those. Keys are internal hashes, never case ids.
CREATE TABLE holdout.compare_runs (
    gate_id    text NOT NULL,
    version_id text NOT NULL,
    case_key   text NOT NULL,
    repetition integer NOT NULL CHECK (repetition >= 1),
    PRIMARY KEY (gate_id, version_id, case_key, repetition)
);

-- The judge and the per-version code a gate comparison's runs were checkpointed under.
ALTER TABLE holdout.compare_audit
    ADD COLUMN judge_version text,
    ADD COLUMN code_versions jsonb;
