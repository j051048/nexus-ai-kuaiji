-- compatibility: incremental
-- Keep the receive boundary auditable and idempotent before any consumer is
-- enabled. Raw events are bounded and immutable evidence; they never create an
-- accounting fact or an "imported" status on their own.
ALTER TABLE accounting.ingestion_runs ADD COLUMN IF NOT EXISTS event_key TEXT NOT NULL DEFAULT '';
ALTER TABLE accounting.ingestion_runs ADD COLUMN IF NOT EXISTS raw_event_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE accounting.ingestion_runs ADD COLUMN IF NOT EXISTS request_hash TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_runs_event
 ON accounting.ingestion_runs(connection_id,event_key) WHERE event_key<>'';
