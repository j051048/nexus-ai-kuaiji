-- compatibility: breaking
-- OPT06-A: firm-level ban on single-operator review.
CREATE TABLE accounting.firm_review_controls(
  firm_id TEXT PRIMARY KEY REFERENCES accounting.firms(id),
  single_operator_disabled BOOLEAN NOT NULL,
  version INTEGER NOT NULL CHECK(version>0),
  reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 10 AND 1000),
  updated_by TEXT NOT NULL REFERENCES accounting.users(id),
  updated_at TEXT NOT NULL
);
ALTER TABLE accounting.firm_review_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.firm_review_controls FORCE ROW LEVEL SECURITY;
CREATE POLICY firm_review_controls_read ON accounting.firm_review_controls
  FOR SELECT USING(firm_id=accounting.actor_firm());
CREATE POLICY firm_review_controls_write ON accounting.firm_review_controls
  FOR ALL USING(firm_id=accounting.actor_firm())
  WITH CHECK(firm_id=accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()));
GRANT SELECT,INSERT,UPDATE ON accounting.firm_review_controls TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.ledger_review_policy_single_operator_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.mode='single_operator' AND EXISTS(
    SELECT 1 FROM accounting.firm_review_controls c
    WHERE c.firm_id=NEW.firm_id AND c.single_operator_disabled
  ) THEN
    RAISE EXCEPTION 'single_operator disabled by firm policy' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS ledger_review_policy_single_operator_guard ON accounting.ledger_review_policies;
CREATE TRIGGER ledger_review_policy_single_operator_guard
BEFORE INSERT ON accounting.ledger_review_policies
FOR EACH ROW EXECUTE FUNCTION accounting.ledger_review_policy_single_operator_guard();
