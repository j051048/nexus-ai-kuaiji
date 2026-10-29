-- compatibility: breaking
-- A blank receipt cannot certify a completed local send.
CREATE OR REPLACE FUNCTION accounting.finish_delivery_notification(p_id TEXT,p_token TEXT,p_outcome TEXT,p_sink_ref TEXT,p_error TEXT)
 RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE n accounting.delivery_notifications;stamp TEXT;
BEGIN
 IF p_outcome IS NULL OR p_sink_ref IS NULL OR p_error IS NULL
 OR p_outcome NOT IN('sink_written','failed','unknown') OR length(p_sink_ref)>500
 OR length(p_error)>200 OR (p_outcome='sink_written' AND NULLIF(btrim(p_sink_ref),'') IS NULL) THEN
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
