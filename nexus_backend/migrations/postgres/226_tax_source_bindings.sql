CREATE TABLE IF NOT EXISTS accounting.tax_source_bindings (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 period TEXT NOT NULL,
 source_file_id TEXT NOT NULL REFERENCES accounting.tax_source_files(id),
 source_system TEXT NOT NULL,
 external_id TEXT NOT NULL,
 source_fingerprint TEXT NOT NULL CHECK(length(source_fingerprint)=64),
 output_kind TEXT NOT NULL CHECK(output_kind IN('workpaper','preview','approval')),
 output_id TEXT NOT NULL,
 output_version INTEGER NOT NULL CHECK(output_version>0),
 frozen BOOLEAN NOT NULL DEFAULT FALSE,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 invalidated_at TEXT,
 invalidated_reason TEXT,
 UNIQUE(book_id,output_kind,output_id,output_version)
);
CREATE INDEX IF NOT EXISTS idx_tax_source_bindings_output ON accounting.tax_source_bindings(book_id,output_kind,output_id,output_version);
ALTER TABLE accounting.tax_source_bindings ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_source_bindings FORCE ROW LEVEL SECURITY;
CREATE POLICY tax_source_bindings_read ON accounting.tax_source_bindings FOR SELECT TO accounting_app USING(accounting.company_visible(company_id));
CREATE POLICY tax_source_bindings_write ON accounting.tax_source_bindings FOR ALL TO accounting_app USING(accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id)) WITH CHECK(accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id));
GRANT SELECT,INSERT,UPDATE ON accounting.tax_source_bindings TO accounting_app;
REVOKE ALL ON accounting.tax_source_bindings FROM PUBLIC;
