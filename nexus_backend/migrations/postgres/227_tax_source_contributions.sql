CREATE TABLE IF NOT EXISTS accounting.tax_source_contributions (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL,
  source_file_id TEXT NOT NULL,
  row_number INTEGER NOT NULL,
  output_kind TEXT NOT NULL CHECK (output_kind IN ('workpaper','preview','approval')),
  output_id TEXT NOT NULL,
  amount_cents BIGINT NOT NULL,
  quantity_milli BIGINT NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS tax_source_contributions_row_uq
  ON accounting.tax_source_contributions(source_file_id, row_number, output_kind, output_id);
ALTER TABLE accounting.tax_source_contributions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_source_contributions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_source_contributions_read ON accounting.tax_source_contributions;
CREATE POLICY tax_source_contributions_read ON accounting.tax_source_contributions
  FOR SELECT TO accounting_app USING (accounting.company_visible(company_id));
DROP POLICY IF EXISTS tax_source_contributions_write ON accounting.tax_source_contributions;
CREATE POLICY tax_source_contributions_write ON accounting.tax_source_contributions
  FOR ALL TO accounting_app USING (accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id))
  WITH CHECK (accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id));
REVOKE ALL ON accounting.tax_source_contributions FROM PUBLIC, accounting_app, accounting_system;
GRANT SELECT, INSERT, UPDATE, DELETE ON accounting.tax_source_contributions TO accounting_app;
