-- Human-confirmed, fixed-scope assistant tools. Never approve or export records.
CREATE TABLE accounting.assistant_action_plans(
 id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('ocr_batch','proposal_batch','bank_drafts')),
 snapshot_json TEXT NOT NULL CHECK(jsonb_typeof(snapshot_json::jsonb)='object'),fingerprint TEXT NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
 item_count INTEGER NOT NULL CHECK(item_count BETWEEN 0 AND 100),reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),
 request_key TEXT NOT NULL CHECK(length(btrim(request_key)) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,
 UNIQUE(conversation_id,created_by,request_key),UNIQUE(id,conversation_id,firm_id,company_id,created_by),
 FOREIGN KEY(conversation_id,firm_id,company_id,created_by) REFERENCES accounting.assistant_conversations(id,firm_id,company_id,created_by)
);
CREATE TABLE accounting.assistant_action_execution_claims(
 plan_id TEXT PRIMARY KEY REFERENCES accounting.assistant_action_plans(id),transaction_id BIGINT NOT NULL,
 confirmed_by TEXT NOT NULL REFERENCES accounting.users(id),request_key TEXT NOT NULL,created_at TEXT NOT NULL
);
CREATE TABLE accounting.assistant_action_receipts(
 id TEXT PRIMARY KEY,plan_id TEXT NOT NULL UNIQUE REFERENCES accounting.assistant_action_execution_claims(plan_id),conversation_id TEXT NOT NULL,
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,request_key TEXT NOT NULL CHECK(length(btrim(request_key)) BETWEEN 8 AND 120),
 request_fingerprint TEXT NOT NULL CHECK(request_fingerprint ~ '^[0-9a-f]{64}$'),result_json TEXT NOT NULL CHECK(jsonb_typeof(result_json::jsonb)='object'),created_at TEXT NOT NULL,
 FOREIGN KEY(plan_id,conversation_id,firm_id,company_id,created_by) REFERENCES accounting.assistant_action_plans(id,conversation_id,firm_id,company_id,created_by)
);
CREATE INDEX assistant_action_plans_scope ON accounting.assistant_action_plans(conversation_id,created_by,created_at,id);

CREATE FUNCTION accounting.assistant_action_books_current(input JSONB) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE item JSONB;target_book TEXT;must_edit BOOLEAN:=input->>'kind'<>'ocr_batch';
BEGIN
 IF jsonb_typeof(input->'books') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(input->'books') LOOP
  target_book:=item->>'id';
  IF NOT accounting.book_allowed(target_book,must_edit)
   OR NOT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
     WHERE b.id=target_book AND b.period=item->>'period' AND p.status='open' AND p.status=item->>'period_status')
   OR item->'accounts' IS DISTINCT FROM COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.code) FROM
     (SELECT code,name,active,version,auxiliary_requirements FROM accounting.accounts WHERE book_id=target_book) x),'[]'::jsonb)
   OR item->'rules' IS DISTINCT FROM COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.id) FROM
     (SELECT r.id,r.status,r.state_version,r.current_version,v.definition_fingerprint FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.book_id=target_book) x),'[]'::jsonb)
   OR item->'templates' IS DISTINCT FROM COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.id) FROM
     (SELECT t.id,t.active,t.current_version,v.definition_fingerprint FROM accounting.journal_templates t JOIN accounting.journal_template_versions v ON v.template_id=t.id AND v.version=t.current_version WHERE t.book_id=target_book) x),'[]'::jsonb)
   OR item->'migration_rules' IS DISTINCT FROM COALESCE((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.id) FROM accounting.migration_learning_public_rules(target_book) x),'[]'::jsonb)
   THEN RETURN false;END IF;
 END LOOP;
 RETURN true;
END $$;

CREATE FUNCTION accounting.assistant_action_item_valid(target_conversation TEXT,action_kind TEXT,item JSONB,after_execution BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE c accounting.assistant_conversations%ROWTYPE;e accounting.business_events%ROWTYPE;a accounting.business_attachments%ROWTYPE;
 b accounting.books%ROWTYPE;s accounting.sources%ROWTYPE;d accounting.drafts%ROWTYPE;mapping accounting.business_mappings%ROWTYPE;
BEGIN
 IF NOT accounting.assistant_conversation_allowed(target_conversation) OR jsonb_typeof(item)<>'object' THEN RETURN false;END IF;
 SELECT * INTO c FROM accounting.assistant_conversations WHERE id=target_conversation;
 IF jsonb_array_length(c.book_ids_json::jsonb)=0 AND jsonb_array_length(c.event_ids_json::jsonb)=0 THEN RETURN false;END IF;
 IF action_kind='bank_drafts' THEN
  IF jsonb_array_length(c.book_ids_json::jsonb)=0 OR jsonb_array_length(c.event_ids_json::jsonb)>0 OR c.draft_id IS NOT NULL
   OR NOT jsonb_exists(c.book_ids_json::jsonb,item->>'book_id') THEN RETURN false;END IF;
  SELECT * INTO s FROM accounting.sources WHERE id=item->>'source_id' AND book_id=item->>'book_id';
  SELECT * INTO b FROM accounting.books WHERE id=s.book_id;
  IF s.id IS NULL OR s.source_kind<>'bank' OR to_jsonb(s) IS DISTINCT FROM item->'source' OR b.company_id<>c.company_id
   OR NOT accounting.book_allowed(b.id,true) OR accounting.source_inactive(b.id,s.id) OR accounting.revision_source_blocked(b.id,s.id)
   OR NOT EXISTS(SELECT 1 FROM accounting.periods p WHERE p.book_id=b.id AND p.period=b.period AND p.status='open')
   OR EXISTS(SELECT 1 FROM accounting.ai_bank_links l WHERE l.bank_source_id=s.id)
   OR (NOT after_execution AND EXISTS(SELECT 1 FROM accounting.drafts x WHERE x.source_id=s.id))
   OR COALESCE((SELECT jsonb_agg(jsonb_build_object('id',x.id,'evidence_ref',x.evidence_ref,'created_at',x.created_at,'created_by',x.created_by) ORDER BY x.id) FROM accounting.source_evidence x WHERE x.source_id=s.id),'[]'::jsonb) IS DISTINCT FROM item->'evidence'
   THEN RETURN false;END IF;
  RETURN true;
 END IF;
 IF action_kind NOT IN('ocr_batch','proposal_batch') THEN RETURN false;END IF;
 SELECT * INTO e FROM accounting.business_events WHERE id=item->>'event_id' AND firm_id=c.firm_id AND company_id=c.company_id;
 IF e.id IS NULL OR e.version IS DISTINCT FROM (item->>'expected_version')::INTEGER
  OR e.fact_version IS DISTINCT FROM (item->>'expected_fact_version')::INTEGER OR e.payload IS DISTINCT FROM item->>'event_payload'
  OR e.status IS DISTINCT FROM item->>'event_status'
  OR (jsonb_array_length(c.event_ids_json::jsonb)>0 AND NOT jsonb_exists(c.event_ids_json::jsonb,e.id))
  OR NOT accounting.ocr_process_scope(e.id,c.firm_id,c.company_id) THEN RETURN false;END IF;
 SELECT * INTO mapping FROM accounting.business_mappings WHERE event_id=e.id;
 IF c.draft_id IS NOT NULL AND mapping.draft_id IS DISTINCT FROM c.draft_id THEN RETURN false;END IF;
 IF jsonb_array_length(c.book_ids_json::jsonb)>0 AND (mapping.book_id IS NULL OR NOT jsonb_exists(c.book_ids_json::jsonb,mapping.book_id) OR mapping.book_id IS DISTINCT FROM item->>'book_id') THEN RETURN false;END IF;
 IF item->>'book_id' IS NOT NULL AND mapping.book_id IS DISTINCT FROM item->>'book_id' THEN RETURN false;END IF;
 IF action_kind='ocr_batch' THEN
  SELECT * INTO a FROM accounting.business_attachments WHERE id=item->>'attachment_id' AND event_id=e.id;
  RETURN COALESCE(a.id IS NOT NULL AND a.sha256 IS NOT DISTINCT FROM item->>'attachment_sha256'
   AND NOT EXISTS(SELECT 1 FROM accounting.event_superseded_attachments(e.id) x WHERE x=item->>'attachment_id')
   AND (after_execution OR NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs j WHERE j.event_id=e.id AND j.attachment_id=a.id AND j.status IN('queued','running','retry_wait'))),false);
 END IF;
 SELECT * INTO d FROM accounting.drafts WHERE id=item->>'draft_id' AND book_id=item->>'book_id';
 SELECT * INTO b FROM accounting.books WHERE id=d.book_id;
 RETURN COALESCE(jsonb_array_length(c.book_ids_json::jsonb)>0 AND jsonb_exists(c.book_ids_json::jsonb,b.id)
  AND e.status='handed_off' AND mapping.draft_id=d.id AND mapping.book_id=d.book_id AND d.status='draft' AND d.last_manual_edit_version=0
  AND d.version IS NOT DISTINCT FROM (item->>'expected_draft_version')::INTEGER AND to_jsonb(d) IS NOT DISTINCT FROM item->'draft'
  AND b.company_id=c.company_id AND accounting.book_allowed(b.id,true)
  AND EXISTS(SELECT 1 FROM accounting.periods p WHERE p.book_id=b.id AND p.period=b.period AND p.status='open')
  AND (after_execution OR NOT EXISTS(SELECT 1 FROM accounting.proposal_jobs j WHERE j.draft_id=d.id)),false);
END $$;

CREATE FUNCTION accounting.assistant_action_plan_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE c accounting.assistant_conversations%ROWTYPE;input JSONB;item JSONB;item_keys TEXT[]:='{}';item_key TEXT;
BEGIN
 SELECT * INTO c FROM accounting.assistant_conversations WHERE id=NEW.conversation_id;
 input:=NEW.snapshot_json::jsonb;
 IF c.id IS NULL OR NOT accounting.assistant_conversation_allowed(c.id) OR NEW.created_by<>accounting.actor_user() OR NEW.firm_id<>accounting.actor_firm()
  OR (NEW.firm_id,NEW.company_id,NEW.created_by) IS DISTINCT FROM (c.firm_id,c.company_id,c.created_by)
  OR input->>'kind' IS DISTINCT FROM NEW.kind OR input->>'scope_version' IS DISTINCT FROM c.scope_version::TEXT
  OR jsonb_typeof(input->'items') IS DISTINCT FROM 'array' OR jsonb_array_length(input->'items')<>NEW.item_count
  OR input->'scope' IS DISTINCT FROM jsonb_build_object('company_id',c.company_id,'book_ids',c.book_ids_json::jsonb,'event_ids',c.event_ids_json::jsonb,'draft_id',c.draft_id)
  OR NEW.fingerprint<>encode(sha256(convert_to(NEW.snapshot_json,'UTF8')),'hex')
  OR NEW.created_at::timestamptz>clock_timestamp()+interval '5 seconds' OR NEW.created_at::timestamptz<clock_timestamp()-interval '5 minutes'
  OR NEW.expires_at::timestamptz IS DISTINCT FROM NEW.created_at::timestamptz+interval '30 minutes'
  OR (NEW.item_count>0 AND input->>'input_basis' IS DISTINCT FROM accounting.assistant_input_basis(c.id))
  OR (NEW.item_count>0 AND NOT accounting.assistant_action_books_current(input))
  THEN RAISE EXCEPTION 'Assistant preview must bind current owner, scope and input facts' USING ERRCODE='23514';END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(input->'items') LOOP
  IF NOT accounting.assistant_action_item_valid(c.id,NEW.kind,item) THEN RAISE EXCEPTION 'Assistant preview contains an ineligible or hidden input' USING ERRCODE='23514';END IF;
  item_key:=CASE NEW.kind WHEN 'ocr_batch' THEN concat(item->>'event_id',':',item->>'attachment_id') WHEN 'proposal_batch' THEN item->>'draft_id' ELSE item->>'source_id' END;
  IF item_key=ANY(item_keys) THEN RAISE EXCEPTION 'Assistant preview contains duplicate inputs' USING ERRCODE='23514';END IF;
  item_keys:=array_append(item_keys,item_key);
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER assistant_action_plans_create BEFORE INSERT ON accounting.assistant_action_plans FOR EACH ROW EXECUTE FUNCTION accounting.assistant_action_plan_guard();
CREATE TRIGGER assistant_action_plans_immutable BEFORE UPDATE OR DELETE ON accounting.assistant_action_plans FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();

CREATE FUNCTION accounting.assistant_begin_action(target_plan TEXT,expected_fingerprint TEXT,confirmation_key TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE p accounting.assistant_action_plans%ROWTYPE;item JSONB;
BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO p FROM accounting.assistant_action_plans WHERE id=target_plan FOR UPDATE;
 IF p.id IS NULL OR NOT accounting.assistant_conversation_allowed(p.conversation_id) OR p.created_by<>accounting.actor_user()
  OR p.firm_id<>accounting.actor_firm() OR p.fingerprint IS DISTINCT FROM expected_fingerprint OR p.item_count=0
  OR p.expires_at::timestamptz<=clock_timestamp() OR length(btrim(confirmation_key)) NOT BETWEEN 8 AND 120
  OR (p.snapshot_json::jsonb)->>'input_basis' IS DISTINCT FROM accounting.assistant_input_basis(p.conversation_id)
  OR NOT accounting.assistant_action_books_current(p.snapshot_json::jsonb)
  OR EXISTS(SELECT 1 FROM accounting.assistant_action_receipts r WHERE r.plan_id=p.id)
  THEN RAISE EXCEPTION 'Assistant confirmation is expired, stale, consumed or outside current scope' USING ERRCODE='23514';END IF;
 FOR item IN SELECT value FROM jsonb_array_elements((p.snapshot_json::jsonb)->'items') LOOP
  IF NOT accounting.assistant_action_item_valid(p.conversation_id,p.kind,item) THEN RAISE EXCEPTION 'Assistant input eligibility changed before confirmation' USING ERRCODE='23514';END IF;
 END LOOP;
 INSERT INTO accounting.assistant_action_execution_claims(plan_id,transaction_id,confirmed_by,request_key,created_at)
 VALUES(p.id,txid_current(),accounting.actor_user(),confirmation_key,to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'));
 RETURN true;
END $$;

CREATE FUNCTION accounting.assistant_action_receipt_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE p accounting.assistant_action_plans%ROWTYPE;claim accounting.assistant_action_execution_claims%ROWTYPE;
 result JSONB;item JSONB;actual INTEGER;target_batch TEXT;target_job TEXT;target_draft TEXT;
BEGIN
 SELECT * INTO p FROM accounting.assistant_action_plans WHERE id=NEW.plan_id;
 SELECT * INTO claim FROM accounting.assistant_action_execution_claims WHERE plan_id=NEW.plan_id;
 result:=NEW.result_json::jsonb;
 IF p.id IS NULL OR NOT accounting.assistant_conversation_allowed(p.conversation_id) OR claim.plan_id IS NULL OR claim.transaction_id<>txid_current()
  OR NEW.created_by<>accounting.actor_user() OR claim.confirmed_by<>accounting.actor_user() OR NEW.request_key IS DISTINCT FROM claim.request_key
  OR (NEW.conversation_id,NEW.firm_id,NEW.company_id,NEW.created_by) IS DISTINCT FROM (p.conversation_id,p.firm_id,p.company_id,p.created_by)
  OR result->>'kind' IS DISTINCT FROM p.kind OR result->>'count' IS DISTINCT FROM p.item_count::TEXT
  OR jsonb_typeof(result->'jobs') IS DISTINCT FROM 'array' OR jsonb_typeof(result->'draft_ids') IS DISTINCT FROM 'array'
  THEN RAISE EXCEPTION 'Assistant receipt requires a same-transaction live human confirmation' USING ERRCODE='23514';END IF;
 IF p.kind='ocr_batch' THEN
  target_batch:=result->>'batch_id';
  IF target_batch IS NULL OR jsonb_array_length(result->'jobs')<>p.item_count OR jsonb_array_length(result->'draft_ids')<>0
   OR NOT EXISTS(SELECT 1 FROM accounting.ocr_batches b WHERE b.id=target_batch AND b.firm_id=p.firm_id AND b.company_id=p.company_id AND b.created_by=p.created_by AND b.idempotency_key='assistant:'||p.id AND b.created_at::timestamptz>=claim.created_at::timestamptz)
   OR (SELECT COUNT(*) FROM accounting.ocr_jobs j WHERE j.batch_id=target_batch)<>p.item_count
   THEN RAISE EXCEPTION 'Assistant OCR receipt does not prove the selected batch' USING ERRCODE='23514';END IF;
 ELSIF p.kind='proposal_batch' THEN
  IF jsonb_array_length(result->'jobs')<>p.item_count OR jsonb_array_length(result->'draft_ids')<>0 THEN RAISE EXCEPTION 'Assistant proposal receipt count differs' USING ERRCODE='23514';END IF;
 ELSE
  IF jsonb_array_length(result->'draft_ids')<>p.item_count OR jsonb_array_length(result->'jobs')<>0 OR result->>'created' IS DISTINCT FROM p.item_count::TEXT THEN RAISE EXCEPTION 'Assistant bank receipt count differs' USING ERRCODE='23514';END IF;
 END IF;
 FOR item IN SELECT value FROM jsonb_array_elements((p.snapshot_json::jsonb)->'items') LOOP
  IF NOT accounting.assistant_action_item_valid(p.conversation_id,p.kind,item,true) THEN RAISE EXCEPTION 'Assistant execution left stale or hidden inputs' USING ERRCODE='23514';END IF;
  IF p.kind='ocr_batch' THEN
   SELECT COUNT(*) INTO actual FROM accounting.ocr_jobs j WHERE j.batch_id=target_batch AND j.event_id=item->>'event_id' AND j.attachment_id=item->>'attachment_id'
    AND j.expected_version=(item->>'expected_version')::INTEGER AND j.expected_fact_version=(item->>'expected_fact_version')::INTEGER
    AND j.attachment_sha256=item->>'attachment_sha256' AND j.configuration_fingerprint=(p.snapshot_json::jsonb)->>'configuration_fingerprint'
    AND j.execution_user_id=p.created_by AND j.created_by=p.created_by AND j.status='queued'
    AND EXISTS(SELECT 1 FROM jsonb_array_elements(result->'jobs') v WHERE v->>'id'=j.id AND v->>'event_id'=j.event_id AND v->>'status'=j.status);
  ELSIF p.kind='proposal_batch' THEN
   SELECT COUNT(*) INTO actual FROM accounting.proposal_jobs j WHERE j.event_id=item->>'event_id' AND j.book_id=item->>'book_id' AND j.draft_id=item->>'draft_id'
    AND j.expected_fact_version=(item->>'expected_fact_version')::INTEGER AND j.expected_draft_version=(item->>'expected_draft_version')::INTEGER
    AND j.configuration_fingerprint=(p.snapshot_json::jsonb)->>'configuration_fingerprint' AND j.policy_fingerprint=item->>'policy_fingerprint'
    AND j.execution_user_id=p.created_by AND j.created_by=p.created_by AND j.status IN('waiting','queued') AND j.created_at::timestamptz>=claim.created_at::timestamptz
    AND EXISTS(SELECT 1 FROM jsonb_array_elements(result->'jobs') v WHERE v->>'id'=j.id AND v->>'event_id'=j.event_id AND v->>'draft_id'=j.draft_id AND v->>'status'=j.status);
  ELSE
   SELECT COUNT(*) INTO actual FROM accounting.drafts d WHERE d.source_id=item->>'source_id' AND d.book_id=item->>'book_id' AND d.status='draft' AND d.version=1 AND d.last_manual_edit_version=0
    AND jsonb_exists(result->'draft_ids',d.id)
    AND EXISTS(SELECT 1 FROM accounting.audit_events a WHERE a.action='draft.suggest' AND a.object_id=d.id AND a.book_id=d.book_id AND a.firm_id=p.firm_id AND a.user_id=p.created_by AND a.created_at::timestamptz>=claim.created_at::timestamptz AND a.details::jsonb->>'source_id'=d.source_id);
  END IF;
  IF actual<>1 THEN RAISE EXCEPTION 'Assistant receipt does not prove every fixed input exactly once' USING ERRCODE='23514';END IF;
 END LOOP;
 RETURN NEW;
END $$;
CREATE TRIGGER assistant_action_receipts_create BEFORE INSERT ON accounting.assistant_action_receipts FOR EACH ROW EXECUTE FUNCTION accounting.assistant_action_receipt_guard();
CREATE TRIGGER assistant_action_receipts_immutable BEFORE UPDATE OR DELETE ON accounting.assistant_action_receipts FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER assistant_action_claims_immutable BEFORE UPDATE OR DELETE ON accounting.assistant_action_execution_claims FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.assistant_action_claim_complete() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM accounting.assistant_action_receipts r WHERE r.plan_id=NEW.plan_id) THEN RAISE EXCEPTION 'Assistant confirmation and receipt must commit atomically' USING ERRCODE='23514';END IF;RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER assistant_action_confirmation_atomic AFTER INSERT ON accounting.assistant_action_execution_claims DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION accounting.assistant_action_claim_complete();
ALTER TABLE accounting.assistant_action_plans ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.assistant_action_plans FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_action_receipts ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.assistant_action_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_action_execution_claims ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.assistant_action_execution_claims FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT ON accounting.assistant_action_plans,accounting.assistant_action_receipts TO accounting_app;
REVOKE ALL ON accounting.assistant_action_execution_claims FROM PUBLIC,accounting_app,accounting_worker;
CREATE POLICY assistant_action_plans_read ON accounting.assistant_action_plans FOR SELECT TO accounting_app USING(created_by=accounting.actor_user() AND accounting.assistant_conversation_allowed(conversation_id));
CREATE POLICY assistant_action_plans_create ON accounting.assistant_action_plans FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.assistant_conversation_allowed(conversation_id));
CREATE POLICY assistant_action_receipts_read ON accounting.assistant_action_receipts FOR SELECT TO accounting_app USING(created_by=accounting.actor_user() AND accounting.assistant_conversation_allowed(conversation_id));
CREATE POLICY assistant_action_receipts_create ON accounting.assistant_action_receipts FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.assistant_conversation_allowed(conversation_id));
REVOKE ALL ON FUNCTION accounting.assistant_action_books_current(JSONB),accounting.assistant_action_item_valid(TEXT,TEXT,JSONB,BOOLEAN),accounting.assistant_action_plan_guard(),accounting.assistant_action_receipt_guard(),accounting.assistant_action_claim_complete(),accounting.assistant_begin_action(TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.assistant_begin_action(TEXT,TEXT,TEXT) TO accounting_app;
