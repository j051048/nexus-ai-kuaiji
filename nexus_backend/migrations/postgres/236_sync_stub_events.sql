-- H6: signed local synchronization stand-in. No outbound connection is opened.
CREATE TABLE accounting.sync_stub_events(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 source_id TEXT NOT NULL CHECK(length(source_id) BETWEEN 1 AND 80),
 external_id TEXT NOT NULL CHECK(length(external_id) BETWEEN 1 AND 120),
 event_type TEXT NOT NULL CHECK(length(event_type) BETWEEN 3 AND 40),
 version INTEGER NOT NULL CHECK(version>0),
 payload_json TEXT NOT NULL,
 payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
 occurred_at TEXT NOT NULL,
 received_at TEXT NOT NULL,
 signature_verified BOOLEAN NOT NULL DEFAULT FALSE,
 status TEXT NOT NULL CHECK(status IN ('applied','superseded','unknown')),
 cursor BIGINT NOT NULL CHECK(cursor>0),
 UNIQUE(firm_id,source_id,external_id,version),
 UNIQUE(firm_id,source_id,cursor),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE INDEX idx_sync_stub_stream ON accounting.sync_stub_events(firm_id,source_id,cursor);

ALTER TABLE accounting.sync_stub_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY sync_stub_events_company ON accounting.sync_stub_events
 USING (accounting.company_visible(company_id))
 WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT,INSERT,UPDATE ON accounting.sync_stub_events TO accounting_app;
GRANT SELECT ON accounting.sync_stub_events TO accounting_projection;
