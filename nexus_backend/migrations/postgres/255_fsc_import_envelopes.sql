CREATE TABLE accounting.fsc_import_envelopes (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    book_id TEXT NOT NULL,
    period TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_object TEXT NOT NULL,
    external_id TEXT NOT NULL,
    source_version INTEGER NOT NULL CHECK (source_version > 0),
    filename TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL CHECK (length(sha256)=64),
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    mapping_version TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    unit TEXT NOT NULL,
    currency TEXT NOT NULL,
    rows_json JSONB NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    adopted_count INTEGER NOT NULL CHECK (adopted_count >= 0),
    total_cents BIGINT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('draft','confirmed','superseded')),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint)=64),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_by TEXT,
    confirmed_at TEXT,
    superseded_at TEXT,
    UNIQUE(book_id, source_system, source_object, external_id, source_version)
);
CREATE INDEX idx_fsc_import_envelopes_book
    ON accounting.fsc_import_envelopes(book_id, period, source_system, source_object);

ALTER TABLE accounting.fsc_import_envelopes ENABLE ROW LEVEL SECURITY;
CREATE POLICY fsc_import_envelopes_company_scope ON accounting.fsc_import_envelopes
    USING (accounting.company_visible(company_id))
    WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT, INSERT, UPDATE ON accounting.fsc_import_envelopes TO accounting_app;

CREATE TABLE accounting.fsc_import_envelope_events (
    id TEXT PRIMARY KEY,
    envelope_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    at TEXT NOT NULL,
    detail_json JSONB NOT NULL
);
ALTER TABLE accounting.fsc_import_envelope_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY fsc_import_events_company_scope ON accounting.fsc_import_envelope_events
    USING (EXISTS (
        SELECT 1 FROM accounting.fsc_import_envelopes e
         WHERE e.id = fsc_import_envelope_events.envelope_id
           AND accounting.company_visible(e.company_id)
    ))
    WITH CHECK (true);
GRANT SELECT, INSERT ON accounting.fsc_import_envelope_events TO accounting_app;
