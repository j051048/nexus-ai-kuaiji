CREATE OR REPLACE FUNCTION accounting.worker_renew_batch_item(
 p_item_id TEXT,p_lease_token TEXT,p_lease_seconds INTEGER
) RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE stamp TEXT; lease TEXT;
BEGIN
 IF p_lease_seconds IS NULL OR p_lease_seconds NOT BETWEEN 1 AND 3600 THEN
  RAISE EXCEPTION 'Batch lease renewal is invalid' USING ERRCODE='22023';
 END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 lease:=to_char((clock_timestamp()+make_interval(secs=>p_lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 UPDATE accounting.batch_operation_items SET lease_until=GREATEST(lease_until,lease),updated_at=stamp
 WHERE id=p_item_id AND lease_token=p_lease_token AND status='running' AND lease_until>stamp;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION accounting.worker_renew_batch_item(TEXT,TEXT,INTEGER) FROM PUBLIC,accounting_app;
GRANT EXECUTE ON FUNCTION accounting.worker_renew_batch_item(TEXT,TEXT,INTEGER) TO accounting_worker;
