-- Decider model version, latency and token usage per bounded decision (Jev calibration data).
ALTER TABLE decision_records ADD COLUMN model text;
ALTER TABLE decision_records ADD COLUMN latency_s double precision;
ALTER TABLE decision_records ADD COLUMN input_tokens integer;
ALTER TABLE decision_records ADD COLUMN output_tokens integer;
