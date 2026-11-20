-- compatibility: breaking
CREATE TABLE IF NOT EXISTS accounting.fee_receivable_items (
  id TEXT PRIMARY KEY,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  batch_id TEXT NOT NULL,
  batch_row_id TEXT NOT NULL,
  period TEXT NOT NULL,
  driver_id TEXT,
  driver_name TEXT NOT NULL,
  vehicle_name TEXT NOT NULL,
  amount_cents BIGINT NOT NULL CHECK(amount_cents >= 0),
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','settled')),
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(batch_row_id)
);
CREATE INDEX IF NOT EXISTS fee_receivable_items_book_period
  ON accounting.fee_receivable_items(book_id, period, status);
ALTER TABLE accounting.fee_receivable_items ENABLE ROW LEVEL SECURITY;
CREATE POLICY fee_receivable_items_scope ON accounting.fee_receivable_items
  USING (EXISTS (SELECT 1 FROM accounting.books b WHERE b.id=book_id AND accounting.company_visible(b.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.books b WHERE b.id=book_id AND accounting.company_visible(b.company_id)));
GRANT SELECT,INSERT,UPDATE ON accounting.fee_receivable_items TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.fee_receivable_items_identity_guard() RETURNS trigger AS $$
BEGIN
  IF NEW.id <> OLD.id OR NEW.book_id <> OLD.book_id OR NEW.batch_id <> OLD.batch_id
     OR NEW.batch_row_id <> OLD.batch_row_id OR NEW.amount_cents <> OLD.amount_cents THEN
    RAISE EXCEPTION 'fee receivable identity/amount is immutable';
  END IF;
  RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS fee_receivable_items_identity ON accounting.fee_receivable_items;
CREATE TRIGGER fee_receivable_items_identity BEFORE UPDATE ON accounting.fee_receivable_items
FOR EACH ROW EXECUTE FUNCTION accounting.fee_receivable_items_identity_guard();
