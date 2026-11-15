CREATE TABLE IF NOT EXISTS tax_source_contributions (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL,
  source_file_id TEXT NOT NULL,
  row_number INTEGER NOT NULL,
  output_kind TEXT NOT NULL CHECK (output_kind IN ('workpaper','preview','approval')),
  output_id TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  quantity_milli INTEGER NOT NULL DEFAULT 0,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (source_file_id, row_number, output_kind, output_id)
);
CREATE INDEX IF NOT EXISTS tax_source_contributions_output_idx
  ON tax_source_contributions(book_id, output_kind, output_id);
