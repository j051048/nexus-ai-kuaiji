-- compatibility: breaking
CREATE TABLE IF NOT EXISTS accounting.lease_invoice_reds (
  id TEXT PRIMARY KEY,
  contract_id TEXT NOT NULL REFERENCES accounting.lease_contracts(id),
  original_reference TEXT NOT NULL,
  red_reference TEXT NOT NULL,
  period TEXT NOT NULL,
  amount_cents BIGINT NOT NULL CHECK(amount_cents > 0),
  reason TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(contract_id, red_reference),
  UNIQUE(contract_id, original_reference)
);
ALTER TABLE accounting.lease_invoice_reds ENABLE ROW LEVEL SECURITY;
CREATE POLICY lease_invoice_reds_scope ON accounting.lease_invoice_reds
  USING (EXISTS (SELECT 1 FROM accounting.lease_contracts c WHERE c.id=contract_id AND accounting.company_visible(c.company_id)))
  WITH CHECK (EXISTS (SELECT 1 FROM accounting.lease_contracts c WHERE c.id=contract_id AND accounting.company_visible(c.company_id)));
GRANT SELECT,INSERT ON accounting.lease_invoice_reds TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.lease_invoice_reds_immutable_guard() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'lease invoice reds are immutable'; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS lease_invoice_reds_immutable ON accounting.lease_invoice_reds;
CREATE TRIGGER lease_invoice_reds_immutable BEFORE UPDATE OR DELETE ON accounting.lease_invoice_reds
FOR EACH ROW EXECUTE FUNCTION accounting.lease_invoice_reds_immutable_guard();
