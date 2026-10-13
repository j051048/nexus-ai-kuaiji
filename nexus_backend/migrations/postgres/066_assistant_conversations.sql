-- Private immutable scopes, dedicated leased execution, immutable messages.
CREATE TABLE accounting.assistant_conversations(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),scope_version INTEGER NOT NULL DEFAULT 1 CHECK(scope_version=1),
 book_ids_json TEXT NOT NULL CHECK(jsonb_typeof(book_ids_json::jsonb)='array' AND jsonb_array_length(book_ids_json::jsonb)<=12),
 event_ids_json TEXT NOT NULL CHECK(jsonb_typeof(event_ids_json::jsonb)='array' AND jsonb_array_length(event_ids_json::jsonb)<=20),draft_id TEXT,
 title TEXT NOT NULL CHECK(length(btrim(title)) BETWEEN 1 AND 120),create_key TEXT NOT NULL,create_fingerprint TEXT NOT NULL CHECK(length(create_fingerprint)=64),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,create_key),UNIQUE(id,firm_id,company_id,created_by),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),CHECK(draft_id IS NULL OR jsonb_array_length(book_ids_json::jsonb)=1)
);
CREATE TABLE accounting.assistant_turns(
 id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL,
 kind TEXT NOT NULL DEFAULT 'chat' CHECK(kind='chat'),provider TEXT NOT NULL,model TEXT NOT NULL,expected_scope_version INTEGER NOT NULL CHECK(expected_scope_version=1),
 question TEXT NOT NULL CHECK(length(btrim(question)) BETWEEN 1 AND 4000),input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),model_fingerprint TEXT NOT NULL CHECK(length(model_fingerprint)=64),
 snapshot_json TEXT NOT NULL CHECK(jsonb_typeof(snapshot_json::jsonb)='object' AND octet_length(snapshot_json)<=65536),history_json TEXT NOT NULL CHECK(jsonb_typeof(history_json::jsonb)='array' AND jsonb_array_length(history_json::jsonb)<=12 AND octet_length(history_json)<=49152),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','succeeded','failed','interrupted','stale','cancelled')),attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 3),available_at TEXT NOT NULL,sent_at TEXT,lease_token TEXT,lease_until TEXT,worker_id TEXT,error_code TEXT NOT NULL DEFAULT '',error_detail TEXT NOT NULL DEFAULT '',reply_json TEXT,
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(conversation_id,request_key),UNIQUE(id,firm_id,company_id,created_by),FOREIGN KEY(conversation_id,firm_id,company_id,created_by) REFERENCES accounting.assistant_conversations(id,firm_id,company_id,created_by),CHECK(reply_json IS NULL OR jsonb_typeof(reply_json::jsonb)='object'),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL)
 OR (status='queued' AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL AND sent_at IS NULL)
 OR (status IN ('succeeded','failed','interrupted','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL)),
 CHECK((status='succeeded' AND reply_json IS NOT NULL AND sent_at IS NOT NULL) OR (status<>'succeeded' AND reply_json IS NULL))
);
CREATE UNIQUE INDEX idx_assistant_one_active ON accounting.assistant_turns(conversation_id) WHERE status IN ('queued','running');
CREATE INDEX idx_assistant_dispatch ON accounting.assistant_turns(status,available_at,firm_id,company_id,created_at,id);
CREATE TABLE accounting.assistant_messages(id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL REFERENCES accounting.assistant_conversations(id),turn_id TEXT NOT NULL REFERENCES accounting.assistant_turns(id),role TEXT NOT NULL CHECK(role IN ('user','assistant')),content_json TEXT NOT NULL CHECK(jsonb_typeof(content_json::jsonb)='object'),created_at TEXT NOT NULL,UNIQUE(turn_id,role));
CREATE INDEX idx_assistant_messages_page ON accounting.assistant_messages(conversation_id,created_at DESC,id);
CREATE INDEX idx_assistant_conversations_page ON accounting.assistant_conversations(firm_id,company_id,created_by,created_at DESC,id);
CREATE TABLE accounting.assistant_company_schedule(firm_id TEXT NOT NULL,company_id TEXT NOT NULL,last_dispatch_seq BIGINT NOT NULL DEFAULT 0,PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id));

CREATE FUNCTION accounting.assistant_refs_allowed(target_firm TEXT,target_company TEXT,target_books TEXT,target_events TEXT,target_draft TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.actor_active() AND target_firm=accounting.actor_firm() AND accounting.company_visible(target_company)
 AND EXISTS(SELECT 1 FROM accounting.firm_clients fc WHERE fc.firm_id=target_firm AND fc.company_id=target_company AND fc.status='active')
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(target_books::jsonb) ref WHERE NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=ref.value AND b.firm_id=target_firm AND b.company_id=target_company AND accounting.book_allowed(b.id)))
 AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(target_events::jsonb) ref WHERE NOT EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=ref.value AND e.firm_id=target_firm AND e.company_id=target_company AND accounting.business_event_visible(e.id,e.firm_id,e.company_id)))
 AND (target_draft IS NULL OR EXISTS(SELECT 1 FROM accounting.drafts d WHERE d.id=target_draft AND d.book_id=(target_books::jsonb->>0)))
 AND jsonb_array_length(target_books::jsonb)=(SELECT count(DISTINCT value) FROM jsonb_array_elements_text(target_books::jsonb))
 AND jsonb_array_length(target_events::jsonb)=(SELECT count(DISTINCT value) FROM jsonb_array_elements_text(target_events::jsonb))
$$;
CREATE FUNCTION accounting.assistant_conversation_allowed(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.assistant_conversations c WHERE c.id=target AND c.created_by=accounting.actor_user() AND accounting.assistant_refs_allowed(c.firm_id,c.company_id,c.book_ids_json,c.event_ids_json,c.draft_id))
$$;
CREATE FUNCTION accounting.assistant_input_basis(target TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT CASE WHEN accounting.assistant_conversation_allowed(target) THEN encode(sha256(convert_to(jsonb_build_object(
  'scope',jsonb_build_array(c.id,c.scope_version,c.book_ids_json::jsonb,c.event_ids_json::jsonb,c.draft_id),
  'books',(SELECT coalesce(jsonb_agg(jsonb_build_array(b.id,b.name,b.period,p.status,(SELECT count(*) FROM accounting.sources s WHERE s.book_id=b.id),(SELECT coalesce(sum(s.amount_cents),0) FROM accounting.sources s WHERE s.book_id=b.id),(SELECT coalesce(jsonb_agg(jsonb_build_array(x.status,x.n) ORDER BY x.status),'[]'::jsonb) FROM (SELECT d.status,count(*) n FROM accounting.drafts d WHERE d.book_id=b.id GROUP BY d.status) x)) ORDER BY b.id),'[]'::jsonb) FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id IN (SELECT value FROM jsonb_array_elements_text(c.book_ids_json::jsonb))),
  'events',(SELECT coalesce(jsonb_agg(jsonb_build_array(e.id,e.version,e.fact_version,e.status,e.payload::jsonb,(SELECT coalesce(jsonb_agg(jsonb_build_array(a.id,a.sha256) ORDER BY a.id),'[]'::jsonb) FROM accounting.business_attachments a WHERE a.event_id=e.id)) ORDER BY e.id),'[]'::jsonb) FROM accounting.business_events e WHERE e.id IN (SELECT value FROM jsonb_array_elements_text(c.event_ids_json::jsonb))),
  'draft',(SELECT jsonb_build_array(d.id,d.version,d.status,d.description,d.explanation,d.source_id,(SELECT coalesce(jsonb_agg(to_jsonb(l) ORDER BY l.ordinal),'[]'::jsonb) FROM accounting.draft_lines l WHERE l.draft_id=d.id)) FROM accounting.drafts d WHERE d.id=c.draft_id)
 )::text,'UTF8')),'hex') ELSE NULL END FROM accounting.assistant_conversations c WHERE c.id=target
$$;
CREATE FUNCTION accounting.assistant_execution_allowed(target TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE t accounting.assistant_turns;u TEXT:=accounting.actor_user();f TEXT:=accounting.actor_firm();allowed BOOLEAN;BEGIN
 SELECT * INTO t FROM accounting.assistant_turns WHERE id=target;IF t.id IS NULL THEN RETURN false;END IF;
 PERFORM set_config('app.user_id',t.created_by,true),set_config('app.firm_id',t.firm_id,true);
 allowed:=coalesce(accounting.assistant_conversation_allowed(t.conversation_id) AND t.input_fingerprint=accounting.assistant_input_basis(t.conversation_id) AND EXISTS(SELECT 1 FROM accounting.ai_company_settings s WHERE s.firm_id=t.firm_id AND s.company_id=t.company_id AND s.enabled=1),false);
 PERFORM set_config('app.user_id',u,true),set_config('app.firm_id',f,true);RETURN allowed;END $$;
CREATE FUNCTION accounting.assistant_check(target TEXT,token TEXT) RETURNS BOOLEAN LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.migration_worker_login() AND EXISTS(SELECT 1 FROM accounting.assistant_turns t WHERE t.id=target AND t.status='running' AND t.lease_token=token AND t.lease_until::timestamptz>clock_timestamp() AND accounting.assistant_execution_allowed(t.id))
$$;
CREATE FUNCTION accounting.assistant_conversation_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.created_by<>accounting.actor_user() OR NOT accounting.assistant_refs_allowed(NEW.firm_id,NEW.company_id,NEW.book_ids_json,NEW.event_ids_json,NEW.draft_id) OR NEW.scope_version<>1 THEN RAISE EXCEPTION 'Private current scope required' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE FUNCTION accounting.assistant_turn_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_OP='INSERT' THEN
 IF NOT accounting.assistant_conversation_allowed(NEW.conversation_id) OR NEW.created_by<>accounting.actor_user() OR NEW.status<>'queued' OR NEW.attempt_count<>0 OR NEW.sent_at IS NOT NULL OR NEW.lease_token IS NOT NULL OR NEW.reply_json IS NOT NULL OR NEW.input_fingerprint IS DISTINCT FROM accounting.assistant_input_basis(NEW.conversation_id) OR NOT EXISTS(SELECT 1 FROM accounting.ai_company_settings s WHERE s.firm_id=NEW.firm_id AND s.company_id=NEW.company_id AND s.enabled=1)
 OR NEW.snapshot_json::jsonb->>'input_fingerprint' IS DISTINCT FROM NEW.input_fingerprint OR NEW.snapshot_json::jsonb->>'company_id' IS DISTINCT FROM NEW.company_id THEN RAISE EXCEPTION 'Only current queued questions may be inserted' USING ERRCODE='23514';END IF;
 ELSE
 IF current_user='accounting_app' AND NOT accounting.migration_worker_login() THEN RAISE EXCEPTION 'Dedicated worker controls turn execution' USING ERRCODE='42501';END IF;
 IF (to_jsonb(NEW)-ARRAY['status','attempt_count','available_at','sent_at','lease_token','lease_until','worker_id','error_code','error_detail','reply_json','updated_at','finished_at']) IS DISTINCT FROM (to_jsonb(OLD)-ARRAY['status','attempt_count','available_at','sent_at','lease_token','lease_until','worker_id','error_code','error_detail','reply_json','updated_at','finished_at']) OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS DISTINCT FROM OLD.sent_at) THEN RAISE EXCEPTION 'Immutable turn input and send marker' USING ERRCODE='23514';END IF;
 END IF;RETURN NEW;END $$;
CREATE FUNCTION accounting.assistant_message_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE t accounting.assistant_turns;BEGIN
 SELECT * INTO t FROM accounting.assistant_turns WHERE id=NEW.turn_id;
 IF t.id IS NULL OR t.conversation_id<>NEW.conversation_id THEN RAISE EXCEPTION 'Message scope mismatch' USING ERRCODE='23514';END IF;
 IF NEW.role='user' THEN IF NOT accounting.assistant_conversation_allowed(NEW.conversation_id) OR NEW.content_json::jsonb IS DISTINCT FROM jsonb_build_object('question',t.question) THEN RAISE EXCEPTION 'Immutable question must match queued input' USING ERRCODE='23514';END IF;
 ELSE IF NOT accounting.migration_worker_login() OR t.status<>'succeeded' OR NEW.content_json::jsonb IS DISTINCT FROM t.reply_json::jsonb THEN RAISE EXCEPTION 'Only fenced worker completion may publish an answer' USING ERRCODE='42501';END IF;END IF;RETURN NEW;END $$;
CREATE TRIGGER assistant_conversation_create BEFORE INSERT ON accounting.assistant_conversations FOR EACH ROW EXECUTE FUNCTION accounting.assistant_conversation_guard();
CREATE TRIGGER assistant_conversation_immutable BEFORE UPDATE OR DELETE ON accounting.assistant_conversations FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER assistant_turn_guard BEFORE INSERT OR UPDATE ON accounting.assistant_turns FOR EACH ROW EXECUTE FUNCTION accounting.assistant_turn_guard();
CREATE TRIGGER assistant_turn_no_delete BEFORE DELETE ON accounting.assistant_turns FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER assistant_message_create BEFORE INSERT ON accounting.assistant_messages FOR EACH ROW EXECUTE FUNCTION accounting.assistant_message_guard();
CREATE TRIGGER assistant_message_immutable BEFORE UPDATE OR DELETE ON accounting.assistant_messages FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();

ALTER TABLE accounting.assistant_conversations ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.assistant_conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_turns ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.assistant_turns FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.assistant_messages ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.assistant_messages FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT ON accounting.assistant_conversations TO accounting_app;
GRANT SELECT(id,conversation_id,firm_id,company_id,created_by,kind,provider,model,expected_scope_version,question,input_fingerprint,model_fingerprint,snapshot_json,history_json,status,attempt_count,available_at,sent_at,error_code,error_detail,reply_json,request_key,request_fingerprint,created_at,updated_at,finished_at),INSERT ON accounting.assistant_turns TO accounting_app;
GRANT SELECT,INSERT ON accounting.assistant_messages TO accounting_app;
CREATE POLICY assistant_conversation_read ON accounting.assistant_conversations FOR SELECT TO accounting_app USING(accounting.assistant_conversation_allowed(id));
CREATE POLICY assistant_conversation_create ON accounting.assistant_conversations FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.assistant_refs_allowed(firm_id,company_id,book_ids_json,event_ids_json,draft_id));
CREATE POLICY assistant_turn_read ON accounting.assistant_turns FOR SELECT TO accounting_app USING(accounting.assistant_conversation_allowed(conversation_id));
CREATE POLICY assistant_turn_create ON accounting.assistant_turns FOR INSERT TO accounting_app WITH CHECK(accounting.assistant_conversation_allowed(conversation_id) AND created_by=accounting.actor_user());
CREATE POLICY assistant_message_read ON accounting.assistant_messages FOR SELECT TO accounting_app USING(accounting.assistant_conversation_allowed(conversation_id));
CREATE POLICY assistant_message_create ON accounting.assistant_messages FOR INSERT TO accounting_app WITH CHECK(role='user' AND accounting.assistant_conversation_allowed(conversation_id));

CREATE FUNCTION accounting.assistant_recover() RETURNS INTEGER LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE t accounting.assistant_turns;stamp TEXT;n INTEGER:=0;outcome TEXT;BEGIN
 IF NOT accounting.migration_worker_login() THEN RAISE EXCEPTION 'Dedicated worker login required' USING ERRCODE='42501';END IF;PERFORM pg_advisory_xact_lock(6420031786281903101);stamp:=accounting.ocr_utc_now();
 FOR t IN SELECT * FROM accounting.assistant_turns WHERE status='running' AND lease_until::timestamptz<=clock_timestamp() FOR UPDATE LOOP
 outcome:=CASE WHEN t.sent_at IS NOT NULL THEN 'interrupted' WHEN NOT accounting.assistant_execution_allowed(t.id) THEN 'stale' WHEN t.attempt_count>=3 THEN 'failed' ELSE 'queued' END;
 UPDATE accounting.assistant_turns SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,available_at=stamp,error_code=CASE WHEN t.sent_at IS NOT NULL THEN 'response_unknown' ELSE 'lease_expired' END,error_detail=CASE WHEN t.sent_at IS NOT NULL THEN '请求已经发出，结果未知；不会自动重复发送，请核查后明确提出新问题' ELSE '未发送请求已恢复排队' END,updated_at=stamp,finished_at=CASE WHEN outcome='queued' THEN NULL ELSE stamp END WHERE id=t.id;n:=n+1;
 END LOOP;RETURN n;END $$;
CREATE FUNCTION accounting.assistant_claim(target_worker TEXT,lease_seconds INTEGER DEFAULT 180) RETURNS SETOF accounting.assistant_turns LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE t accounting.assistant_turns;stamp TEXT;seq BIGINT;BEGIN
 IF NOT accounting.migration_worker_login() OR length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 30 AND 600 THEN RAISE EXCEPTION 'Dedicated bounded worker required' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);PERFORM accounting.assistant_recover();
 IF (SELECT count(*) FROM accounting.assistant_turns WHERE status='running')+(SELECT count(*) FROM accounting.ocr_jobs WHERE status='running')+(SELECT count(*) FROM accounting.proposal_jobs WHERE status='running')>=2 THEN RETURN;END IF;
 INSERT INTO accounting.assistant_company_schedule(firm_id,company_id) SELECT DISTINCT firm_id,company_id FROM accounting.assistant_turns WHERE status='queued' ON CONFLICT DO NOTHING;
 FOR t IN SELECT q.* FROM accounting.assistant_turns q JOIN accounting.assistant_company_schedule s ON s.firm_id=q.firm_id AND s.company_id=q.company_id WHERE q.status='queued' AND q.available_at::timestamptz<=clock_timestamp()
 AND NOT EXISTS(SELECT 1 FROM accounting.assistant_turns a WHERE a.firm_id=q.firm_id AND a.company_id=q.company_id AND a.status='running')
 AND NOT EXISTS(SELECT 1 FROM accounting.ocr_jobs a WHERE a.firm_id=q.firm_id AND a.company_id=q.company_id AND a.status='running')
 AND NOT EXISTS(SELECT 1 FROM accounting.proposal_jobs a WHERE a.firm_id=q.firm_id AND a.company_id=q.company_id AND a.status='running') ORDER BY s.last_dispatch_seq,q.created_at,q.id FOR UPDATE OF q LOOP
 stamp:=accounting.ocr_utc_now();IF NOT accounting.assistant_execution_allowed(t.id) THEN UPDATE accounting.assistant_turns SET status='stale',error_code='scope_changed',error_detail='授权或已保存资料已变化，请重新选择当前范围',updated_at=stamp,finished_at=stamp WHERE id=t.id;CONTINUE;END IF;
 SELECT coalesce(max(last_dispatch_seq),0)+1 INTO seq FROM accounting.assistant_company_schedule;UPDATE accounting.assistant_company_schedule SET last_dispatch_seq=seq WHERE firm_id=t.firm_id AND company_id=t.company_id;
 UPDATE accounting.assistant_turns SET status='running',attempt_count=attempt_count+1,lease_token=md5(random()::text||clock_timestamp()::text||t.id),lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),worker_id=target_worker,updated_at=stamp,error_code='',error_detail='' WHERE id=t.id RETURNING * INTO t;RETURN NEXT t;RETURN;
 END LOOP;END $$;
CREATE FUNCTION accounting.assistant_mark_sent(target TEXT,token TEXT,model_fp TEXT) RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);IF NOT accounting.assistant_check(target,token) THEN RETURN false;END IF;
 UPDATE accounting.assistant_turns SET sent_at=accounting.ocr_utc_now(),updated_at=accounting.ocr_utc_now() WHERE id=target AND sent_at IS NULL AND model_fingerprint=model_fp;RETURN FOUND;END $$;
CREATE FUNCTION accounting.assistant_finish(target TEXT,token TEXT,outcome TEXT,answer TEXT DEFAULT NULL,code TEXT DEFAULT '',detail TEXT DEFAULT '') RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE t accounting.assistant_turns;stamp TEXT;state TEXT:=outcome;j JSONB;BEGIN
 IF NOT accounting.migration_worker_login() OR outcome NOT IN ('succeeded','failed','interrupted','stale','cancelled') OR length(code)>120 OR length(detail)>1000 THEN RAISE EXCEPTION 'Dedicated valid completion required' USING ERRCODE='42501';END IF;PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO t FROM accounting.assistant_turns WHERE id=target AND status='running' AND lease_token=token AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;IF t.id IS NULL THEN RETURN false;END IF;
 IF NOT accounting.assistant_execution_allowed(t.id) THEN state:='stale';END IF;
 IF state='succeeded' THEN
 j:=answer::jsonb;IF t.sent_at IS NULL OR coalesce(jsonb_typeof(j)='object' AND jsonb_typeof(j->'answer')='string' AND length(j->>'answer') BETWEEN 1 AND 12000 AND jsonb_typeof(j->'citation_ids')='array' AND jsonb_array_length(j->'citation_ids')<=100 AND jsonb_typeof(j->'proposals')='array' AND jsonb_array_length(j->'proposals')<=3,false) IS NOT TRUE
 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(j->'citation_ids') r WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(t.snapshot_json::jsonb->'evidence') e WHERE e->>'id'=r.value))
 OR EXISTS(SELECT 1 FROM jsonb_array_elements(j->'proposals') p WHERE coalesce(p->>'kind','') NOT IN ('ocr_batch','proposal_batch','bank_drafts') OR coalesce(length(p->>'reason'),0) NOT BETWEEN 1 AND 1000)
 OR j-ARRAY['answer','citation_ids','proposals']<>'{}'::jsonb THEN RAISE EXCEPTION 'Answer must be typed and cite only the frozen current scope' USING ERRCODE='23514';END IF;
 END IF;stamp:=accounting.ocr_utc_now();
 UPDATE accounting.assistant_turns SET status=state,reply_json=CASE WHEN state='succeeded' THEN answer ELSE NULL END,lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code=CASE WHEN state='stale' THEN 'scope_changed' ELSE code END,error_detail=detail,updated_at=stamp,finished_at=stamp WHERE id=t.id;
 IF state='succeeded' THEN INSERT INTO accounting.assistant_messages(id,conversation_id,turn_id,role,content_json,created_at) VALUES(md5(t.id||':assistant'),t.conversation_id,t.id,'assistant',answer,stamp);END IF;RETURN true;END $$;
CREATE FUNCTION accounting.assistant_cancel(target TEXT,reason TEXT) RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE t accounting.assistant_turns;stamp TEXT;BEGIN IF length(btrim(reason)) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Reason required';END IF;PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO t FROM accounting.assistant_turns WHERE id=target AND accounting.assistant_conversation_allowed(conversation_id) FOR UPDATE;IF t.id IS NULL OR t.status NOT IN ('queued','running') THEN RETURN false;END IF;
 stamp:=accounting.ocr_utc_now();UPDATE accounting.assistant_turns SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='user_cancelled',error_detail=reason,updated_at=stamp,finished_at=stamp WHERE id=t.id;RETURN true;END $$;
REVOKE ALL ON FUNCTION accounting.assistant_refs_allowed(TEXT,TEXT,TEXT,TEXT,TEXT),accounting.assistant_conversation_allowed(TEXT),accounting.assistant_input_basis(TEXT),accounting.assistant_execution_allowed(TEXT),accounting.assistant_check(TEXT,TEXT),accounting.assistant_conversation_guard(),accounting.assistant_turn_guard(),accounting.assistant_message_guard(),accounting.assistant_recover(),accounting.assistant_claim(TEXT,INTEGER),accounting.assistant_mark_sent(TEXT,TEXT,TEXT),accounting.assistant_finish(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT),accounting.assistant_cancel(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.assistant_refs_allowed(TEXT,TEXT,TEXT,TEXT,TEXT),accounting.assistant_conversation_allowed(TEXT),accounting.assistant_input_basis(TEXT),accounting.assistant_check(TEXT,TEXT),accounting.assistant_mark_sent(TEXT,TEXT,TEXT),accounting.assistant_cancel(TEXT,TEXT) TO accounting_app;
GRANT EXECUTE ON FUNCTION accounting.assistant_execution_allowed(TEXT),accounting.assistant_check(TEXT,TEXT),accounting.assistant_recover(),accounting.assistant_claim(TEXT,INTEGER),accounting.assistant_mark_sent(TEXT,TEXT,TEXT),accounting.assistant_finish(TEXT,TEXT,TEXT,TEXT,TEXT,TEXT) TO accounting_worker;
