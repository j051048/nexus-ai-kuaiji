-- compatibility: breaking
-- N03: immutable notification evidence, bounded leases and unknown outcomes.
-- SQLite has no database login/RLS context: actor capabilities remain service
-- checks. These triggers enforce structural scope, source and state invariants.
DROP TRIGGER IF EXISTS delivery_notification_guard_upd;
DROP TRIGGER IF EXISTS delivery_grant_guard_ins;
DROP TRIGGER IF EXISTS delivery_grant_guard_upd;

CREATE TABLE IF NOT EXISTS delivery_notifications_n03(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 period TEXT NOT NULL,
 package_id TEXT,
 package_version INTEGER,
 manifest_sha256 TEXT,
 kind TEXT NOT NULL CHECK(kind IN('signature_reminder','missing_contact_followup','signature_overdue')),
 recipient_user_id TEXT REFERENCES users(id),
 grant_id TEXT REFERENCES monthly_delivery_grants(id),
 grant_version INTEGER NOT NULL DEFAULT 0 CHECK(grant_version>=0),
 dedupe_key TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('pending','claimed','sending','unknown','sink_written','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 last_error TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL,
 legacy_payload_json TEXT NOT NULL DEFAULT '',
 sink_ref TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 dispatched_at TEXT,
 restore_epoch TEXT NOT NULL DEFAULT '',
 worker_id TEXT,
 lease_token TEXT,
 lease_until TEXT,
 send_started_at TEXT,
 available_at TEXT NOT NULL DEFAULT ''
);

-- Preserve every historical row and its original payload. Anything that may
-- already have reached an external sink is quarantined, never made retryable.
INSERT INTO delivery_notifications_n03(
 id,firm_id,company_id,book_id,period,package_id,package_version,manifest_sha256,
 kind,recipient_user_id,grant_id,grant_version,dedupe_key,state,attempts,last_error,
 payload_json,legacy_payload_json,sink_ref,created_by,created_at,updated_at,dispatched_at)
SELECT n.id,n.firm_id,n.company_id,n.book_id,n.period,n.package_id,p.version,p.manifest_sha256,
 n.kind,n.recipient_user_id,n.grant_id,COALESCE(g.version,0),n.dedupe_key,
 CASE WHEN n.state='failed' OR (n.state='pending' AND n.attempts>0) THEN 'unknown'
      WHEN n.state='pending' THEN 'cancelled' ELSE n.state END,
 n.attempts,
 CASE WHEN n.state='failed' OR (n.state='pending' AND n.attempts>0)
      THEN 'legacy_outcome_unknown'
      WHEN n.state='pending' THEN 'legacy_notification_requires_requeue' ELSE n.last_error END,
 n.payload_json,n.payload_json,n.sink_ref,n.created_by,n.created_at,n.updated_at,n.dispatched_at
FROM delivery_notifications n
LEFT JOIN monthly_delivery_packages p ON p.id=n.package_id
LEFT JOIN monthly_delivery_grants g ON g.id=n.grant_id;
DROP TABLE delivery_notifications;
ALTER TABLE delivery_notifications_n03 RENAME TO delivery_notifications;
CREATE UNIQUE INDEX IF NOT EXISTS ux_delivery_notifications_dedupe ON delivery_notifications(dedupe_key);
CREATE INDEX IF NOT EXISTS idx_delivery_notifications_pending ON delivery_notifications(state,available_at,created_at);

CREATE TRIGGER IF NOT EXISTS delivery_grant_guard_ins BEFORE INSERT ON monthly_delivery_grants
BEGIN
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=NEW.period
   WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
  THEN RAISE(ABORT,'Delivery grant must match firm/company/book/period') END;
 SELECT CASE WHEN NEW.state<>'active' OR NEW.revoked_by IS NOT NULL OR NEW.revoked_at IS NOT NULL
  OR NEW.version<>COALESCE((SELECT MAX(g.version)+1 FROM monthly_delivery_grants g
                            WHERE g.book_id=NEW.book_id AND g.period=NEW.period),1)
  OR length(trim(NEW.reason))<3
  THEN RAISE(ABORT,'Delivery grant must start as the next active version') END;
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id
   WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.recipient_user_id AND u.active=1)
  THEN RAISE(ABORT,'Delivery recipient must be an active company member') END;
END;

CREATE TRIGGER IF NOT EXISTS delivery_grant_guard_upd BEFORE UPDATE ON monthly_delivery_grants
BEGIN
 SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id
   OR NEW.company_id IS NOT OLD.company_id OR NEW.book_id IS NOT OLD.book_id
   OR NEW.period IS NOT OLD.period OR NEW.version IS NOT OLD.version
   OR NEW.recipient_user_id IS NOT OLD.recipient_user_id
   OR NEW.granted_by IS NOT OLD.granted_by OR NEW.granted_at IS NOT OLD.granted_at
  THEN RAISE(ABORT,'Delivery grant identity is immutable') END;
 SELECT CASE WHEN OLD.state<>'active' OR NEW.state<>'revoked'
   OR NEW.revoked_by IS NULL OR NEW.revoked_at IS NULL OR length(trim(NEW.reason))<3
  THEN RAISE(ABORT,'Delivery grant may only be revoked once') END;
END;

CREATE TRIGGER IF NOT EXISTS delivery_notification_guard_ins BEFORE INSERT ON delivery_notifications
BEGIN
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=NEW.period
   WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
  THEN RAISE(ABORT,'Notification must match firm/company/book/period') END;
 SELECT CASE WHEN NEW.state<>'pending' OR NEW.attempts<>0 OR NEW.worker_id IS NOT NULL
   OR NEW.lease_token IS NOT NULL OR NEW.lease_until IS NOT NULL
   OR NEW.send_started_at IS NOT NULL OR NEW.dispatched_at IS NOT NULL
   OR NEW.sink_ref<>'' OR NEW.legacy_payload_json<>''
  THEN RAISE(ABORT,'Notification must start pending and unsent') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM restore_state WHERE hold<>0)
   OR NEW.restore_epoch IS NOT COALESCE((SELECT restore_id FROM restore_state WHERE instance_id='restored'),'')
  THEN RAISE(ABORT,'Notification is bound to the current released restore epoch') END;
 SELECT CASE WHEN NEW.package_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM monthly_delivery_packages p WHERE p.id=NEW.package_id
   AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND p.book_id=NEW.book_id
   AND p.period=NEW.period AND p.version=NEW.package_version AND p.manifest_sha256=NEW.manifest_sha256)
  THEN RAISE(ABORT,'Notification package version and manifest must match') END;
 SELECT CASE WHEN NEW.package_id IS NULL AND (NEW.package_version IS NOT NULL OR NEW.manifest_sha256 IS NOT NULL)
  THEN RAISE(ABORT,'Missing package cannot carry a version or manifest') END;
 SELECT CASE WHEN NEW.grant_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM monthly_delivery_grants g WHERE g.id=NEW.grant_id
   AND g.firm_id=NEW.firm_id AND g.company_id=NEW.company_id AND g.book_id=NEW.book_id
   AND g.period=NEW.period AND g.version=NEW.grant_version AND g.state='active'
   AND g.recipient_user_id IS NEW.recipient_user_id)
  THEN RAISE(ABORT,'Notification grant must match current source and recipient') END;
 SELECT CASE WHEN NEW.grant_id IS NULL AND (NEW.grant_version<>0 OR EXISTS(
   SELECT 1 FROM monthly_delivery_grants g WHERE g.book_id=NEW.book_id AND g.period=NEW.period))
  THEN RAISE(ABORT,'Legacy notification cannot bypass grant history') END;
 SELECT CASE WHEN NEW.kind IN('signature_reminder','signature_overdue') AND (
   NEW.package_id IS NULL OR NEW.recipient_user_id IS NULL OR NOT EXISTS(
     SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id
     WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.recipient_user_id AND u.active=1)
   OR NOT EXISTS(SELECT 1 FROM monthly_delivery_packages p WHERE p.id=NEW.package_id
      AND (p.contact_user_id=NEW.recipient_user_id OR (p.contact_user_id IS NULL AND NEW.grant_id IS NOT NULL)))
   OR EXISTS(SELECT 1 FROM monthly_delivery_acknowledgements a WHERE a.package_id=NEW.package_id))
  THEN RAISE(ABORT,'Reminder must bind an unsigned package and its eligible recipient') END;
 SELECT CASE WHEN json_valid(NEW.payload_json)<>1
  THEN RAISE(ABORT,'Notification payload must be valid JSON') END;
 SELECT CASE WHEN json_type(NEW.payload_json)<>'object'
   OR (SELECT COUNT(*) FROM json_each(NEW.payload_json))<>14
   OR (SELECT COUNT(DISTINCT key) FROM json_each(NEW.payload_json))<>14
   OR json_extract(NEW.payload_json,'$.notification_id') IS NOT NEW.id
   OR json_extract(NEW.payload_json,'$.firm_id') IS NOT NEW.firm_id
   OR json_extract(NEW.payload_json,'$.company_id') IS NOT NEW.company_id
   OR json_extract(NEW.payload_json,'$.book_id') IS NOT NEW.book_id
   OR json_extract(NEW.payload_json,'$.period') IS NOT NEW.period
   OR json_extract(NEW.payload_json,'$.package_id') IS NOT NEW.package_id
   OR json_extract(NEW.payload_json,'$.package_version') IS NOT NEW.package_version
   OR json_extract(NEW.payload_json,'$.manifest_sha256') IS NOT NEW.manifest_sha256
   OR json_extract(NEW.payload_json,'$.recipient_user_id') IS NOT NEW.recipient_user_id
   OR json_extract(NEW.payload_json,'$.grant_id') IS NOT NEW.grant_id
   OR json_extract(NEW.payload_json,'$.grant_version') IS NOT NEW.grant_version
   OR json_type(NEW.payload_json,'$.grant_version')<>'integer'
   OR (NEW.package_id IS NOT NULL AND json_type(NEW.payload_json,'$.package_version')<>'integer')
   OR json_extract(NEW.payload_json,'$.restore_epoch') IS NOT NEW.restore_epoch
   OR json_extract(NEW.payload_json,'$.kind') IS NOT NEW.kind
   OR json_type(NEW.payload_json,'$.reason')<>'text'
   OR length(trim(json_extract(NEW.payload_json,'$.reason')))<3
  THEN RAISE(ABORT,'Notification payload must exactly bind immutable columns') END;
END;

CREATE TRIGGER IF NOT EXISTS delivery_notification_guard_upd BEFORE UPDATE ON delivery_notifications
BEGIN
 SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id
   OR NEW.company_id IS NOT OLD.company_id OR NEW.book_id IS NOT OLD.book_id
   OR NEW.period IS NOT OLD.period OR NEW.package_id IS NOT OLD.package_id
   OR NEW.package_version IS NOT OLD.package_version OR NEW.manifest_sha256 IS NOT OLD.manifest_sha256
   OR NEW.kind IS NOT OLD.kind OR NEW.recipient_user_id IS NOT OLD.recipient_user_id
   OR NEW.grant_id IS NOT OLD.grant_id OR NEW.grant_version IS NOT OLD.grant_version
   OR NEW.restore_epoch IS NOT OLD.restore_epoch OR NEW.dedupe_key IS NOT OLD.dedupe_key
   OR NEW.payload_json IS NOT OLD.payload_json OR NEW.legacy_payload_json IS NOT OLD.legacy_payload_json
   OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
  THEN RAISE(ABORT,'Notification identity and frozen evidence are immutable') END;
 SELECT CASE WHEN NOT (
   (OLD.state='pending' AND NEW.state IN('claimed','cancelled'))
   OR (OLD.state='claimed' AND NEW.state IN('pending','sending','cancelled'))
   OR (OLD.state='sending' AND NEW.state IN('sink_written','unknown','failed'))
   OR (OLD.state='failed' AND NEW.state IN('claimed','cancelled')))
  THEN RAISE(ABORT,'Notification state transition is not permitted') END;
 SELECT CASE WHEN NEW.attempts<>OLD.attempts+CASE WHEN NEW.state='sending' THEN 1 ELSE 0 END
  THEN RAISE(ABORT,'Only entering transport increments notification attempts') END;
 SELECT CASE WHEN NEW.state IN('claimed','sending') AND (
   NEW.worker_id IS NULL OR NEW.lease_token IS NULL OR NEW.lease_until IS NULL)
  THEN RAISE(ABORT,'Claimed notification requires a bounded lease') END;
 SELECT CASE WHEN OLD.state='claimed' AND NEW.state='sending' AND (
   NEW.lease_token IS NOT OLD.lease_token OR NEW.worker_id IS NOT OLD.worker_id
   OR NEW.send_started_at IS NULL)
  THEN RAISE(ABORT,'Transport must retain the claimed notification fence') END;
 SELECT CASE WHEN OLD.state='sending' AND (
   NEW.lease_token IS NOT OLD.lease_token OR NEW.worker_id IS NOT OLD.worker_id
   OR NEW.send_started_at IS NOT OLD.send_started_at)
  THEN RAISE(ABORT,'Settlement must retain transport identity') END;
END;
