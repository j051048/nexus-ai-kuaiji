-- compatibility: incremental
CREATE TABLE IF NOT EXISTS accounting.tax_source_files (
 id TEXT PRIMARY KEY,
 book_id TEXT NOT NULL,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 period TEXT NOT NULL,
 source_system TEXT NOT NULL,
 external_id TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),
 filename TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
 sheet_name TEXT NOT NULL,
 rule_version TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('active','superseded')),
 rows_json TEXT NOT NULL CHECK(jsonb_typeof(rows_json::jsonb) IS NOT NULL),
 amount_total_cents INTEGER NOT NULL,
 quantity_milli INTEGER NOT NULL,
 fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
 superseded_at TEXT,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(book_id,period,source_system,external_id,version)
);
CREATE INDEX IF NOT EXISTS idx_tax_source_active ON accounting.tax_source_files(book_id,period,source_system,external_id,status);
ALTER TABLE accounting.tax_source_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_source_files FORCE ROW LEVEL SECURITY;
CREATE POLICY tax_source_files_read ON accounting.tax_source_files FOR SELECT TO accounting_app USING(accounting.company_visible(company_id));
CREATE POLICY tax_source_files_write ON accounting.tax_source_files FOR ALL TO accounting_app USING(accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id)) WITH CHECK(accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id));
GRANT SELECT,INSERT,UPDATE ON accounting.tax_source_files TO accounting_app;
REVOKE ALL ON accounting.tax_source_files FROM PUBLIC;
