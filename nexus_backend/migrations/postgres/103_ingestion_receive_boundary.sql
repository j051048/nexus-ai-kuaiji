-- compatibility: incremental
-- Keep the receive boundary auditable and idempotent before any consumer is
-- enabled. Raw events are bounded and immutable evidence; they never create an
-- accounting fact or an "imported" status on their own.
ALTER TABLE accounting.ingestion_runs ADD COLUMN IF NOT EXISTS event_key TEXT NOT NULL DEFAULT '';
ALTER TABLE accounting.ingestion_runs ADD COLUMN IF NOT EXISTS raw_event_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE accounting.ingestion_runs ADD COLUMN IF NOT EXISTS request_hash TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_runs_event
 ON accounting.ingestion_runs(connection_id,event_key) WHERE event_key<>'';

-- The receive path runs without a human actor, so it uses accounting_system to
-- append one immutable ingest audit row. It gets INSERT only, never UPDATE or
-- DELETE; the existing company/firm guard still validates the row.
GRANT INSERT ON accounting.audit_events TO accounting_system;
GRANT USAGE,SELECT ON SEQUENCE accounting.audit_events_id_seq TO accounting_system;
DROP POLICY IF EXISTS audit_system_append ON accounting.audit_events;
CREATE POLICY audit_system_append ON accounting.audit_events
 FOR INSERT TO accounting_system WITH CHECK(true);
