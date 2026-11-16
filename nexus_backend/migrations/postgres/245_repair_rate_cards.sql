CREATE TABLE IF NOT EXISTS accounting.repair_rate_cards (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  vehicle_type TEXT NOT NULL,
  rate_cents BIGINT NOT NULL CHECK(rate_cents > 0),
  valid_from TEXT NOT NULL,
  valid_to TEXT NOT NULL,
  coverage_scope TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repair_rate_cards_book ON accounting.repair_rate_cards(book_id,vehicle_type,valid_from);
ALTER TABLE accounting.repair_rate_cards ENABLE ROW LEVEL SECURITY;
CREATE POLICY repair_rate_cards_company ON accounting.repair_rate_cards
  USING (EXISTS (SELECT 1 FROM accounting.books b WHERE b.id=book_id AND accounting.company_visible(b.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.books b WHERE b.id=book_id AND accounting.company_visible(b.company_id)));
GRANT SELECT,INSERT,UPDATE ON accounting.repair_rate_cards TO accounting_app;
