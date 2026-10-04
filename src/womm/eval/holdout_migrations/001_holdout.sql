-- Sealed holdout store (R23, AE3). Applied only by womm.eval.holdout (the holdout scripts),
-- never by womm.api.db.Database.migrate(), and only in the database HOLDOUT_DATABASE_URL names.

-- IA identifiers of each holdout proposal; they never appear in the public repository.
CREATE TABLE holdout.ia_references (
    fixture      text PRIMARY KEY,
    celex        text,
    ia_reference text NOT NULL,
    ia_celex     text,
    ia_date      date,
    rsb_ref      text,
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Holdout evaluation scenarios: never in data/fixtures/, injected at scoring time.
CREATE TABLE holdout.scenarios (
    fixture     text NOT NULL,
    scenario_id text NOT NULL,
    articles    jsonb NOT NULL,
    scenario    jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fixture, scenario_id)
);

CREATE TABLE holdout.cases (
    case_id      text PRIMARY KEY,
    fixture      text NOT NULL REFERENCES holdout.ia_references (fixture),
    scenario_id  text NOT NULL,
    split        text NOT NULL CHECK (split = 'holdout'),
    body         jsonb NOT NULL,
    body_sha256  text NOT NULL,
    verified_by  text NOT NULL,
    verified_on  date NOT NULL,
    draft_sha256 text,
    imported_at  timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (fixture, scenario_id) REFERENCES holdout.scenarios (fixture, scenario_id)
);

-- Category counts of the holdout (plan Revision: category-stratified split).
CREATE VIEW holdout.category_counts AS
SELECT coalesce(item ->> 'category', 'uncategorised') AS category, count(*) AS items
FROM holdout.cases, jsonb_array_elements(body -> 'expected_impacts') AS item
GROUP BY 1;

-- The only place holdout.compare results are written: aggregates, never case ids.
CREATE TABLE holdout.compare_audit (
    id                bigserial PRIMARY KEY,
    created_at        timestamptz NOT NULL DEFAULT now(),
    candidate_version text NOT NULL,
    baseline_version  text NOT NULL,
    repetitions       integer NOT NULL,
    git_sha           text,
    result            jsonb NOT NULL
);
