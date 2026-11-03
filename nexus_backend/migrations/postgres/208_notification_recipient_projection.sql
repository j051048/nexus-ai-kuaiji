-- compatibility: incremental
-- Address projection is ephemeral and bound to the current notification lease.
-- Workers retain no SELECT privilege on users. Historical migrations stay intact.
CREATE OR REPLACE FUNCTION accounting.begin_delivery_notification(p_id TEXT,p_token TEXT,p_lease_seconds INTEGER)
 RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE n accounting.delivery_notifications;stamp TEXT;recipient_email TEXT;
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
 SELECT btrim(COALESCE(u.email,'')) INTO recipient_email FROM accounting.users u WHERE u.id=n.recipient_user_id;
 UPDATE accounting.delivery_notifications SET state='sending',attempts=attempts+1,send_started_at=stamp,updated_at=stamp,
 lease_until=to_char((clock_timestamp()+make_interval(secs=>p_lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"')
 WHERE id=n.id RETURNING * INTO n;
 PERFORM accounting.n03_notification_audit(n,'sending');
 RETURN to_jsonb(n)||jsonb_build_object('recipient_email',COALESCE(recipient_email,''));
END $$;
REVOKE ALL ON FUNCTION accounting.begin_delivery_notification(TEXT,TEXT,INTEGER) FROM PUBLIC,accounting_app,accounting_system;
GRANT EXECUTE ON FUNCTION accounting.begin_delivery_notification(TEXT,TEXT,INTEGER) TO accounting_worker;
