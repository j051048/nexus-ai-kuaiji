-- compatibility: incremental
ALTER TABLE accounting_policies ADD COLUMN fee_variance_reasons_json TEXT NOT NULL DEFAULT '[]';

CREATE TABLE fee_receivable_variances (
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id),
  period TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('underpaid','overpaid','unmatched','cross_period','many_to_one','one_to_many')),
  receivable_id TEXT,
  bank_source_id TEXT,
  expected_cents INTEGER NOT NULL CHECK(expected_cents>=0),
  actual_cents INTEGER NOT NULL CHECK(actual_cents>=0),
  difference_cents INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','handled','reversed')),
  reason_code TEXT NOT NULL DEFAULT 'unresolved',
  evidence_ref TEXT NOT NULL DEFAULT '',
  policy_version INTEGER,
  policy_fingerprint TEXT NOT NULL DEFAULT '',
  handled_by TEXT REFERENCES users(id),
  handled_at TEXT,
  reversed_by TEXT REFERENCES users(id),
  reversed_at TEXT,
  reversal_reason TEXT NOT NULL DEFAULT '',
  draft_id TEXT,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  request_fingerprint TEXT NOT NULL,
  UNIQUE(created_by,idempotency_key)
);
CREATE INDEX idx_fee_variance_book_status ON fee_receivable_variances(book_id,period,status);
CREATE INDEX idx_fee_variance_receivable ON fee_receivable_variances(receivable_id);

CREATE TRIGGER fee_variance_immutable_identity BEFORE UPDATE ON fee_receivable_variances
WHEN NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id
  OR NEW.book_id<>OLD.book_id OR NEW.period<>OLD.period OR NEW.kind<>OLD.kind
  OR NEW.expected_cents<>OLD.expected_cents OR NEW.actual_cents<>OLD.actual_cents
  OR NEW.difference_cents<>OLD.difference_cents OR NEW.created_by<>OLD.created_by
  OR NEW.created_at<>OLD.created_at
BEGIN SELECT RAISE(ABORT,'fee variance identity is immutable'); END;

CREATE TRIGGER fee_variance_no_delete BEFORE DELETE ON fee_receivable_variances
BEGIN SELECT RAISE(ABORT,'fee variances are append-only history'); END;
