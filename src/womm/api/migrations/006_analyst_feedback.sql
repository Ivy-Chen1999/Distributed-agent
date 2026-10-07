-- Analyst feedback (self-evolution plan 2026-10-06-001, U11; origin R31).
-- Train/val data only, like 004: every split column is constrained, so feedback on holdout
-- material can never be stored here.

-- Failure Memory gains two human kinds. `source` says who produced an event: the LLM judge
-- (every kind of 004) or an analyst (the analyst_* kinds), and the two can never be mixed up.
ALTER TABLE failure_events DROP CONSTRAINT failure_events_kind_check;
ALTER TABLE failure_events ADD CONSTRAINT failure_events_kind_check
    CHECK (kind IN ('missed_impact', 'missed_omission', 'unsupported_finding', 'expert_error',
                    'analyst_missing_impact', 'analyst_weak_evidence'));
ALTER TABLE failure_events ADD COLUMN source text NOT NULL DEFAULT 'judge'
    CHECK (source IN ('judge', 'human'));
ALTER TABLE failure_events ADD CONSTRAINT failure_events_source_matches_kind
    CHECK ((source = 'human') = (kind LIKE 'analyst\_%'));

-- One row per imported analyst mark (the audit log of R31). `feedback_id` is the LangSmith
-- feedback id, so an import is idempotent and every human Failure Memory row links back to the
-- mark that produced it. A mark may only refer to a scored train/val run already recorded in
-- Failure Memory (the foreign key). A `missing_impact` mark is also a golden-case candidate:
-- `queued` until it is staged into an open golden draft, where the review gate decides it; it is
-- never added to a golden case directly.
CREATE TABLE analyst_feedback (
    feedback_id      text PRIMARY KEY,
    mark             text NOT NULL CHECK (mark IN ('accept', 'reject', 'edit', 'missing_impact',
                                                   'weak_evidence')),
    system_version   text NOT NULL,
    case_id          text NOT NULL,
    fixture          text NOT NULL,
    split            text NOT NULL CHECK (split IN ('train', 'val')),
    run_id           text NOT NULL,
    trace_run_id     text NOT NULL,
    finding_id       text,
    analyst          text,
    note             text,
    payload          jsonb NOT NULL DEFAULT '{}'::jsonb,
    failure_event_id bigint REFERENCES failure_events (id),
    golden_candidate text CHECK (golden_candidate IN ('queued', 'staged')),
    golden_draft     text,
    created_at       timestamptz NOT NULL,
    imported_at      timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (system_version, case_id, run_id)
        REFERENCES failure_case_runs (system_version, case_id, run_id),
    CHECK ((golden_candidate IS NOT NULL) = (mark = 'missing_impact')),
    CHECK ((golden_candidate = 'staged') = (golden_draft IS NOT NULL)),
    CHECK ((failure_event_id IS NOT NULL) = (mark IN ('missing_impact', 'weak_evidence')))
);
CREATE INDEX analyst_feedback_version_idx ON analyst_feedback (system_version);
