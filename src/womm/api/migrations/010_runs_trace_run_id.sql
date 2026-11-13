-- The LangSmith root run id of a run, set when the run starts (NULL when tracing was off), so
-- the console can link the trace of a run that crashed or is still running.
ALTER TABLE runs ADD COLUMN IF NOT EXISTS trace_run_id text;
