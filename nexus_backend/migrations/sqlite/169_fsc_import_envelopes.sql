CREATE TABLE fsc_import_envelopes (
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
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    mapping_version TEXT NOT NULL,
    rule_version TEXT NOT NULL,
    unit TEXT NOT NULL,
    currency TEXT NOT NULL,
    rows_json TEXT NOT NULL,
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    adopted_count INTEGER NOT NULL CHECK (adopted_count >= 0),
    total_cents INTEGER NOT NULL,
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
    ON fsc_import_envelopes(book_id, period, source_system, source_object);

CREATE TRIGGER fsc_import_envelopes_identity_immutable
BEFORE UPDATE ON fsc_import_envelopes
WHEN OLD.id <> NEW.id
  OR OLD.firm_id <> NEW.firm_id
  OR OLD.company_id <> NEW.company_id
  OR OLD.book_id <> NEW.book_id
  OR OLD.period <> NEW.period
  OR OLD.source_system <> NEW.source_system
  OR OLD.source_object <> NEW.source_object
  OR OLD.external_id <> NEW.external_id
  OR OLD.source_version <> NEW.source_version
  OR OLD.sha256 <> NEW.sha256
  OR OLD.fingerprint <> NEW.fingerprint
BEGIN
    SELECT RAISE(ABORT, 'fsc import envelope identity is immutable');
END;

CREATE TRIGGER fsc_import_envelopes_terminal_immutable
BEFORE UPDATE ON fsc_import_envelopes
WHEN OLD.status IN ('confirmed','superseded') AND NEW.rows_json <> OLD.rows_json
BEGIN
    SELECT RAISE(ABORT, 'confirmed fsc import envelope is immutable');
END;

CREATE TABLE fsc_import_envelope_events (
    id TEXT PRIMARY KEY,
    envelope_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    at TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
