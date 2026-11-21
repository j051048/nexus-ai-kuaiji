-- compatibility: incremental
-- Payment orders carry their own maker/approver/reviewer so separation of duties
-- is enforced by the database, not only by the service layer.
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.payment_orders(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  period TEXT NOT NULL,
  payee TEXT NOT NULL,
  payee_account TEXT NOT NULL,
  amount_cents BIGINT NOT NULL CHECK(amount_cents > 0),
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN(
    'draft','pending_approval','approved','queued_for_bank','submitted',
    'succeeded','failed','bounced','bank_unknown')),
  maker_user_id TEXT NOT NULL REFERENCES accounting.users(id),
  approver_user_id TEXT REFERENCES accounting.users(id),
  reviewer_user_id TEXT REFERENCES accounting.users(id),
  escalated BOOLEAN NOT NULL DEFAULT FALSE,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(book_id, idempotency_key),
  CHECK(approver_user_id IS NULL OR approver_user_id <> maker_user_id),
  CHECK(reviewer_user_id IS NULL OR (reviewer_user_id <> maker_user_id
        AND (approver_user_id IS NULL OR reviewer_user_id <> approver_user_id))),
  CHECK(status NOT IN('failed','bounced') OR reason <> '')
);

CREATE TABLE IF NOT EXISTS accounting.payment_events(
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES accounting.payment_orders(id),
  book_id TEXT NOT NULL,
  from_status TEXT NOT NULL,
  to_status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  actor_user_id TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TEXT NOT NULL
);

ALTER TABLE accounting.payment_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.payment_orders FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS payment_orders_read ON accounting.payment_orders;
CREATE POLICY payment_orders_read ON accounting.payment_orders
  FOR SELECT TO accounting_app
  USING (accounting.book_allowed(book_id) AND firm_id = accounting.actor_firm());
DROP POLICY IF EXISTS payment_orders_insert ON accounting.payment_orders;
CREATE POLICY payment_orders_insert ON accounting.payment_orders
  FOR INSERT TO accounting_app
  WITH CHECK (accounting.book_allowed(book_id, true)
    AND firm_id = accounting.actor_firm()
    AND maker_user_id = accounting.actor_user());
DROP POLICY IF EXISTS payment_orders_update ON accounting.payment_orders;
CREATE POLICY payment_orders_update ON accounting.payment_orders
  FOR UPDATE TO accounting_app
  USING (accounting.book_allowed(book_id, true) AND firm_id = accounting.actor_firm())
  WITH CHECK (accounting.book_allowed(book_id, true) AND firm_id = accounting.actor_firm());

ALTER TABLE accounting.payment_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.payment_events FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS payment_events_read ON accounting.payment_events;
CREATE POLICY payment_events_read ON accounting.payment_events
  FOR SELECT TO accounting_app
  USING (accounting.book_allowed(book_id));
DROP POLICY IF EXISTS payment_events_insert ON accounting.payment_events;
CREATE POLICY payment_events_insert ON accounting.payment_events
  FOR INSERT TO accounting_app
  WITH CHECK (accounting.book_allowed(book_id, true)
    AND actor_user_id = accounting.actor_user());

REVOKE ALL ON accounting.payment_orders, accounting.payment_events
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT, UPDATE ON accounting.payment_orders TO accounting_app;
GRANT SELECT, INSERT ON accounting.payment_events TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.payment_events_append_only() RETURNS trigger AS $$
BEGIN
  RAISE EXCEPTION 'payment events are append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS payment_events_no_change ON accounting.payment_events;
CREATE TRIGGER payment_events_no_change BEFORE UPDATE OR DELETE ON accounting.payment_events
  FOR EACH ROW EXECUTE FUNCTION accounting.payment_events_append_only();

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.payment_orders;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.payment_orders
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
