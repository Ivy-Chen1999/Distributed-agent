-- Self-evolution cycle (plan 2026-10-06-001). Train/val data only: every split column below is
-- constrained, so holdout cases, results or metrics can never be stored in this database.

-- U1 Failure Memory: one row per missed impact, missed omission, unsupported finding or expert
-- error of a scored train/val run. The legacy `failures` table stays for v0 compatibility.
CREATE TABLE failure_events (
    id              bigserial PRIMARY KEY,
    system_version  text NOT NULL,
    kind            text NOT NULL CHECK (kind IN ('missed_impact', 'missed_omission',
                                                  'unsupported_finding', 'expert_error')),
    case_id         text NOT NULL,
    fixture         text NOT NULL,
    split           text NOT NULL CHECK (split IN ('train', 'val')),
    item_id         text NOT NULL,
    category        text NOT NULL,
    touching_agents text[] NOT NULL DEFAULT '{}',
    owner           text NOT NULL,
    run_id          text NOT NULL,
    repetition      integer NOT NULL CHECK (repetition >= 1),
    detail          jsonb NOT NULL DEFAULT '{}'::jsonb,
    git_sha         text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, kind, item_id)
);
CREATE INDEX failure_events_version_idx ON failure_events (system_version);

-- The denominator of a miss rate: every scored train/val run of a case.
CREATE TABLE failure_case_runs (
    system_version text NOT NULL,
    case_id        text NOT NULL,
    fixture        text NOT NULL,
    split          text NOT NULL CHECK (split IN ('train', 'val')),
    run_id         text NOT NULL,
    repetition     integer NOT NULL CHECK (repetition >= 1),
    git_sha        text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (system_version, case_id, run_id)
);
