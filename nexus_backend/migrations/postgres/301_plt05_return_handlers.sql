CREATE TABLE IF NOT EXISTS accounting.bridge_return_row_handlers (
  import_id TEXT NOT NULL REFERENCES accounting.bridge_return_imports(id),
  voucher_code TEXT NOT NULL,
  book_id TEXT NOT NULL,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('resolved','ignored')),
  reason TEXT NOT NULL,
  evidence_ref TEXT NOT NULL,
  handled_by TEXT NOT NULL REFERENCES accounting.users(id),
  handled_at TIMESTAMPTZ NOT NULL,
  fingerprint TEXT NOT NULL,
  PRIMARY KEY(import_id, voucher_code)
);
CREATE INDEX IF NOT EXISTS idx_bridge_return_handlers_book
  ON accounting.bridge_return_row_handlers(book_id, handled_at);
ALTER TABLE accounting.bridge_return_row_handlers ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bridge_return_row_handlers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bridge_return_handlers_firm ON accounting.bridge_return_row_handlers;
CREATE POLICY bridge_return_handlers_firm ON accounting.bridge_return_row_handlers
  USING (firm_id = accounting.actor_firm())
  WITH CHECK (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));
GRANT SELECT, INSERT ON accounting.bridge_return_row_handlers TO accounting_app;
REVOKE UPDATE, DELETE ON accounting.bridge_return_row_handlers FROM accounting_app;
