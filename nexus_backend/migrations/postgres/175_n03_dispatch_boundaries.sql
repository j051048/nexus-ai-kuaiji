-- compatibility: breaking
-- Preserve attempt evidence and enforce bounded, null-safe worker inputs.
CREATE OR REPLACE FUNCTION accounting.n03_notification_audit(n accounting.delivery_notifications, action_name TEXT)
 RETURNS VOID LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
 VALUES(n.firm_id,n.book_id,n.created_by,'delivery.notification.'||action_name,n.id,
 jsonb_build_object('state',n.state,'attempts',n.attempts,'package_id',n.package_id,'package_version',n.package_version,
 'manifest_sha256',n.manifest_sha256,'grant_id',n.grant_id,'grant_version',n.grant_version,'restore_epoch',n.restore_epoch,
 'executor_kind','worker','initiated_by',n.created_by,'worker_id',n.worker_id,'lease_sha256',CASE WHEN n.lease_token IS NULL THEN NULL ELSE encode(sha256(convert_to(n.lease_token,'UTF8')),'hex') END,
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
 ORDER BY created_at,id LIMIT 100 FOR UPDATE LOOP
  UPDATE accounting.delivery_notifications SET state=CASE WHEN n.state='sending' THEN 'unknown'
   WHEN n.restore_epoch<>epoch OR held THEN 'cancelled' ELSE 'pending' END,
   last_error=CASE WHEN n.restore_epoch<>epoch THEN 'restored_notification_quarantined'
    WHEN held THEN 'restore_hold_quarantined' ELSE 'delivery_lease_expired' END,
   lease_until=NULL,updated_at=stamp WHERE id=n.id RETURNING * INTO n;
  PERFORM accounting.n03_notification_audit(n,'recovered');changed:=changed+1;
 END LOOP;
 RETURN changed;
END $$;

CREATE OR REPLACE FUNCTION accounting.claim_delivery_notification(p_worker_id TEXT,p_lease_seconds INTEGER)
 RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE n accounting.delivery_notifications; stamp TEXT;
BEGIN
 IF p_worker_id IS NULL OR length(btrim(p_worker_id)) NOT BETWEEN 1 AND 100 OR p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 600 THEN
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
 IF p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 600 THEN RAISE EXCEPTION 'Invalid delivery lease' USING ERRCODE='22023'; END IF;
 PERFORM accounting.lock_legacy_write();
 IF EXISTS(SELECT 1 FROM accounting.restore_state WHERE hold) THEN RETURN NULL; END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 SELECT * INTO n FROM accounting.delivery_notifications WHERE id=p_id AND state='claimed'
 AND lease_token=p_token AND lease_until>stamp FOR UPDATE;
 IF NOT FOUND THEN RETURN NULL; END IF;
 IF NOT accounting.n03_notification_current(n) THEN
  UPDATE accounting.delivery_notifications SET state='cancelled',updated_at=stamp,last_error='authorization_or_package_changed',
  lease_until=NULL WHERE id=n.id RETURNING * INTO n;
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
 IF p_outcome IS NULL OR p_sink_ref IS NULL OR p_error IS NULL
 OR p_outcome NOT IN('sink_written','failed','unknown') OR length(p_sink_ref)>500
 OR length(p_error)>200 OR (p_outcome='sink_written' AND NULLIF(p_sink_ref,'') IS NULL) THEN
  RAISE EXCEPTION 'Invalid notification settlement' USING ERRCODE='22023'; END IF;
 PERFORM accounting.lock_legacy_write();
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 UPDATE accounting.delivery_notifications SET state=p_outcome,sink_ref=COALESCE(p_sink_ref,''),last_error=COALESCE(p_error,''),
 dispatched_at=CASE WHEN p_outcome='sink_written' THEN stamp ELSE NULL END,
 available_at=CASE WHEN p_outcome='failed' THEN to_char((clock_timestamp()+interval '30 seconds') AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') ELSE available_at END,
 updated_at=stamp,lease_until=NULL
 WHERE id=p_id AND state='sending' AND lease_token=p_token AND lease_until>stamp
 AND restore_epoch=accounting.n03_restore_epoch() RETURNING * INTO n;
 IF NOT FOUND THEN RETURN false; END IF;
 PERFORM accounting.n03_notification_audit(n,'settled');RETURN true;
END $$;
