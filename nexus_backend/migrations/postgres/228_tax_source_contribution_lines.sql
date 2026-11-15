CREATE TABLE IF NOT EXISTS accounting.tax_source_contribution_lines (
  id TEXT PRIMARY KEY,
  contribution_id TEXT NOT NULL REFERENCES accounting.tax_source_contributions(id) ON DELETE CASCADE,
  output_line_id TEXT NOT NULL,
  amount_cents BIGINT NOT NULL,
  quantity_milli BIGINT NOT NULL DEFAULT 0,
  UNIQUE (contribution_id, output_line_id)
);
CREATE INDEX IF NOT EXISTS tax_source_contribution_lines_output_idx
  ON accounting.tax_source_contribution_lines(output_line_id);
ALTER TABLE accounting.tax_source_contribution_lines ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_source_contribution_lines FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tax_source_contribution_lines_tenant ON accounting.tax_source_contribution_lines;
CREATE POLICY tax_source_contribution_lines_tenant ON accounting.tax_source_contribution_lines
  FOR ALL TO accounting_app
  USING (EXISTS (
    SELECT 1 FROM accounting.tax_source_contributions c
    WHERE c.id = contribution_id AND accounting.company_visible(c.company_id)
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM accounting.tax_source_contributions c
    WHERE c.id = contribution_id AND accounting.company_visible(c.company_id)
  ));
REVOKE ALL ON accounting.tax_source_contribution_lines FROM PUBLIC;
GRANT SELECT, INSERT, DELETE ON accounting.tax_source_contribution_lines TO accounting_app;
