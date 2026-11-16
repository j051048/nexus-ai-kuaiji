CREATE TABLE IF NOT EXISTS lease_invoice_reds (
  id TEXT PRIMARY KEY,
  contract_id TEXT NOT NULL REFERENCES lease_contracts(id),
  original_reference TEXT NOT NULL,
  red_reference TEXT NOT NULL,
  period TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
  reason TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(contract_id, red_reference),
  UNIQUE(contract_id, original_reference)
);
CREATE TRIGGER IF NOT EXISTS lease_invoice_reds_immutable
BEFORE UPDATE ON lease_invoice_reds
BEGIN SELECT RAISE(ABORT, 'lease invoice reds are immutable'); END;
CREATE TRIGGER IF NOT EXISTS lease_invoice_reds_no_delete
BEFORE DELETE ON lease_invoice_reds
BEGIN SELECT RAISE(ABORT, 'lease invoice reds are immutable'); END;
