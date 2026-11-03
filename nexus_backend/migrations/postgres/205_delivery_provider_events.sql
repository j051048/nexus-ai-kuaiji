-- compatibility: additive
-- NEXT06: append-only provider receipt evidence. The API worker inserts only
-- after signature/timestamp verification; tenant readers see their own rows.
CREATE TABLE IF NOT EXISTS accounting.delivery_provider_events(
 id TEXT PRIMARY KEY,
 notification_id TEXT NOT NULL REFERENCES accounting.delivery_notifications(id),
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 provider_event_id TEXT NOT NULL UNIQUE,
 event_type TEXT NOT NULL CHECK(event_type IN('accepted','delivered','viewed','bounced')),
 occurred_at TEXT NOT NULL,
 received_at TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
 signature_timestamp BIGINT NOT NULL,
 raw_size INTEGER NOT NULL CHECK(raw_size>0 AND raw_size<=65536)
);
CREATE INDEX IF NOT EXISTS idx_delivery_provider_events_timeline
 ON accounting.delivery_provider_events(notification_id, occurred_at, received_at);

ALTER TABLE accounting.delivery_provider_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.delivery_provider_events FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.delivery_provider_events FROM PUBLIC, accounting_app, accounting_worker;
GRANT SELECT ON accounting.delivery_provider_events TO accounting_app;
GRANT SELECT, INSERT ON accounting.delivery_provider_events TO accounting_worker;

CREATE POLICY delivery_provider_events_read ON accounting.delivery_provider_events
 FOR SELECT TO accounting_app
 USING (firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY delivery_provider_events_insert ON accounting.delivery_provider_events
 FOR INSERT TO accounting_worker WITH CHECK (true);

CREATE OR REPLACE FUNCTION accounting.delivery_provider_events_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Provider receipt events are append-only';
END $$;
DROP TRIGGER IF EXISTS delivery_provider_events_append_only ON accounting.delivery_provider_events;
CREATE TRIGGER delivery_provider_events_append_only
 BEFORE UPDATE OR DELETE ON accounting.delivery_provider_events
 FOR EACH ROW EXECUTE FUNCTION accounting.delivery_provider_events_append_only();
