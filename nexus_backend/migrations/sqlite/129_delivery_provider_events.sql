-- compatibilty: additive
-- NEXT06: provider receipt events are append-only external evidence.
-- They never change the notification state, never retry a send and never
-- stand in for a customer signature.
CREATE TABLE IF NOT EXISTS delivery_provider_events(
 id TEXT PRIMARY KEY,
 notification_id TEXT NOT NULL REFERENCES delivery_notifications(id),
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 provider_event_id TEXT NOT NULL UNIQUE,
 event_type TEXT NOT NULL CHECK(event_type IN('accepted','delivered','viewed','bounced')),
 occurred_at TEXT NOT NULL,
 received_at TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
 signature_timestamp INTEGER NOT NULL,
 raw_size INTEGER NOT NULL CHECK(raw_size>0 AND raw_size<=65536)
);
CREATE INDEX IF NOT EXISTS idx_delivery_provider_events_timeline
 ON delivery_provider_events(notification_id, occurred_at, received_at);

CREATE TRIGGER IF NOT EXISTS delivery_provider_events_no_update
BEFORE UPDATE ON delivery_provider_events
BEGIN
 SELECT RAISE(ABORT,'Provider receipt events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS delivery_provider_events_no_delete
BEFORE DELETE ON delivery_provider_events
BEGIN
 SELECT RAISE(ABORT,'Provider receipt events are append-only');
END;
