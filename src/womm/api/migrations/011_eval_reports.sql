-- Published train/val evaluation reports, shown on the console's Overview for the deployed
-- system version (`womm eval publish`). Train/val only: every run row is constrained, so a
-- holdout result can never be stored here. No case content, judge text or scenario text: per
-- run only the counts behind coverage and omissions and the run's grounding and precision.
CREATE TABLE eval_reports (
    report_id             bigserial PRIMARY KEY,
    system_version        text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    git_sha               text,
    judge_version         text,
    repetitions           integer NOT NULL CHECK (repetitions >= 1),
    experiment_name       text,
    experiment_tenant_id  text,
    experiment_project_id text,
    summary               jsonb NOT NULL
);
CREATE INDEX eval_reports_version_idx ON eval_reports (system_version, created_at DESC);

CREATE TABLE eval_report_runs (
    report_id  bigint NOT NULL REFERENCES eval_reports (report_id) ON DELETE CASCADE,
    case_id    text NOT NULL,
    split      text NOT NULL CHECK (split IN ('train', 'val')),
    repetition integer NOT NULL CHECK (repetition >= 1),
    counts     jsonb NOT NULL,
    PRIMARY KEY (report_id, case_id, repetition)
);
