-- compatibility: incremental
CREATE TABLE IF NOT EXISTS notification_investigations(
 id TEXT PRIMARY KEY,
 notification_id TEXT NOT NULL REFERENCES delivery_notifications(id),
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 period TEXT NOT NULL,
 package_id TEXT NOT NULL REFERENCES monthly_delivery_packages(id),
 package_version INTEGER NOT NULL CHECK(package_version>0),
 manifest_sha256 TEXT NOT NULL CHECK(length(manifest_sha256)=64),
 recipient_user_id TEXT NOT NULL REFERENCES users(id),
 attempts INTEGER NOT NULL CHECK(attempts>=0),
 revision INTEGER NOT NULL CHECK(revision>0),
 conclusion TEXT NOT NULL CHECK(conclusion IN('accepted_by_channel','not_sent','unresolved')),
 evidence_ref TEXT NOT NULL CHECK(length(trim(evidence_ref)) BETWEEN 3 AND 1000),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 UNIQUE(notification_id,revision), UNIQUE(notification_id,idempotency_key)
);
CREATE INDEX IF NOT EXISTS notification_investigation_scope ON notification_investigations(firm_id,book_id,notification_id,revision);

CREATE TRIGGER IF NOT EXISTS notification_investigation_insert BEFORE INSERT ON notification_investigations BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM delivery_notifications n WHERE n.id=NEW.notification_id
 AND n.firm_id=NEW.firm_id AND n.company_id=NEW.company_id AND n.book_id=NEW.book_id AND n.period=NEW.period
 AND n.package_id=NEW.package_id AND n.package_version=NEW.package_version AND n.manifest_sha256=NEW.manifest_sha256
 AND n.recipient_user_id=NEW.recipient_user_id AND n.attempts=NEW.attempts AND n.state='unknown')
 OR NEW.revision<>(SELECT COALESCE(MAX(revision),0)+1 FROM notification_investigations WHERE notification_id=NEW.notification_id)
 OR EXISTS(SELECT 1 FROM restore_state WHERE hold<>0 OR hold IS NULL)
 THEN RAISE(ABORT,'Unknown notification identity/revision or restore hold') END;
END;
CREATE TRIGGER IF NOT EXISTS notification_investigation_update BEFORE UPDATE ON notification_investigations BEGIN
 SELECT RAISE(ABORT,'Investigation history is append only'); END;
CREATE TRIGGER IF NOT EXISTS notification_investigation_delete BEFORE DELETE ON notification_investigations BEGIN
 SELECT RAISE(ABORT,'Investigation history is append only'); END;
