-- compatibility: breaking
-- N03: immutable notification evidence, restricted native writes and fenced
-- dispatch. No network operation is performed by these database functions.
ALTER TABLE accounting.delivery_notifications
 ADD COLUMN IF NOT EXISTS package_version INTEGER,
 ADD COLUMN IF NOT EXISTS manifest_sha256 TEXT,
 ADD COLUMN IF NOT EXISTS grant_version INTEGER NOT NULL DEFAULT 0,
 ADD COLUMN IF NOT EXISTS restore_epoch TEXT NOT NULL DEFAULT '',
 ADD COLUMN IF NOT EXISTS worker_id TEXT,
 ADD COLUMN IF NOT EXISTS lease_token TEXT,
 ADD COLUMN IF NOT EXISTS lease_until TEXT,
 ADD COLUMN IF NOT EXISTS send_started_at TEXT,
 ADD COLUMN IF NOT EXISTS available_at TEXT NOT NULL DEFAULT '',
 ADD COLUMN IF NOT EXISTS legacy_payload_json TEXT NOT NULL DEFAULT '';
DROP TRIGGER IF EXISTS delivery_notification_guard ON accounting.delivery_notifications;
ALTER TABLE accounting.delivery_notifications DROP CONSTRAINT IF EXISTS delivery_notifications_state_check;
ALTER TABLE accounting.delivery_notifications ADD CONSTRAINT delivery_notifications_state_check
 CHECK(state IN('pending','claimed','sending','unknown','sink_written','failed','cancelled'));
UPDATE accounting.delivery_notifications n SET
 package_version=(SELECT p.version FROM accounting.monthly_delivery_packages p WHERE p.id=n.package_id),
 manifest_sha256=(SELECT p.manifest_sha256 FROM accounting.monthly_delivery_packages p WHERE p.id=n.package_id),
 grant_version=COALESCE((SELECT g.version FROM accounting.monthly_delivery_grants g WHERE g.id=n.grant_id),0),
 legacy_payload_json=payload_json,
 state=CASE WHEN state='failed' OR (state='pending' AND attempts>0) THEN 'unknown'
            WHEN state='pending' THEN 'cancelled' ELSE state END,
 last_error=CASE WHEN state='failed' OR (state='pending' AND attempts>0) THEN 'legacy_delivery_result_unknown'
                 WHEN state='pending' THEN 'legacy_notification_requires_requeue' ELSE last_error END;

CREATE OR REPLACE FUNCTION accounting.n03_restore_epoch() RETURNS TEXT LANGUAGE sql STABLE
 SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT COALESCE((SELECT restore_id FROM accounting.restore_state WHERE instance_id='restored'),'') $$;
CREATE OR REPLACE FUNCTION accounting.n03_review_eligible(target_book TEXT,target_user TEXT,target_firm TEXT)
 RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.companies c ON c.id=b.company_id AND c.status='active'
 JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.users u ON u.id=target_user AND u.active=1
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id
 WHERE b.id=target_book AND b.firm_id=target_firm AND
 (m.role IN('owner','admin') OR (ba.role='reviewer' AND (m.role='reviewer' OR
 (m.role IS NULL AND EXISTS(SELECT 1 FROM accounting.company_memberships cm
 WHERE cm.company_id=b.company_id AND cm.user_id=u.id AND cm.role='finance')))))) $$;

CREATE OR REPLACE FUNCTION accounting.delivery_grant_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD;
BEGIN
 PERFORM accounting.lock_legacy_write();
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id OR b.company_id IS DISTINCT FROM NEW.company_id
 OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NOT accounting.book_review_allowed(NEW.book_id)
 OR accounting.firm_role(NEW.firm_id) NOT IN('owner','admin','reviewer') THEN
  RAISE EXCEPTION 'Current delivery manager and scope required' USING ERRCODE='42501'; END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.periods p WHERE p.book_id=NEW.book_id AND p.period=NEW.period)
 OR NEW.period !~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' OR length(btrim(NEW.reason)) NOT BETWEEN 3 AND 1000 THEN
  RAISE EXCEPTION 'Delivery grant period and reason required' USING ERRCODE='23514'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.granted_by IS DISTINCT FROM accounting.actor_user() OR NEW.state<>'active'
   OR NEW.revoked_by IS NOT NULL OR NEW.revoked_at IS NOT NULL THEN
   RAISE EXCEPTION 'Current grant creator identity required' USING ERRCODE='42501'; END IF;
  IF NEW.version<>(SELECT COALESCE(MAX(version),0)+1 FROM accounting.monthly_delivery_grants WHERE book_id=NEW.book_id AND period=NEW.period) THEN
   RAISE EXCEPTION 'Consecutive grant version required' USING ERRCODE='23514'; END IF;
 ELSE
  IF (NEW.id,NEW.firm_id,NEW.company_id,NEW.book_id,NEW.period,NEW.version,NEW.recipient_user_id,NEW.granted_by,NEW.granted_at)
    IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.book_id,OLD.period,OLD.version,OLD.recipient_user_id,OLD.granted_by,OLD.granted_at)
   OR OLD.state<>'active' OR NEW.state<>'revoked' THEN
   RAISE EXCEPTION 'Delivery grant identity is immutable and revocation terminal' USING ERRCODE='23514'; END IF;
  IF NEW.revoked_by IS DISTINCT FROM accounting.actor_user() OR NULLIF(NEW.revoked_at,'') IS NULL THEN
   RAISE EXCEPTION 'Current revoker identity required' USING ERRCODE='42501'; END IF;
 END IF;
 IF NEW.state='active' AND NOT EXISTS(SELECT 1 FROM accounting.company_memberships cm
 JOIN accounting.users u ON u.id=cm.user_id AND u.active=1
 WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.recipient_user_id) THEN
  RAISE EXCEPTION 'Delivery recipient must be a current company member' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION accounting.n03_notification_current(n accounting.delivery_notifications)
 RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE p RECORD;g RECORD;
BEGIN
 IF n.kind='missing_contact_followup' OR n.restore_epoch IS DISTINCT FROM accounting.n03_restore_epoch()
 OR NOT accounting.n03_review_eligible(n.book_id,n.created_by,n.firm_id)
 OR NOT EXISTS(SELECT 1 FROM accounting.company_memberships cm JOIN accounting.users u ON u.id=cm.user_id AND u.active=1
 WHERE cm.company_id=n.company_id AND cm.user_id=n.recipient_user_id) THEN RETURN false; END IF;
 SELECT * INTO p FROM accounting.monthly_delivery_packages WHERE id=n.package_id;
 IF NOT FOUND OR (p.firm_id,p.company_id,p.book_id,p.period,p.version,p.manifest_sha256)
   IS DISTINCT FROM (n.firm_id,n.company_id,n.book_id,n.period,n.package_version,n.manifest_sha256)
 OR p.version<>(SELECT MAX(version) FROM accounting.monthly_delivery_packages WHERE book_id=n.book_id AND period=n.period)
 OR (p.contact_user_id IS NOT NULL AND p.contact_user_id IS DISTINCT FROM n.recipient_user_id)
 OR EXISTS(SELECT 1 FROM accounting.monthly_delivery_acknowledgements WHERE package_id=p.id)
 OR EXISTS(SELECT 1 FROM accounting.delivery_notifications other WHERE other.id<>n.id
 AND other.package_id=n.package_id AND other.recipient_user_id=n.recipient_user_id
 AND other.state IN('claimed','sending','unknown')) THEN RETURN false; END IF;
 IF n.grant_id IS NULL THEN
  RETURN n.grant_version=0 AND p.contact_user_id=n.recipient_user_id AND NOT EXISTS(
   SELECT 1 FROM accounting.monthly_delivery_grants WHERE book_id=n.book_id AND period=n.period);
 END IF;
 SELECT * INTO g FROM accounting.monthly_delivery_grants WHERE id=n.grant_id;
 RETURN FOUND AND g.state='active' AND
 (g.firm_id,g.company_id,g.book_id,g.period,g.recipient_user_id,g.version)
 IS NOT DISTINCT FROM (n.firm_id,n.company_id,n.book_id,n.period,n.recipient_user_id,n.grant_version);
END $$;

CREATE OR REPLACE FUNCTION accounting.delivery_notification_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE payload JSONB; expected JSONB; worker BOOLEAN; b RECORD;
BEGIN
 PERFORM accounting.lock_legacy_write();
 worker:=current_setting('role',true)='accounting_worker' AND pg_has_role(session_user,'accounting_worker','MEMBER');
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.firm_id,NEW.company_id,NEW.book_id,NEW.period,NEW.package_id,NEW.package_version,NEW.manifest_sha256,
   NEW.kind,NEW.recipient_user_id,NEW.grant_id,NEW.grant_version,NEW.restore_epoch,NEW.dedupe_key,NEW.payload_json,
   NEW.created_by,NEW.created_at,NEW.legacy_payload_json)
   IS DISTINCT FROM
   (OLD.id,OLD.firm_id,OLD.company_id,OLD.book_id,OLD.period,OLD.package_id,OLD.package_version,OLD.manifest_sha256,
   OLD.kind,OLD.recipient_user_id,OLD.grant_id,OLD.grant_version,OLD.restore_epoch,OLD.dedupe_key,OLD.payload_json,
   OLD.created_by,OLD.created_at,OLD.legacy_payload_json) THEN
   RAISE EXCEPTION 'Delivery notification identity is immutable' USING ERRCODE='23514'; END IF;
  IF OLD.state IN('sink_written','cancelled','unknown') THEN
   RAISE EXCEPTION 'Terminal delivery notification cannot restart' USING ERRCODE='23514'; END IF;
  IF NOT worker THEN
   IF NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NOT accounting.book_review_allowed(NEW.book_id) THEN
    RAISE EXCEPTION 'Current notification review permission required' USING ERRCODE='42501'; END IF;
   IF NEW.state<>'cancelled' OR OLD.state NOT IN('pending','failed','claimed')
    OR (NEW.attempts,NEW.sink_ref,NEW.dispatched_at,NEW.send_started_at,NEW.available_at)
       IS DISTINCT FROM (OLD.attempts,OLD.sink_ref,OLD.dispatched_at,OLD.send_started_at,OLD.available_at) THEN
    RAISE EXCEPTION 'Only unsent notification cancellation is allowed' USING ERRCODE='23514'; END IF;
  ELSE
   IF NOT ((OLD.state IN('pending','failed') AND NEW.state IN('claimed','cancelled','unknown'))
     OR (OLD.state='claimed' AND NEW.state IN('sending','pending','cancelled','unknown'))
     OR (OLD.state='sending' AND NEW.state IN('sink_written','failed','unknown'))) THEN
    RAISE EXCEPTION 'Invalid notification state transition' USING ERRCODE='23514'; END IF;
   IF NEW.attempts<>OLD.attempts+(CASE WHEN OLD.state='claimed' AND NEW.state='sending' THEN 1 ELSE 0 END) THEN
    RAISE EXCEPTION 'Notification attempts only advance at send boundary' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF worker OR NEW.created_by IS DISTINCT FROM accounting.actor_user() OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
 OR NOT accounting.book_review_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'Current notification creator and review permission required' USING ERRCODE='42501'; END IF;
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id OR b.company_id IS DISTINCT FROM NEW.company_id
 OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=NEW.book_id AND period=NEW.period)
 OR NEW.period !~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' THEN
  RAISE EXCEPTION 'Notification scope or period differs' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM accounting.restore_state WHERE hold) OR NEW.restore_epoch IS DISTINCT FROM accounting.n03_restore_epoch() THEN
  RAISE EXCEPTION 'Restore hold or epoch prevents notification creation' USING ERRCODE='55000'; END IF;
 IF NEW.state<>'pending' OR NEW.attempts<>0 OR NEW.sink_ref<>'' OR NEW.legacy_payload_json<>''
 OR NEW.worker_id IS NOT NULL OR NEW.lease_token IS NOT NULL OR NEW.lease_until IS NOT NULL
 OR NEW.send_started_at IS NOT NULL OR NEW.dispatched_at IS NOT NULL OR NEW.available_at<>'' THEN
  RAISE EXCEPTION 'Notifications must start as unattempted pending evidence' USING ERRCODE='23514'; END IF;
 BEGIN payload:=NEW.payload_json::jsonb; EXCEPTION WHEN OTHERS THEN
  RAISE EXCEPTION 'Invalid notification payload' USING ERRCODE='23514'; END;
 expected:=jsonb_build_object('notification_id',NEW.id,'firm_id',NEW.firm_id,'company_id',NEW.company_id,
 'book_id',NEW.book_id,'period',NEW.period,'package_id',NEW.package_id,'package_version',NEW.package_version,
 'manifest_sha256',NEW.manifest_sha256,'recipient_user_id',NEW.recipient_user_id,'grant_id',NEW.grant_id,
 'grant_version',NEW.grant_version,'restore_epoch',NEW.restore_epoch,'kind',NEW.kind,'reason',payload->'reason');
 IF payload IS DISTINCT FROM expected OR jsonb_typeof(payload->'reason') IS DISTINCT FROM 'string'
 OR length(btrim(payload->>'reason')) NOT BETWEEN 3 AND 1000 THEN
  RAISE EXCEPTION 'Notification payload differs from frozen scope' USING ERRCODE='23514'; END IF;
 IF NEW.kind='missing_contact_followup' THEN
  IF NEW.package_id IS NOT NULL OR NEW.package_version IS NOT NULL OR NEW.manifest_sha256 IS NOT NULL
   OR NEW.recipient_user_id IS NOT NULL OR NEW.grant_id IS NOT NULL OR NEW.grant_version<>0 THEN
   RAISE EXCEPTION 'Internal followup cannot carry an outbound recipient' USING ERRCODE='23514'; END IF;
 ELSIF NOT accounting.n03_notification_current(NEW) THEN
  RAISE EXCEPTION 'Notification package recipient grant binding is stale or invalid' USING ERRCODE='23514';
 END IF;
 IF NEW.package_id IS NOT NULL AND EXISTS(SELECT 1 FROM accounting.delivery_notifications prior
 WHERE prior.package_id=NEW.package_id AND prior.recipient_user_id=NEW.recipient_user_id
 AND prior.state IN('claimed','sending','unknown')) THEN
  RAISE EXCEPTION 'Prior delivery attempt unresolved; no replacement retry allowed' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER delivery_notification_guard BEFORE INSERT OR UPDATE ON accounting.delivery_notifications
 FOR EACH ROW EXECUTE FUNCTION accounting.delivery_notification_guard();

DROP POLICY IF EXISTS monthly_delivery_grants_insert ON accounting.monthly_delivery_grants;
DROP POLICY IF EXISTS monthly_delivery_grants_update ON accounting.monthly_delivery_grants;
DROP POLICY IF EXISTS delivery_notifications_insert ON accounting.delivery_notifications;
DROP POLICY IF EXISTS delivery_notifications_update ON accounting.delivery_notifications;
CREATE POLICY monthly_delivery_grants_insert ON accounting.monthly_delivery_grants FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id)
 AND accounting.firm_role(firm_id) IN('owner','admin','reviewer'));
CREATE POLICY monthly_delivery_grants_update ON accounting.monthly_delivery_grants FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id)
 AND accounting.firm_role(firm_id) IN('owner','admin','reviewer'))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id)
 AND accounting.firm_role(firm_id) IN('owner','admin','reviewer'));
CREATE POLICY delivery_notifications_insert ON accounting.delivery_notifications FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id));
CREATE POLICY delivery_notifications_update ON accounting.delivery_notifications FOR UPDATE TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id))
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id));

-- Statement gates precede tuple locks even for native SQL callers.
DROP TRIGGER IF EXISTS n03_statement_gate ON accounting.monthly_delivery_grants;
CREATE TRIGGER n03_statement_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.monthly_delivery_grants
 FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();
DROP TRIGGER IF EXISTS n03_statement_gate ON accounting.delivery_notifications;
CREATE TRIGGER n03_statement_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.delivery_notifications
 FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate();

CREATE OR REPLACE FUNCTION accounting.n03_notification_audit(n accounting.delivery_notifications, action_name TEXT)
 RETURNS VOID LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
 VALUES(n.firm_id,n.book_id,n.created_by,'delivery.notification.'||action_name,n.id,
 jsonb_build_object('state',n.state,'attempts',n.attempts,'package_id',n.package_id,'package_version',n.package_version,
 'manifest_sha256',n.manifest_sha256,'grant_id',n.grant_id,'grant_version',n.grant_version,'restore_epoch',n.restore_epoch,
 'lease_sha256',CASE WHEN n.lease_token IS NULL THEN NULL ELSE encode(sha256(convert_to(n.lease_token,'UTF8')),'hex') END,
 'send_started_at',n.send_started_at,'sink_ref',n.sink_ref,'error',n.last_error)::text,n.updated_at) $$;

CREATE OR REPLACE FUNCTION accounting.recover_delivery_notifications() RETURNS INTEGER LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE n accounting.delivery_notifications; changed INTEGER:=0; stamp TEXT; epoch TEXT; held BOOLEAN;
BEGIN
 PERFORM accounting.lock_legacy_write();
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 epoch:=accounting.n03_restore_epoch(); held:=EXISTS(SELECT 1 FROM accounting.restore_state WHERE hold);
 FOR n IN SELECT * FROM accounting.delivery_notifications
 WHERE state IN('pending','failed','claimed','sending') AND (restore_epoch<>epoch
 OR (state IN('claimed','sending') AND (lease_until<=stamp OR held)))
 ORDER BY created_at,id LIMIT 200 FOR UPDATE LOOP
  UPDATE accounting.delivery_notifications SET state=CASE WHEN n.state='sending' THEN 'unknown'
   WHEN n.restore_epoch<>epoch OR held THEN 'cancelled' ELSE 'pending' END,
   last_error=CASE WHEN n.restore_epoch<>epoch THEN 'restored_notification_quarantined'
    WHEN held THEN 'restore_hold_quarantined' ELSE 'delivery_lease_expired' END,
   lease_until=NULL,lease_token=NULL,worker_id=NULL,updated_at=stamp WHERE id=n.id RETURNING * INTO n;
  PERFORM accounting.n03_notification_audit(n,'recovered');changed:=changed+1;
 END LOOP;
 RETURN changed;
END $$;

CREATE OR REPLACE FUNCTION accounting.pending_delivery_notification_count() RETURNS INTEGER LANGUAGE sql STABLE
 SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT count(*)::INTEGER FROM accounting.delivery_notifications WHERE state IN('pending','failed') AND attempts<5
 AND kind<>'missing_contact_followup' $$;

CREATE OR REPLACE FUNCTION accounting.claim_delivery_notification(p_worker_id TEXT,p_lease_seconds INTEGER)
 RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE n accounting.delivery_notifications; stamp TEXT;
BEGIN
 IF p_worker_id IS NULL OR length(p_worker_id) NOT BETWEEN 1 AND 100 OR p_lease_seconds NOT BETWEEN 1 AND 3600 THEN
  RAISE EXCEPTION 'Invalid delivery worker identity or lease' USING ERRCODE='22023'; END IF;
 PERFORM accounting.lock_legacy_write();
 IF EXISTS(SELECT 1 FROM accounting.restore_state WHERE hold) THEN RETURN NULL; END IF;
 PERFORM accounting.recover_delivery_notifications();
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 SELECT * INTO n FROM accounting.delivery_notifications WHERE state IN('pending','failed') AND attempts<5
 AND kind<>'missing_contact_followup' AND available_at<=stamp ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF NOT accounting.n03_notification_current(n) THEN
  UPDATE accounting.delivery_notifications SET state='cancelled',updated_at=stamp,last_error='authorization_or_package_changed'
  WHERE id=n.id RETURNING * INTO n;
  PERFORM accounting.n03_notification_audit(n,'cancelled');RETURN to_jsonb(n);
 END IF;
 UPDATE accounting.delivery_notifications SET state='claimed',worker_id=p_worker_id,
 lease_token=md5(p_worker_id||clock_timestamp()::text||random()::text),
 lease_until=to_char((clock_timestamp()+make_interval(secs=>p_lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),
 updated_at=stamp,last_error='' WHERE id=n.id RETURNING * INTO n;
 PERFORM accounting.n03_notification_audit(n,'claimed');RETURN to_jsonb(n);
END $$;

CREATE OR REPLACE FUNCTION accounting.begin_delivery_notification(p_id TEXT,p_token TEXT,p_lease_seconds INTEGER)
 RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE n accounting.delivery_notifications;stamp TEXT;
BEGIN
 IF p_lease_seconds NOT BETWEEN 1 AND 3600 THEN RAISE EXCEPTION 'Invalid delivery lease' USING ERRCODE='22023'; END IF;
 PERFORM accounting.lock_legacy_write();
 IF EXISTS(SELECT 1 FROM accounting.restore_state WHERE hold) THEN RETURN NULL; END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 SELECT * INTO n FROM accounting.delivery_notifications WHERE id=p_id AND state='claimed'
 AND lease_token=p_token AND lease_until>stamp FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF NOT accounting.n03_notification_current(n) THEN
  UPDATE accounting.delivery_notifications SET state='cancelled',updated_at=stamp,last_error='authorization_or_package_changed',
  lease_token=NULL,lease_until=NULL,worker_id=NULL WHERE id=n.id RETURNING * INTO n;
  PERFORM accounting.n03_notification_audit(n,'cancelled');RETURN NULL;
 END IF;
 UPDATE accounting.delivery_notifications SET state='sending',attempts=attempts+1,send_started_at=stamp,updated_at=stamp,
 lease_until=to_char((clock_timestamp()+make_interval(secs=>p_lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
 WHERE id=n.id RETURNING * INTO n;
 PERFORM accounting.n03_notification_audit(n,'sending');RETURN to_jsonb(n);
END $$;

CREATE OR REPLACE FUNCTION accounting.finish_delivery_notification(p_id TEXT,p_token TEXT,p_outcome TEXT,p_sink_ref TEXT,p_error TEXT)
 RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE n accounting.delivery_notifications;stamp TEXT;
BEGIN
 IF p_outcome NOT IN('sink_written','failed','unknown') OR length(COALESCE(p_sink_ref,''))>1000
 OR length(COALESCE(p_error,''))>1000 OR (p_outcome='sink_written' AND NULLIF(p_sink_ref,'') IS NULL) THEN
  RAISE EXCEPTION 'Invalid notification settlement' USING ERRCODE='22023'; END IF;
 PERFORM accounting.lock_legacy_write();
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 UPDATE accounting.delivery_notifications SET state=p_outcome,sink_ref=COALESCE(p_sink_ref,''),last_error=COALESCE(p_error,''),
 dispatched_at=CASE WHEN p_outcome='sink_written' THEN stamp ELSE NULL END,
 available_at=CASE WHEN p_outcome='failed' THEN to_char((clock_timestamp()+interval '30 seconds') AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') ELSE available_at END,
 updated_at=stamp,worker_id=NULL,lease_token=NULL,lease_until=NULL
 WHERE id=p_id AND state='sending' AND lease_token=p_token AND lease_until>stamp
 AND restore_epoch=accounting.n03_restore_epoch() RETURNING * INTO n;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM accounting.n03_notification_audit(n,'settled');RETURN true;
END $$;

REVOKE ALL ON FUNCTION accounting.n03_restore_epoch(),accounting.n03_review_eligible(TEXT,TEXT,TEXT),
 accounting.n03_notification_current(accounting.delivery_notifications),
 accounting.n03_notification_audit(accounting.delivery_notifications,TEXT),accounting.delivery_grant_guard(),
 accounting.delivery_notification_guard() FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
REVOKE ALL ON FUNCTION accounting.claim_delivery_notification(TEXT,INTEGER),accounting.begin_delivery_notification(TEXT,TEXT,INTEGER),
 accounting.finish_delivery_notification(TEXT,TEXT,TEXT,TEXT,TEXT),accounting.recover_delivery_notifications(),
 accounting.pending_delivery_notification_count() FROM PUBLIC,accounting_app,accounting_worker,accounting_system,accounting_projection;
GRANT EXECUTE ON FUNCTION accounting.claim_delivery_notification(TEXT,INTEGER),accounting.begin_delivery_notification(TEXT,TEXT,INTEGER),
 accounting.finish_delivery_notification(TEXT,TEXT,TEXT,TEXT,TEXT),accounting.recover_delivery_notifications(),
 accounting.pending_delivery_notification_count() TO accounting_worker;
REVOKE ALL ON accounting.delivery_notifications,accounting.monthly_delivery_grants FROM accounting_worker,accounting_system,accounting_projection;
