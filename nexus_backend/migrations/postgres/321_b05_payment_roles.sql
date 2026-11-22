-- compatibility: incremental
-- 出纳（cashier）与资金审批人（treasury_approver）：与账套读写权限正交的付款岗位。
-- 台账权限回答「能不能看这本账」，付款岗位回答「能不能发起/放款」——两者不能互相冒充：
--   出纳能制单但不能审批；资金审批人能审批但不能制单；两者都不自动获得账套编辑权。
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.book_payment_roles(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  user_id TEXT NOT NULL REFERENCES accounting.users(id),
  role TEXT NOT NULL CHECK(role IN('cashier','treasury_approver')),
  state TEXT NOT NULL DEFAULT 'active' CHECK(state IN('active','revoked')),
  reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
  granted_by TEXT NOT NULL REFERENCES accounting.users(id),
  granted_at TEXT NOT NULL,
  revoked_by TEXT REFERENCES accounting.users(id),
  revoked_at TEXT,
  CHECK(state='active' OR (revoked_by IS NOT NULL AND revoked_at IS NOT NULL)),
  UNIQUE(book_id,user_id,role)
);
CREATE INDEX IF NOT EXISTS book_payment_roles_actor
  ON accounting.book_payment_roles(book_id,user_id,state);

ALTER TABLE accounting.book_payment_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.book_payment_roles FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS book_payment_roles_read ON accounting.book_payment_roles;
CREATE POLICY book_payment_roles_read ON accounting.book_payment_roles
  FOR SELECT TO accounting_app
  USING (firm_id = accounting.actor_firm() AND accounting.book_allowed(book_id));

-- 授权与撤销只能由事务所管理者或企业管理者做：出纳不能给自己或同事发审批权。
DROP POLICY IF EXISTS book_payment_roles_insert ON accounting.book_payment_roles;
CREATE POLICY book_payment_roles_insert ON accounting.book_payment_roles
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.book_allowed(book_id)
    AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
    AND granted_by = accounting.actor_user()
    AND state = 'active'
  );
DROP POLICY IF EXISTS book_payment_roles_update ON accounting.book_payment_roles;
CREATE POLICY book_payment_roles_update ON accounting.book_payment_roles
  FOR UPDATE TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.book_allowed(book_id)
    AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
  )
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.book_allowed(book_id)
    AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
    AND (state = 'active' OR revoked_by = accounting.actor_user())
  );

-- 付款岗位判定：单个岗位与「任一付款岗位」两个谓词，付款表策略只引用这两个函数，
-- 避免把岗位判断散落成多份手写 SQL。
CREATE OR REPLACE FUNCTION accounting.book_payment_role(target TEXT, wanted TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.book_payment_roles r
  JOIN accounting.books b ON b.id=r.book_id AND b.firm_id=r.firm_id AND b.company_id=r.company_id
  JOIN accounting.users u ON u.id=r.user_id AND u.active=1
  WHERE r.book_id=target AND r.firm_id=accounting.actor_firm()
    AND r.user_id=accounting.actor_user() AND r.role=wanted AND r.state='active')
$$;
CREATE OR REPLACE FUNCTION accounting.book_payment_member(target TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_payment_role(target,'cashier') OR accounting.book_payment_role(target,'treasury_approver')
$$;

DROP POLICY IF EXISTS payment_orders_read ON accounting.payment_orders;
CREATE POLICY payment_orders_read ON accounting.payment_orders
  FOR SELECT TO accounting_app
  USING (
    (accounting.book_allowed(book_id) OR accounting.book_payment_member(book_id))
    AND firm_id = accounting.actor_firm()
  );
DROP POLICY IF EXISTS payment_orders_insert ON accounting.payment_orders;
CREATE POLICY payment_orders_insert ON accounting.payment_orders
  FOR INSERT TO accounting_app
  WITH CHECK (
    (accounting.book_allowed(book_id, true) OR accounting.book_payment_role(book_id,'cashier'))
    AND firm_id = accounting.actor_firm()
    AND maker_user_id = accounting.actor_user()
  );
DROP POLICY IF EXISTS payment_orders_update ON accounting.payment_orders;
CREATE POLICY payment_orders_update ON accounting.payment_orders
  FOR UPDATE TO accounting_app
  USING (
    (accounting.book_allowed(book_id, true) OR accounting.book_payment_member(book_id))
    AND firm_id = accounting.actor_firm()
  )
  WITH CHECK (
    (accounting.book_allowed(book_id, true) OR accounting.book_payment_member(book_id))
    AND firm_id = accounting.actor_firm()
  );

DROP POLICY IF EXISTS payment_events_read ON accounting.payment_events;
CREATE POLICY payment_events_read ON accounting.payment_events
  FOR SELECT TO accounting_app
  USING (accounting.book_allowed(book_id) OR accounting.book_payment_member(book_id));
DROP POLICY IF EXISTS payment_events_insert ON accounting.payment_events;
CREATE POLICY payment_events_insert ON accounting.payment_events
  FOR INSERT TO accounting_app
  WITH CHECK (
    (accounting.book_allowed(book_id, true) OR accounting.book_payment_member(book_id))
    AND actor_user_id = accounting.actor_user()
  );

REVOKE ALL ON accounting.book_payment_roles FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT, UPDATE ON accounting.book_payment_roles TO accounting_app;
REVOKE ALL ON FUNCTION accounting.book_payment_role(TEXT,TEXT)
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON FUNCTION accounting.book_payment_member(TEXT)
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT EXECUTE ON FUNCTION accounting.book_payment_role(TEXT,TEXT) TO accounting_app;
GRANT EXECUTE ON FUNCTION accounting.book_payment_member(TEXT) TO accounting_app;

-- 撤销用状态表达，不删记录：谁在什么时候拿过审批权，必须留痕。
CREATE OR REPLACE FUNCTION accounting.book_payment_roles_no_delete() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'payment role grants are revoked, never deleted';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS book_payment_roles_no_delete ON accounting.book_payment_roles;
CREATE TRIGGER book_payment_roles_no_delete BEFORE DELETE ON accounting.book_payment_roles
  FOR EACH ROW EXECUTE FUNCTION accounting.book_payment_roles_no_delete();

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.book_payment_roles;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.book_payment_roles
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
