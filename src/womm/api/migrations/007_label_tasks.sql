-- Hosted labelling (womm.api.labels): the labelling pages of the blind reference-answer check
-- and of the coverage-judge calibration, and the answers people give on them. Train/val
-- material only: the owner uploads the pages from a local sample, and the upload refuses
-- holdout fixtures. The tool's or judge's decisions (key.private.json) never come here.

-- One row per (sample, annotator): the cards that annotator labels, as the page shows them.
CREATE TABLE label_tasks (
    sample       text NOT NULL,
    annotator    text NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('reference_check', 'calibration')),
    cards        jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    submitted_at timestamptz,
    PRIMARY KEY (sample, annotator)
);

-- One row per answered card, saved as the annotator goes; the last answer wins.
CREATE TABLE label_answers (
    sample     text NOT NULL,
    annotator  text NOT NULL,
    item_id    text NOT NULL,
    label      text NOT NULL,
    note       text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sample, annotator, item_id),
    FOREIGN KEY (sample, annotator) REFERENCES label_tasks ON DELETE CASCADE
);
