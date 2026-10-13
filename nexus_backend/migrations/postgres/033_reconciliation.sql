CREATE TABLE accounting.reconciliation_receipts(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES accounting.firms(id),created_by TEXT NOT NULL REFERENCES accounting.users(id),
 idempotency_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,
 request_json TEXT NOT NULL CHECK(jsonb_typeof(request_json::jsonb)='object'),reason TEXT NOT NULL CHECK(length(btrim(reason))>0),created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key)
);
CREATE INDEX idx_sources_reconciliation_candidate ON accounting.sources(book_id,source_kind,direction,counterparty,date,amount_cents,id);
CREATE INDEX idx_reconciliation_receipts_owner ON accounting.reconciliation_receipts(firm_id,created_by,created_at,id);
ALTER TABLE accounting.reconciliation_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.reconciliation_receipts FORCE ROW LEVEL SECURITY;
CREATE TRIGGER reconciliation_receipt_update BEFORE UPDATE OR DELETE ON accounting.reconciliation_receipts FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
REVOKE ALL ON accounting.reconciliation_receipts FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT ON accounting.reconciliation_receipts TO accounting_app;
CREATE FUNCTION accounting.reconciliation_receipt_visible(target_payload TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(target_payload::jsonb->'items') i WHERE
  NOT accounting.book_allowed(i->>'book_id',true) OR NOT EXISTS(SELECT 1 FROM accounting.business_mappings m
   WHERE m.event_id=i->>'event_id' AND m.book_id=i->>'book_id' AND m.firm_id=accounting.actor_firm()
    AND accounting.business_event_visible(m.event_id,m.firm_id,m.company_id)))
$$;
CREATE POLICY reconciliation_receipt_read ON accounting.reconciliation_receipts FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.reconciliation_receipt_visible(request_json));
CREATE FUNCTION accounting.confirm_reconciliation(target_key TEXT,target_fingerprint TEXT,target_payload TEXT) RETURNS TEXT
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE payload JSONB; item JSONB; bank JSONB; original RECORD; bank_row RECORD; receipt RECORD;
  selected_ids TEXT[]; all_ids TEXT[]:=ARRAY[]::TEXT[]; events TEXT[]:=ARRAY[]::TEXT[]; total_amount NUMERIC;
  result_id TEXT; stamp TEXT;
 BEGIN
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  payload:=target_payload::jsonb;
  IF NOT accounting.actor_active() OR target_key IS NULL OR target_fingerprint IS NULL OR length(target_key) NOT BETWEEN 8 AND 120 OR length(target_fingerprint) NOT BETWEEN 1 AND 100
   OR jsonb_typeof(payload) IS DISTINCT FROM 'object' OR jsonb_typeof(payload->'items') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'items') NOT BETWEEN 1 AND 20
   OR length(btrim(COALESCE(payload->>'reason',''))) NOT BETWEEN 1 AND 1000 THEN
   RAISE EXCEPTION 'Bounded reconciliation command required' USING ERRCODE='23514';
  END IF;
  -- Authorize every supplied canonical book/event before receipt recovery.
  FOR item IN SELECT value FROM jsonb_array_elements(payload->'items') LOOP
   IF NOT accounting.book_allowed(item->>'book_id',true) OR NOT EXISTS(SELECT 1 FROM accounting.business_mappings m
    WHERE m.event_id=item->>'event_id' AND m.book_id=item->>'book_id' AND m.firm_id=accounting.actor_firm()
     AND accounting.business_event_visible(m.event_id,m.firm_id,m.company_id)) THEN
    RAISE EXCEPTION 'Current scoped book/event authorization required' USING ERRCODE='42501';
   END IF;
  END LOOP;
  SELECT * INTO receipt FROM accounting.reconciliation_receipts WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND idempotency_key=target_key;
  IF FOUND THEN
   IF receipt.request_fingerprint<>target_fingerprint OR receipt.request_json::jsonb<>payload OR NOT accounting.reconciliation_receipt_visible(receipt.request_json) THEN
    RAISE EXCEPTION 'Reconciliation key already binds another command or revoked scope' USING ERRCODE='42501';
   END IF;
   RETURN receipt.id;
  END IF;
  -- All validation precedes any mutation. Source amounts remain integer cents.
  FOR item IN SELECT value FROM jsonb_array_elements(payload->'items') LOOP
   IF item->>'event_id'=ANY(events) OR jsonb_typeof(item->'banks') IS DISTINCT FROM 'array' OR jsonb_array_length(item->'banks') NOT BETWEEN 1 AND 20 THEN
    RAISE EXCEPTION 'Distinct bounded business/bank selection required' USING ERRCODE='23514';
   END IF;
   events:=array_append(events,item->>'event_id');
   SELECT m.*,e.version AS event_version,e.fact_version,e.status AS event_status,e.payload::jsonb AS facts,
    d.version AS business_draft_version,d.status AS draft_status,s.amount_cents,s.direction,s.counterparty,s.date,s.fingerprint,p.status AS period_status
    INTO original FROM accounting.business_mappings m JOIN accounting.business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id
    JOIN accounting.books b ON b.id=m.book_id AND b.firm_id=m.firm_id AND b.company_id=m.company_id
    JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
    JOIN accounting.drafts d ON d.id=m.draft_id AND d.book_id=m.book_id AND d.source_id=m.source_id
    JOIN accounting.sources s ON s.id=m.source_id AND s.book_id=m.book_id AND s.source_kind='business'
    WHERE m.event_id=item->>'event_id' AND m.book_id=item->>'book_id' AND m.firm_id=accounting.actor_firm();
   IF NOT FOUND OR original.period_status<>'open' OR original.event_status<>'handed_off' OR original.draft_status<>'draft'
    OR original.facts->>'payment_status' IS DISTINCT FROM 'paid' OR original.event_version IS DISTINCT FROM (item->>'event_version')::INTEGER
    OR original.fact_version IS DISTINCT FROM (item->>'fact_version')::INTEGER OR original.business_draft_version IS DISTINCT FROM (item->>'business_draft_version')::INTEGER
    OR original.fingerprint IS DISTINCT FROM item->>'business_source_fingerprint' OR original.amount_cents<>((original.facts->>'amount')::NUMERIC*100)
    OR original.direction<>original.facts->>'direction' OR original.counterparty<>original.facts->>'counterparty' OR original.date<>original.facts->>'occurred_on' THEN
    RAISE EXCEPTION 'Current open unreviewed canonical paid facts required' USING ERRCODE='23514';
   END IF;
   selected_ids:=ARRAY[]::TEXT[]; total_amount:=0;
   FOR bank IN SELECT value FROM jsonb_array_elements(item->'banks') LOOP
    IF COALESCE(bank->>'source_id','')='' OR bank->>'source_id'=ANY(all_ids) THEN
     RAISE EXCEPTION 'A bank source may be used once per command' USING ERRCODE='23514';
    END IF;
    all_ids:=array_append(all_ids,bank->>'source_id'); selected_ids:=array_append(selected_ids,bank->>'source_id');
    SELECT s.*,d.version AS draft_version,d.status AS draft_status,l.event_id AS linked_event INTO bank_row
     FROM accounting.sources s JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id
     LEFT JOIN accounting.ai_bank_links l ON l.bank_source_id=s.id WHERE s.id=bank->>'source_id' AND s.book_id=original.book_id AND s.source_kind='bank';
    IF NOT FOUND OR bank_row.draft_status<>'draft' OR bank_row.draft_version IS DISTINCT FROM (bank->>'draft_version')::INTEGER
     OR bank_row.fingerprint IS DISTINCT FROM bank->>'source_fingerprint' OR (bank_row.linked_event IS NOT NULL AND bank_row.linked_event<>original.event_id)
     OR bank_row.direction<>original.direction OR bank_row.counterparty<>original.counterparty OR abs(bank_row.date::DATE-original.date::DATE)>7
     OR accounting.bank_source_import_blocked(original.book_id,bank_row.id) THEN
     RAISE EXCEPTION 'Current unreviewed same-party bank facts and import review required' USING ERRCODE='23514';
    END IF;
    total_amount:=total_amount+bank_row.amount_cents;
   END LOOP;
   IF total_amount<>original.amount_cents OR EXISTS(SELECT 1 FROM accounting.ai_bank_links l WHERE l.event_id=original.event_id AND l.book_id=original.book_id AND NOT l.bank_source_id=ANY(selected_ids)) THEN
    RAISE EXCEPTION 'Selected bank total and complete current linkage must equal paid business amount' USING ERRCODE='23514';
   END IF;
  END LOOP;
  stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
  FOR item IN SELECT value FROM jsonb_array_elements(payload->'items') LOOP
   FOR bank IN SELECT value FROM jsonb_array_elements(item->'banks') LOOP
    INSERT INTO accounting.ai_bank_links(bank_source_id,firm_id,company_id,event_id,book_id,created_by,created_at)
     SELECT bank->>'source_id',m.firm_id,m.company_id,m.event_id,m.book_id,accounting.actor_user(),stamp
     FROM accounting.business_mappings m WHERE m.event_id=item->>'event_id' AND m.book_id=item->>'book_id'
      AND NOT EXISTS(SELECT 1 FROM accounting.ai_bank_links l WHERE l.bank_source_id=bank->>'source_id');
   END LOOP;
  END LOOP;
  result_id:=md5(clock_timestamp()::TEXT||random()::TEXT||accounting.actor_user()||target_key);
  INSERT INTO accounting.reconciliation_receipts(id,firm_id,created_by,idempotency_key,request_fingerprint,request_json,reason,created_at)
   VALUES(result_id,accounting.actor_firm(),accounting.actor_user(),target_key,target_fingerprint,payload::TEXT,payload->>'reason',stamp);
  RETURN result_id;
 END
$$;
REVOKE ALL ON FUNCTION accounting.reconciliation_receipt_visible(TEXT),accounting.confirm_reconciliation(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.reconciliation_receipt_visible(TEXT),accounting.confirm_reconciliation(TEXT,TEXT,TEXT) TO accounting_app;
