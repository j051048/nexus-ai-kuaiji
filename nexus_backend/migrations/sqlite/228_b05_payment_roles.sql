-- compatibility: incremental
-- 出纳（cashier）与资金审批人（treasury_approver）：与账套读写权限正交的付款岗位。
-- SQLite 无 RLS，用「禁止删除」触发器保证与生产同一契约：撤销只能改状态，不能抹掉历史。

CREATE TABLE IF NOT EXISTS book_payment_roles(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  book_id TEXT NOT NULL REFERENCES books(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  role TEXT NOT NULL CHECK(role IN('cashier','treasury_approver')),
  state TEXT NOT NULL DEFAULT 'active' CHECK(state IN('active','revoked')),
  reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
  granted_by TEXT NOT NULL REFERENCES users(id),
  granted_at TEXT NOT NULL,
  revoked_by TEXT REFERENCES users(id),
  revoked_at TEXT,
  CHECK(state='active' OR (revoked_by IS NOT NULL AND revoked_at IS NOT NULL)),
  UNIQUE(book_id,user_id,role)
);
CREATE INDEX IF NOT EXISTS book_payment_roles_actor
  ON book_payment_roles(book_id,user_id,state);

CREATE TRIGGER IF NOT EXISTS book_payment_roles_no_delete
 BEFORE DELETE ON book_payment_roles
BEGIN SELECT RAISE(ABORT,'payment role grants are revoked, never deleted'); END;
