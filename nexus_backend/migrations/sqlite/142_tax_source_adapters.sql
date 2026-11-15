CREATE TABLE tax_source_files(
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  period TEXT NOT NULL,
  source_system TEXT NOT NULL CHECK(length(trim(source_system)) BETWEEN 1 AND 80),
  external_id TEXT NOT NULL CHECK(length(trim(external_id)) BETWEEN 1 AND 200),
  version INTEGER NOT NULL CHECK(version > 0),
  filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
  sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
  size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
  sheet_name TEXT NOT NULL,
  rule_version TEXT NOT NULL CHECK(length(trim(rule_version)) BETWEEN 1 AND 80),
  status TEXT NOT NULL CHECK(status IN ('active','superseded')),
  rows_json TEXT NOT NULL CHECK(json_valid(rows_json)),
  amount_total_cents INTEGER NOT NULL,
  quantity_milli INTEGER NOT NULL,
  fingerprint TEXT NOT NULL CHECK(length(fingerprint) = 64),
  superseded_at TEXT,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE(book_id, period, source_system, external_id, version),
  FOREIGN KEY(book_id, firm_id, company_id) REFERENCES books(id, firm_id, company_id)
);
CREATE INDEX idx_tax_source_active ON tax_source_files(book_id, period, source_system, external_id, status);
CREATE TRIGGER tax_source_files_update BEFORE UPDATE ON tax_source_files
BEGIN
  SELECT CASE WHEN NEW.id<>OLD.id OR NEW.book_id<>OLD.book_id OR NEW.firm_id<>OLD.firm_id
    OR NEW.company_id<>OLD.company_id OR NEW.source_system<>OLD.source_system
    OR NEW.external_id<>OLD.external_id OR NEW.version<>OLD.version
    OR NEW.sha256<>OLD.sha256
  THEN RAISE(ABORT,'Tax source identity is immutable') END;
END;
CREATE TRIGGER tax_source_files_delete BEFORE DELETE ON tax_source_files
BEGIN SELECT RAISE(ABORT,'Tax source files are retained'); END;
