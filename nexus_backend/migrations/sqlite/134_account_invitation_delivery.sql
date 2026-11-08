-- compatibility: incremental
CREATE TABLE IF NOT EXISTS account_invitation_deliveries(
 id TEXT PRIMARY KEY,request_id TEXT NOT NULL REFERENCES admin_account_requests(id),
 firm_id TEXT NOT NULL,company_id TEXT,request_attempt INTEGER NOT NULL CHECK(request_attempt>0),
 activation_digest TEXT NOT NULL CHECK(length(activation_digest)=64),
 recipient_email TEXT NOT NULL,idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN('sending','accepted','not_sent','unknown')),
 finished_at TEXT NOT NULL DEFAULT '',channel TEXT NOT NULL,UNIQUE(request_id,idempotency_key)
);
CREATE TRIGGER IF NOT EXISTS invitation_delivery_insert BEFORE INSERT ON account_invitation_deliveries BEGIN
 SELECT CASE WHEN NEW.created_by<>accounting_actor_user() OR NEW.state<>'sending' OR NEW.finished_at<>'' OR NOT EXISTS(
  SELECT 1 FROM admin_account_requests r WHERE r.id=NEW.request_id AND r.firm_id=NEW.firm_id AND r.company_id IS NEW.company_id
  AND r.provisioning_mode='activation' AND r.status='awaiting_activation' AND r.expires_at>unixepoch() AND r.attempts=NEW.request_attempt AND r.email=NEW.recipient_email
  AND EXISTS(SELECT 1 FROM audit_events a WHERE a.object_id=r.id AND a.action='admin.account.link_created' AND json_extract(a.details,'$.activation_digest')=NEW.activation_digest AND json_extract(a.details,'$.request_attempt')=r.attempts)
 ) OR EXISTS(SELECT 1 FROM account_invitation_deliveries d WHERE d.request_id=NEW.request_id AND d.request_attempt=NEW.request_attempt AND d.state<>'not_sent')
 OR (SELECT count(*) FROM account_invitation_deliveries d WHERE d.request_id=NEW.request_id AND d.request_attempt=NEW.request_attempt)>=3
 THEN RAISE(ABORT,'Invalid or already attempted invitation') END;
END;
CREATE TRIGGER IF NOT EXISTS invitation_delivery_update BEFORE UPDATE ON account_invitation_deliveries BEGIN
 SELECT CASE WHEN OLD.state<>'sending' OR NEW.state NOT IN('accepted','not_sent','unknown') OR NEW.finished_at=''
 OR NEW.id<>OLD.id OR NEW.request_id<>OLD.request_id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.request_attempt<>OLD.request_attempt OR NEW.activation_digest<>OLD.activation_digest OR NEW.recipient_email<>OLD.recipient_email
 OR NEW.idempotency_key<>OLD.idempotency_key OR NEW.created_by<>OLD.created_by OR NEW.created_by<>accounting_actor_user()
 OR NEW.created_at<>OLD.created_at OR NEW.channel<>OLD.channel
 THEN RAISE(ABORT,'Invitation result is fenced and immutable') END;
END;
CREATE TRIGGER IF NOT EXISTS invitation_delivery_delete BEFORE DELETE ON account_invitation_deliveries BEGIN
 SELECT RAISE(ABORT,'Invitation evidence cannot be deleted');
END;
