-- compatibility: breaking
-- Forward repair only: a ledger identity is not an account identity. Preserve
-- exact account/ledger/firm/company binding and live manager authorization.
DROP POLICY b02_account_versions_read ON accounting.ledger_account_versions;
DROP POLICY b02_account_versions_write ON accounting.ledger_account_versions;

CREATE POLICY b02_account_versions_read ON accounting.ledger_account_versions
 FOR SELECT TO accounting_app
 USING(accounting.actor_active() AND firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.ledger_accounts a
  WHERE a.id=ledger_account_versions.account_id
   AND a.ledger_id=ledger_account_versions.ledger_id
   AND a.firm_id=ledger_account_versions.firm_id
   AND a.company_id=ledger_account_versions.company_id
   AND (accounting.firm_manager(a.firm_id) OR EXISTS(
    SELECT 1 FROM accounting.ledger_managers m
    WHERE m.ledger_id=a.ledger_id AND m.user_id=accounting.actor_user()))));

CREATE POLICY b02_account_versions_write ON accounting.ledger_account_versions
 FOR ALL TO accounting_app
 USING(accounting.actor_active() AND firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.ledger_accounts a
  WHERE a.id=ledger_account_versions.account_id
   AND a.ledger_id=ledger_account_versions.ledger_id
   AND a.firm_id=ledger_account_versions.firm_id
   AND a.company_id=ledger_account_versions.company_id
   AND (accounting.firm_manager(a.firm_id) OR EXISTS(
    SELECT 1 FROM accounting.ledger_managers m
    WHERE m.ledger_id=a.ledger_id AND m.user_id=accounting.actor_user()))))
 WITH CHECK(accounting.actor_active() AND firm_id=accounting.actor_firm() AND EXISTS(
  SELECT 1 FROM accounting.ledger_accounts a
  WHERE a.id=ledger_account_versions.account_id
   AND a.ledger_id=ledger_account_versions.ledger_id
   AND a.firm_id=ledger_account_versions.firm_id
   AND a.company_id=ledger_account_versions.company_id
   AND (accounting.firm_manager(a.firm_id) OR EXISTS(
    SELECT 1 FROM accounting.ledger_managers m
    WHERE m.ledger_id=a.ledger_id AND m.user_id=accounting.actor_user()))));
