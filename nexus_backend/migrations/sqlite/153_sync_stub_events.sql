-- Local, signed synchronization stand-in. No external network is performed.
CREATE TABLE IF NOT EXISTS sync_stub_events (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    book_id TEXT NOT NULL REFERENCES books(id),
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    version INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    signature_verified INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'applied',
    cursor INTEGER NOT NULL,
    UNIQUE(firm_id, source_id, external_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS sync_stub_events_cursor
    ON sync_stub_events(firm_id, source_id, cursor);
CREATE INDEX IF NOT EXISTS sync_stub_events_stream
    ON sync_stub_events(firm_id, source_id, cursor);
