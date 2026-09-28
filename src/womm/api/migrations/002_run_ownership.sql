-- Run ownership + heartbeats: a starting replica must not orphan another replica's live runs.
ALTER TABLE runs ADD COLUMN instance_id text;
ALTER TABLE runs ADD COLUMN heartbeat_at timestamptz;

-- Re-recording the same evaluation must not duplicate failure rows.
CREATE UNIQUE INDEX failures_dedupe_idx
    ON failures (run_id, case_id, category, coalesce(agent, ''));
