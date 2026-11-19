CREATE TABLE IF NOT EXISTS bridge_return_row_handlers (
  import_id TEXT NOT NULL REFERENCES bridge_return_imports(id),
  voucher_code TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id),
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('resolved','ignored')),
  reason TEXT NOT NULL,
  evidence_ref TEXT NOT NULL,
  handled_by TEXT NOT NULL REFERENCES users(id),
  handled_at TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  PRIMARY KEY(import_id, voucher_code)
);
CREATE INDEX IF NOT EXISTS idx_bridge_return_handlers_book
  ON bridge_return_row_handlers(book_id, handled_at);
CREATE TRIGGER IF NOT EXISTS bridge_return_handlers_no_delete
BEFORE DELETE ON bridge_return_row_handlers
BEGIN SELECT RAISE(ABORT, '差异处置记录不能删除'); END;
