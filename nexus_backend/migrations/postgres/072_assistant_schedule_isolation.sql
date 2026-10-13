-- Internal global fairness state is accessed only through bounded worker
-- SECURITY DEFINER functions owned by the migration principal with BYPASSRLS.
-- No direct client/runtime table access or row policy is needed.
ALTER TABLE accounting.assistant_company_schedule ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_company_schedule FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE accounting.assistant_company_schedule
 FROM PUBLIC,accounting_app,accounting_system,accounting_worker;
