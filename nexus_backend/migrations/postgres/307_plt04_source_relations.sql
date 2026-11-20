-- compatibility: incremental
-- PLT04: verified settlement/bank/fee source relation shared by the assistant
-- and the fee-variance register.  Registration is explicit: amounts, periods and
-- source versions must agree before a difference can be attributed to a fee.
CREATE TABLE accounting.fee_source_relations (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  period TEXT NOT NULL,
  settlement_source_id TEXT NOT NULL,
  bank_source_id TEXT NOT NULL,
  fee_source_id TEXT,
  settlement_version TEXT NOT NULL,
  bank_version TEXT NOT NULL,
  fee_version TEXT,
  settlement_amount_cents BIGINT NOT NULL CHECK(settlement_amount_cents>0),
  bank_amount_cents BIGINT NOT NULL CHECK(bank_amount_cents>=0),
  fee_amount_cents BIGINT CHECK(fee_amount_cents IS NULL OR fee_amount_cents>=0),
  difference_cents BIGINT NOT NULL,
  relation_sha256 TEXT NOT NULL,
  verified_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TIMESTAMPTZ NOT NULL,
  UNIQUE(book_id,settlement_source_id,bank_source_id,fee_source_id)
);
CREATE INDEX idx_fee_source_relation_bank ON accounting.fee_source_relations(book_id,bank_source_id);

ALTER TABLE accounting.fee_receivable_variances ADD COLUMN relation_id TEXT;

ALTER TABLE accounting.fee_source_relations ENABLE ROW LEVEL SECURITY;
CREATE POLICY fee_source_relations_read ON accounting.fee_source_relations
  FOR SELECT USING (accounting.book_allowed(book_id));
CREATE POLICY fee_source_relations_write ON accounting.fee_source_relations
  FOR INSERT WITH CHECK (accounting.book_allowed(book_id, TRUE));
GRANT SELECT,INSERT ON accounting.fee_source_relations TO accounting_app;
REVOKE ALL ON accounting.fee_source_relations FROM PUBLIC;

CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fee_source_relations
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
