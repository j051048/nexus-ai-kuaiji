CREATE TABLE IF NOT EXISTS tax_source_contribution_lines (
  id TEXT PRIMARY KEY,
  contribution_id TEXT NOT NULL,
  output_line_id TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  quantity_milli INTEGER NOT NULL DEFAULT 0,
  UNIQUE (contribution_id, output_line_id)
);
CREATE INDEX IF NOT EXISTS tax_source_contribution_lines_output_idx
  ON tax_source_contribution_lines(output_line_id);
