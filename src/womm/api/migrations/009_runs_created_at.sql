-- The console's run table and GET /runs list runs newest first (Database.list_runs); without
-- this index every call sorts the whole runs table.
CREATE INDEX IF NOT EXISTS runs_created_at_idx ON runs (created_at DESC);
