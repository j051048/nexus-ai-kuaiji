-- A business journal inherits unresolved blockers on its linked bank evidence.
CREATE FUNCTION accounting.source_workflow_blocked(target_book TEXT,target_source TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book)
   THEN RAISE EXCEPTION 'Source workflow scope denied' USING ERRCODE='42501'; END IF;
  RETURN accounting.case_source_blocked(target_book,target_source) OR accounting.bank_source_import_blocked(target_book,target_source)
   OR EXISTS(SELECT 1 FROM accounting.business_mappings m JOIN accounting.ai_bank_links l ON l.event_id=m.event_id AND l.book_id=m.book_id
    WHERE m.book_id=target_book AND m.source_id=target_source AND
     (accounting.case_source_blocked(target_book,l.bank_source_id) OR accounting.bank_source_import_blocked(target_book,l.bank_source_id)));
 END $$;
REVOKE ALL ON FUNCTION accounting.source_workflow_blocked(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.source_workflow_blocked(TEXT,TEXT) TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.confirm_reconciliation(target_key TEXT,target_fingerprint TEXT,target_payload TEXT) RETURNS TEXT
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
     OR accounting.bank_source_import_blocked(original.book_id,bank_row.id) OR accounting.case_source_blocked(original.book_id,bank_row.id) THEN
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
CREATE OR REPLACE FUNCTION accounting.worker_finish_proposal_company_rule(target_job TEXT,target_token TEXT,target_rule TEXT,target_version INTEGER,target_draft_version INTEGER) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.proposal_jobs; stamp TEXT;
 BEGIN
  IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') THEN RAISE EXCEPTION 'Worker scope denied' USING ERRCODE='42501'; END IF;
  PERFORM pg_advisory_xact_lock(6420031786281903101);
  SELECT * INTO j FROM accounting.proposal_jobs WHERE id=target_job AND status='running' AND lease_token=target_token AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
  IF j.id IS NULL OR j.current_run_id IS NOT NULL OR target_draft_version<>j.expected_draft_version+1
   OR NOT accounting.book_allowed(j.book_id,true) OR NOT accounting.ai_event_open(j.event_id,j.firm_id,j.company_id) THEN RETURN false; END IF;
  IF NOT EXISTS(SELECT 1 FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version
   JOIN accounting.drafts d ON d.id=j.draft_id AND d.book_id=j.book_id JOIN accounting.business_events e ON e.id=j.event_id
   WHERE r.id=target_rule AND r.book_id=j.book_id AND r.firm_id=j.firm_id AND r.company_id=j.company_id AND r.status='published' AND r.current_version=target_version
   AND d.status='draft' AND d.version=target_draft_version AND d.last_manual_edit_version=0 AND e.fact_version=j.expected_fact_version
   AND accounting.learning_matches(v.definition_json::jsonb,jsonb_build_object('source_kind','business','business_type',e.template_id,'payload',e.payload::jsonb))
   AND accounting.learning_render_matches(j.book_id,v.definition_json::jsonb,
    jsonb_build_object('source_kind','business','business_type',e.template_id,'payload',e.payload::jsonb,
     'context',jsonb_build_object('department_id',e.department_id,'applicant_id',e.applicant_id),
     'recognized',accounting.learning_recognized(e.id,e.fact_version,(SELECT amount_cents FROM accounting.sources WHERE id=d.source_id AND book_id=j.book_id))),
    jsonb_build_object('lines',(SELECT jsonb_agg(jsonb_build_object('account_code',l.account_code,'debit',(l.debit_cents/100)::text||'.'||lpad((l.debit_cents%100)::text,2,'0'),'credit',(l.credit_cents/100)::text||'.'||lpad((l.credit_cents%100)::text,2,'0'),
     'auxiliary_values',COALESCE(a.values_json::jsonb,'{}'::jsonb)) ORDER BY l.ordinal)
     FROM accounting.draft_lines l LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal
     WHERE l.draft_id=j.draft_id AND l.book_id=j.book_id)))
   AND NOT accounting.source_workflow_blocked(j.book_id,d.source_id)) THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.proposal_attempts SET phase='finished',status='succeeded',error_code='',error_detail='',finished_at=stamp WHERE job_id=j.id AND lease_token=target_token AND status='running';
  UPDATE accounting.proposal_jobs SET status='succeeded',lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,finished_at=stamp,error_code='',error_detail='',
   result_rule_id=target_rule,result_rule_version=target_version,result_draft_version=target_draft_version WHERE id=j.id;
  RETURN true;
 END
$$;
