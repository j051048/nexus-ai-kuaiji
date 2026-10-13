-- Forward hardening after 066 was applied; preserve immutable scope/history.
ALTER FUNCTION accounting.assistant_message_guard() SECURITY DEFINER;
CREATE FUNCTION accounting.assistant_turn_context_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.assistant_conversations;history JSONB;payload JSONB:=NEW.snapshot_json::jsonb;BEGIN
 SELECT * INTO c FROM accounting.assistant_conversations WHERE id=NEW.conversation_id;
 IF c.id IS NULL OR NOT accounting.assistant_conversation_allowed(c.id) THEN RAISE EXCEPTION 'Current private conversation required' USING ERRCODE='23514';END IF;
 IF coalesce(jsonb_typeof(payload->'books')='array' AND jsonb_typeof(payload->'events')='array' AND jsonb_typeof(payload->'evidence')='array',false) IS NOT TRUE
 OR coalesce((SELECT jsonb_agg(b->'id' ORDER BY b->>'id') FROM jsonb_array_elements(payload->'books') b),'[]'::jsonb) IS DISTINCT FROM c.book_ids_json::jsonb
 OR coalesce((SELECT jsonb_agg(e->'id' ORDER BY e->>'id') FROM jsonb_array_elements(payload->'events') e),'[]'::jsonb) IS DISTINCT FROM c.event_ids_json::jsonb
 OR payload->'draft'->>'id' IS DISTINCT FROM c.draft_id THEN RAISE EXCEPTION 'Frozen context may include only explicit scope references' USING ERRCODE='23514';END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(payload->'evidence') e WHERE coalesce(
  (e->>'kind'='book' AND e->>'id'='book:'||(e->>'book_id') AND c.book_ids_json::jsonb ? (e->>'book_id'))
  OR (e->>'kind'='event' AND c.event_ids_json::jsonb ? (e->>'event_id') AND EXISTS(SELECT 1 FROM accounting.business_events r WHERE r.id=e->>'event_id' AND e->>'id'='event:'||r.id||':v'||r.fact_version::text))
  OR (e->>'kind'='attachment' AND c.event_ids_json::jsonb ? (e->>'event_id') AND EXISTS(SELECT 1 FROM accounting.business_attachments a WHERE a.id=e->>'attachment_id' AND a.event_id=e->>'event_id' AND e->>'id'='attachment:'||a.id))
  OR (e->>'kind'='draft' AND e->>'draft_id'=c.draft_id AND EXISTS(SELECT 1 FROM accounting.drafts d WHERE d.id=c.draft_id AND d.book_id=e->>'book_id' AND e->>'id'='draft:'||d.id||':v'||d.version::text)),false) IS NOT TRUE)
 THEN RAISE EXCEPTION 'Evidence identifiers must belong to the explicit current scope' USING ERRCODE='23514';END IF;
 SELECT coalesce(jsonb_agg(jsonb_build_object('role',m.role,'content',m.content_json::jsonb) ORDER BY m.created_at,m.id),'[]'::jsonb) INTO history
 FROM (SELECT role,content_json,created_at,id FROM accounting.assistant_messages WHERE conversation_id=c.id ORDER BY created_at DESC,id DESC LIMIT 12) m;
 WHILE jsonb_array_length(history)>0 AND octet_length(history::text)>49152 LOOP history:=history-0;END LOOP;
 IF history IS DISTINCT FROM NEW.history_json::jsonb THEN RAISE EXCEPTION 'Turn history must come from immutable private messages' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE TRIGGER assistant_turn_context_guard BEFORE INSERT ON accounting.assistant_turns FOR EACH ROW EXECUTE FUNCTION accounting.assistant_turn_context_guard();
REVOKE ALL ON FUNCTION accounting.assistant_turn_context_guard() FROM PUBLIC;
CREATE OR REPLACE FUNCTION accounting.assistant_recover() RETURNS INTEGER LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE t accounting.assistant_turns;stamp TEXT;n INTEGER:=0;outcome TEXT;BEGIN
 IF NOT accounting.migration_worker_login() THEN RAISE EXCEPTION 'Dedicated worker login required' USING ERRCODE='42501';END IF;PERFORM pg_advisory_xact_lock(6420031786281903101);stamp:=accounting.ocr_utc_now();
 FOR t IN SELECT * FROM accounting.assistant_turns WHERE status='running' AND lease_until::timestamptz<=clock_timestamp() ORDER BY lease_until,id LIMIT 100 FOR UPDATE LOOP
 outcome:=CASE WHEN t.sent_at IS NOT NULL THEN 'interrupted' WHEN NOT accounting.assistant_execution_allowed(t.id) THEN 'stale' WHEN t.attempt_count>=3 THEN 'failed' ELSE 'queued' END;
 UPDATE accounting.assistant_turns SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,available_at=stamp,error_code=CASE WHEN t.sent_at IS NOT NULL THEN 'response_unknown' ELSE 'lease_expired' END,error_detail=CASE WHEN t.sent_at IS NOT NULL THEN '请求已经发出，结果未知；不会自动重复发送，请核查后明确提出新问题' ELSE '未发送请求已恢复排队' END,updated_at=stamp,finished_at=CASE WHEN outcome='queued' THEN NULL ELSE stamp END WHERE id=t.id;n:=n+1;
 END LOOP;RETURN n;END $$;
CREATE OR REPLACE FUNCTION accounting.assistant_claim(target_worker TEXT,lease_seconds INTEGER DEFAULT 180) RETURNS SETOF accounting.assistant_turns LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE t accounting.assistant_turns;stamp TEXT;seq BIGINT;BEGIN
 IF NOT accounting.migration_worker_login() OR length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 THEN RAISE EXCEPTION 'Dedicated bounded worker required' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);PERFORM accounting.assistant_recover();
 IF (SELECT count(*) FROM accounting.assistant_turns WHERE status='running')+(SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')+(SELECT count(*) FROM accounting.proposal_jobs WHERE status='running')>=2 THEN RETURN;END IF;
 INSERT INTO accounting.assistant_company_schedule(firm_id,company_id) SELECT DISTINCT firm_id,company_id FROM accounting.assistant_turns WHERE status='queued' ON CONFLICT DO NOTHING;
 FOR t IN SELECT q.* FROM accounting.assistant_turns q JOIN accounting.assistant_company_schedule s ON s.firm_id=q.firm_id AND s.company_id=q.company_id WHERE q.status='queued' AND q.available_at::timestamptz<=clock_timestamp()
 AND NOT EXISTS(SELECT 1 FROM accounting.assistant_turns a WHERE a.firm_id=q.firm_id AND a.company_id=q.company_id AND a.status='running')
 AND NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs a WHERE a.firm_id=q.firm_id AND a.company_id=q.company_id AND a.status='running')
 AND NOT EXISTS(SELECT 1 FROM accounting.proposal_jobs a WHERE a.firm_id=q.firm_id AND a.company_id=q.company_id AND a.status='running') ORDER BY s.last_dispatch_seq,q.created_at,q.id LIMIT 100 FOR UPDATE OF q LOOP
 stamp:=accounting.ocr_utc_now();IF NOT accounting.assistant_execution_allowed(t.id) THEN UPDATE accounting.assistant_turns SET status='stale',error_code='scope_changed',error_detail='授权或已保存资料已变化，请重新选择当前范围',updated_at=stamp,finished_at=stamp WHERE id=t.id;CONTINUE;END IF;
 SELECT coalesce(max(last_dispatch_seq),0)+1 INTO seq FROM accounting.assistant_company_schedule;UPDATE accounting.assistant_company_schedule SET last_dispatch_seq=seq WHERE firm_id=t.firm_id AND company_id=t.company_id;
 UPDATE accounting.assistant_turns SET status='running',attempt_count=attempt_count+1,lease_token=md5(random()::text||clock_timestamp()::text||t.id),lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),worker_id=target_worker,updated_at=stamp,error_code='',error_detail='' WHERE id=t.id RETURNING * INTO t;RETURN NEXT t;RETURN;
 END LOOP;END $$;
CREATE OR REPLACE FUNCTION accounting.assistant_finish(target TEXT,token TEXT,outcome TEXT,answer TEXT DEFAULT NULL,code TEXT DEFAULT '',detail TEXT DEFAULT '') RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE t accounting.assistant_turns;stamp TEXT;state TEXT:=outcome;j JSONB;BEGIN
 IF NOT accounting.migration_worker_login() OR outcome NOT IN ('succeeded','failed','interrupted','stale','cancelled') OR length(code)>120 OR length(detail)>1000 THEN RAISE EXCEPTION 'Dedicated valid completion required' USING ERRCODE='42501';END IF;PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO t FROM accounting.assistant_turns WHERE id=target AND status='running' AND lease_token=token AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;IF t.id IS NULL THEN RETURN false;END IF;
 IF NOT accounting.assistant_execution_allowed(t.id) THEN state:='stale';END IF;
 IF state='succeeded' THEN
 j:=answer::jsonb;IF t.sent_at IS NULL OR coalesce(jsonb_typeof(j)='object' AND jsonb_typeof(j->'answer')='string' AND length(j->>'answer') BETWEEN 1 AND 12000 AND jsonb_typeof(j->'citation_ids')='array' AND jsonb_array_length(j->'citation_ids')<=100 AND jsonb_typeof(j->'proposals')='array' AND jsonb_array_length(j->'proposals')<=3,false) IS NOT TRUE
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(j->'citation_ids') r WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(t.snapshot_json::jsonb->'evidence') e WHERE e->>'id'=r.value))
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(j->'proposals') p WHERE coalesce(p->>'kind','') NOT IN ('ocr_batch','proposal_batch','bank_drafts') OR coalesce(length(p->>'reason'),0) NOT BETWEEN 1 AND 1000)
 OR jsonb_array_length(j->'citation_ids')<>(SELECT count(DISTINCT value) FROM jsonb_array_elements_text(j->'citation_ids')) OR EXISTS(SELECT 1 FROM jsonb_array_elements(j->'citation_ids') r WHERE jsonb_typeof(r)<>'string') OR EXISTS(SELECT 1 FROM jsonb_array_elements(j->'proposals') p WHERE jsonb_typeof(p)<>'object' OR jsonb_typeof(p->'reason') IS DISTINCT FROM 'string' OR p-ARRAY['kind','reason']<>'{}'::jsonb) OR length(btrim(j->>'answer'))=0 OR j-ARRAY['answer','citation_ids','proposals']<>'{}'::jsonb THEN RAISE EXCEPTION 'Answer must be typed and cite only the frozen current scope' USING ERRCODE='23514';END IF;
 END IF;stamp:=accounting.ocr_utc_now();
 UPDATE accounting.assistant_turns SET status=state,reply_json=CASE WHEN state='succeeded' THEN answer ELSE NULL END,lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code=CASE WHEN state='stale' THEN 'scope_changed' ELSE code END,error_detail=detail,updated_at=stamp,finished_at=stamp WHERE id=t.id;
 IF state='succeeded' THEN INSERT INTO accounting.assistant_messages(id,conversation_id,turn_id,role,content_json,created_at) VALUES(md5(t.id||':assistant'),t.conversation_id,t.id,'assistant',answer,stamp);END IF;RETURN true;END $$;
