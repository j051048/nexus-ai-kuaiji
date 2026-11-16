CREATE TABLE IF NOT EXISTS repair_rate_cards (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES books(id),
  vehicle_type TEXT NOT NULL,
  rate_cents INTEGER NOT NULL CHECK(rate_cents > 0),
  valid_from TEXT NOT NULL,
  valid_to TEXT NOT NULL,
  coverage_scope TEXT NOT NULL,
  source_ref TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_repair_rate_cards_book ON repair_rate_cards(book_id,vehicle_type,valid_from);
