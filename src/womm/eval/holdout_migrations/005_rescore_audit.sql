-- Cross-family judge re-score (plan 2026-10-15-001, U3). Applied only by womm.eval.holdout.
--
-- Re-scored runs, checkpointed so a rescore stopped by a usage limit resumes. Keys are internal
-- hashes, never case ids.
CREATE TABLE holdout.rescore_progress (
    gate_id               text NOT NULL,
    rescore_judge_version text NOT NULL,
    version_id            text NOT NULL,
    case_key              text NOT NULL,
    repetition            integer NOT NULL CHECK (repetition >= 1),
    score                 jsonb NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (gate_id, rescore_judge_version, version_id, case_key, repetition)
);

-- One current re-score per gate (aggregates only); a replaced one is kept as superseded. Never
-- copied to the main database or Failure Memory (R28).
CREATE TABLE holdout.rescore_audit (
    id                    bigserial PRIMARY KEY,
    gate_id               text NOT NULL,
    rescore_judge_version text NOT NULL,
    result                jsonb NOT NULL,
    superseded            boolean NOT NULL DEFAULT false,
    created_at            timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX rescore_audit_current_idx ON holdout.rescore_audit (gate_id)
    WHERE NOT superseded;
