-- compatibility: incremental
-- Mirrors postgres/168_monthly_delivery_followups.sql for the local demo engine.
-- SQLite has no RLS; the service still enforces book access, eligibility and
-- immutability so the same commands behave the same way in both engines.
CREATE TABLE IF NOT EXISTS monthly_delivery_grants(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 period TEXT NOT NULL,
 recipient_user_id TEXT NOT NULL REFERENCES users(id),
 version INTEGER NOT NULL CHECK(version>0),
 state TEXT NOT NULL CHECK(state IN('active','revoked')),
 granted_by TEXT NOT NULL REFERENCES users(id),
 granted_at TEXT NOT NULL,
 revoked_by TEXT REFERENCES users(id),
 revoked_at TEXT,
 reason TEXT NOT NULL,
 UNIQUE(book_id,period,version)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_monthly_delivery_grants_active
 ON monthly_delivery_grants(book_id,period) WHERE state='active';

CREATE TABLE IF NOT EXISTS delivery_notifications(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 period TEXT NOT NULL,
 package_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN('signature_reminder','missing_contact_followup','signature_overdue')),
 recipient_user_id TEXT REFERENCES users(id),
 grant_id TEXT REFERENCES monthly_delivery_grants(id),
 dedupe_key TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('pending','sink_written','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 last_error TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL,
 sink_ref TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 dispatched_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_delivery_notifications_dedupe
 ON delivery_notifications(dedupe_key);

CREATE TRIGGER IF NOT EXISTS delivery_grant_guard_ins BEFORE INSERT ON monthly_delivery_grants
BEGIN
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM books b WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
  THEN RAISE(ABORT,'Delivery grant must match the current firm and book') END;
 SELECT CASE WHEN NEW.state='active' AND NOT EXISTS(
   SELECT 1 FROM company_memberships cm WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.recipient_user_id)
  THEN RAISE(ABORT,'Delivery recipient must be a current member of that company') END;
END;

CREATE TRIGGER IF NOT EXISTS delivery_grant_guard_upd BEFORE UPDATE ON monthly_delivery_grants
BEGIN
 SELECT CASE WHEN NEW.book_id<>OLD.book_id OR NEW.period<>OLD.period
   OR NEW.version<>OLD.version OR NEW.recipient_user_id<>OLD.recipient_user_id
  THEN RAISE(ABORT,'Delivery grant identity is immutable; issue a new version') END;
END;

CREATE TRIGGER IF NOT EXISTS delivery_notification_guard_upd BEFORE UPDATE ON delivery_notifications
BEGIN
 SELECT CASE WHEN OLD.state='cancelled' THEN RAISE(ABORT,'Cancelled delivery notification is terminal') END;
 SELECT CASE WHEN NEW.dedupe_key<>OLD.dedupe_key OR NEW.book_id<>OLD.book_id
   OR NEW.period<>OLD.period OR NEW.kind<>OLD.kind
  THEN RAISE(ABORT,'Delivery notification identity is immutable') END;
END;
