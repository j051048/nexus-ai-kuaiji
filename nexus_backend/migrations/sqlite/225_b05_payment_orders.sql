-- compatibility: incremental
CREATE TABLE payment_orders(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  book_id TEXT NOT NULL REFERENCES books(id),
  period TEXT NOT NULL,
  payee TEXT NOT NULL,
  payee_account TEXT NOT NULL,
  amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
  idempotency_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN(
    'draft','pending_approval','approved','queued_for_bank','submitted',
    'succeeded','failed','bounced','bank_unknown')),
  maker_user_id TEXT NOT NULL REFERENCES users(id),
  approver_user_id TEXT REFERENCES users(id),
  reviewer_user_id TEXT REFERENCES users(id),
  escalated INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(book_id, idempotency_key),
  CHECK(approver_user_id IS NULL OR approver_user_id <> maker_user_id),
  CHECK(reviewer_user_id IS NULL OR (reviewer_user_id <> maker_user_id
        AND (approver_user_id IS NULL OR reviewer_user_id <> approver_user_id))),
  CHECK(status NOT IN('failed','bounced') OR reason <> '')
);
CREATE TABLE payment_events(
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES payment_orders(id),
  book_id TEXT NOT NULL,
  from_status TEXT NOT NULL,
  to_status TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  actor_user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL
);
CREATE TRIGGER payment_events_append_only_update BEFORE UPDATE ON payment_events
BEGIN SELECT RAISE(ABORT, 'payment events are append-only'); END;
CREATE TRIGGER payment_events_append_only_delete BEFORE DELETE ON payment_events
BEGIN SELECT RAISE(ABORT, 'payment events are append-only'); END;
