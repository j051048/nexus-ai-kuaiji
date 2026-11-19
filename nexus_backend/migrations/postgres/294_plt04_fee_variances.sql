-- compatibility: incremental
ALTER TABLE accounting.accounting_policies
  ADD COLUMN fee_variance_reasons_json JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE accounting.fee_receivable_variances (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  period TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('underpaid','overpaid','unmatched','cross_period','many_to_one','one_to_many')),
  receivable_id TEXT,
  bank_source_id TEXT,
  expected_cents BIGINT NOT NULL CHECK(expected_cents>=0),
  actual_cents BIGINT NOT NULL CHECK(actual_cents>=0),
  difference_cents BIGINT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','handled','reversed')),
  reason_code TEXT NOT NULL DEFAULT 'unresolved',
  evidence_ref TEXT NOT NULL DEFAULT '',
  policy_version BIGINT,
  policy_fingerprint TEXT NOT NULL DEFAULT '',
  handled_by TEXT REFERENCES accounting.users(id),
  handled_at TIMESTAMPTZ,
  reversed_by TEXT REFERENCES accounting.users(id),
  reversed_at TIMESTAMPTZ,
  reversal_reason TEXT NOT NULL DEFAULT '',
  draft_id TEXT,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TIMESTAMPTZ NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_fingerprint TEXT NOT NULL,
  UNIQUE(created_by,idempotency_key)
);
CREATE INDEX idx_fee_variance_book_status ON accounting.fee_receivable_variances(book_id,period,status);
CREATE INDEX idx_fee_variance_receivable ON accounting.fee_receivable_variances(receivable_id);

CREATE OR REPLACE FUNCTION accounting.fee_variance_immutable_identity() RETURNS trigger AS $$
BEGIN
  IF NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id
    OR NEW.book_id<>OLD.book_id OR NEW.period<>OLD.period OR NEW.kind<>OLD.kind
    OR NEW.expected_cents<>OLD.expected_cents OR NEW.actual_cents<>OLD.actual_cents
    OR NEW.difference_cents<>OLD.difference_cents OR NEW.created_by<>OLD.created_by
    OR NEW.created_at<>OLD.created_at THEN
    RAISE EXCEPTION 'fee variance identity is immutable';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER fee_variance_immutable_identity BEFORE UPDATE ON accounting.fee_receivable_variances
FOR EACH ROW EXECUTE FUNCTION accounting.fee_variance_immutable_identity();

CREATE OR REPLACE FUNCTION accounting.fee_variance_no_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'fee variances are append-only history';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER fee_variance_no_delete BEFORE DELETE ON accounting.fee_receivable_variances
FOR EACH ROW EXECUTE FUNCTION accounting.fee_variance_no_delete();

ALTER TABLE accounting.fee_receivable_variances ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fee_receivable_variances FORCE ROW LEVEL SECURITY;
CREATE POLICY fee_variance_read ON accounting.fee_receivable_variances
 FOR SELECT TO accounting_app
 USING(accounting.book_allowed(book_id));
CREATE POLICY fee_variance_insert ON accounting.fee_receivable_variances
 FOR INSERT TO accounting_app
 WITH CHECK(accounting.book_allowed(book_id,TRUE));
CREATE POLICY fee_variance_update ON accounting.fee_receivable_variances
 FOR UPDATE TO accounting_app
 USING(accounting.book_allowed(book_id,TRUE))
 WITH CHECK(accounting.book_allowed(book_id,TRUE));
GRANT SELECT,INSERT,UPDATE ON accounting.fee_receivable_variances TO accounting_app;
