-- WOMM v0.1 schema: runs, live events, decisions, failures, system versions.

CREATE TABLE system_versions (
    version_id    text PRIMARY KEY,
    source_path   text,
    spec          jsonb NOT NULL,
    prompt_hashes jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE runs (
    run_id         text PRIMARY KEY,
    scenario_id    text NOT NULL,
    status         text NOT NULL,
    system_version text NOT NULL,
    error_kind     text,
    error          text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    started_at     timestamptz,
    finished_at    timestamptz,
    result         jsonb
);
CREATE INDEX runs_status_idx ON runs (status);

CREATE TABLE run_events (
    run_id  text NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    seq     integer NOT NULL,
    node    text NOT NULL,
    event   text NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    at      timestamptz NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE decision_records (
    id             bigserial PRIMARY KEY,
    run_id         text NOT NULL REFERENCES runs (run_id) ON DELETE CASCADE,
    decision_point text NOT NULL,
    subject        text NOT NULL,
    decision       text NOT NULL,
    probability    double precision,
    mode           text NOT NULL,
    decider        text NOT NULL,
    system_version text NOT NULL,
    error          text,
    truncated      boolean NOT NULL DEFAULT false,
    created_at     timestamptz NOT NULL
);
CREATE INDEX decision_records_run_idx ON decision_records (run_id);

-- R14b: failures from evaluation (train/val only in v1; the holdout never lands here).
CREATE TABLE failures (
    id             bigserial PRIMARY KEY,
    run_id         text,
    case_id        text NOT NULL,
    scenario_id    text NOT NULL,
    agent          text,
    category       text NOT NULL,
    detail         jsonb NOT NULL DEFAULT '{}'::jsonb,
    system_version text NOT NULL,
    git_sha        text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX failures_version_idx ON failures (system_version);
