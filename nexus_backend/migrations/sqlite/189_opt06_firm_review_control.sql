-- OPT06-A: firm-level ban on single-operator review.
CREATE TABLE firm_review_controls(
  firm_id TEXT PRIMARY KEY REFERENCES firms(id),
  single_operator_disabled INTEGER NOT NULL CHECK(single_operator_disabled IN(0,1)),
  version INTEGER NOT NULL CHECK(version>0),
  reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 10 AND 1000),
  updated_by TEXT NOT NULL REFERENCES users(id),
  updated_at TEXT NOT NULL
);

CREATE TRIGGER ledger_review_policy_single_operator_guard
BEFORE INSERT ON ledger_review_policies
WHEN NEW.mode='single_operator'
 AND EXISTS(SELECT 1 FROM firm_review_controls c WHERE c.firm_id=NEW.firm_id AND c.single_operator_disabled=1)
BEGIN
  SELECT RAISE(ABORT,'single_operator disabled by firm policy');
END;
