-- Changes enqueue company identifiers only. Dispatchers consume a bounded batch;
-- client principals never consume or peek at another firm's queue.
CREATE TABLE accounting.ops_company_dirty(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,dirty_at TEXT NOT NULL,revision BIGINT NOT NULL DEFAULT 1,
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
CREATE TABLE accounting.ops_company_refresh(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,last_refreshed_at TEXT,next_due_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
CREATE INDEX idx_ops_dirty_fair ON accounting.ops_company_dirty(dirty_at,firm_id,company_id);
CREATE INDEX idx_ops_refresh_due ON accounting.ops_company_refresh(next_due_at,firm_id,company_id);
CREATE FUNCTION accounting.mark_ops_company_dirty(target_firm TEXT,target_company TEXT) RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company) THEN RETURN; END IF;
 INSERT INTO accounting.ops_company_dirty(firm_id,company_id,dirty_at) VALUES(target_firm,target_company,clock_timestamp()::text)
 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO accounting.ops_company_refresh(firm_id,company_id,next_due_at) VALUES(target_firm,target_company,clock_timestamp()::text) ON CONFLICT DO NOTHING;
 END $$;
CREATE FUNCTION accounting.ops_mark_changed() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE scope RECORD; BEGIN
 IF TG_TABLE_NAME IN ('drafts','periods') THEN
  SELECT firm_id,company_id INTO scope FROM accounting.books WHERE id=NEW.book_id;
  PERFORM accounting.mark_ops_company_dirty(scope.firm_id,scope.company_id);
 ELSE
  PERFORM accounting.mark_ops_company_dirty(NEW.firm_id,NEW.company_id);
 END IF;
 RETURN NEW; END $$;
DO $$ DECLARE name TEXT; BEGIN
 FOREACH name IN ARRAY ARRAY['firm_clients','model_company_policies','model_costs','ocr_jobs','proposal_jobs','ops_exception_snapshots','case_tasks','delivery_receipts','drafts','periods'] LOOP
  EXECUTE format('CREATE TRIGGER ops_dirty_after_change AFTER INSERT OR UPDATE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ops_mark_changed()',name);
 END LOOP;
END $$;
INSERT INTO accounting.ops_company_dirty(firm_id,company_id,dirty_at) SELECT firm_id,company_id,clock_timestamp()::text FROM accounting.firm_clients WHERE status='active';
INSERT INTO accounting.ops_company_refresh(firm_id,company_id,next_due_at) SELECT firm_id,company_id,clock_timestamp()::text FROM accounting.firm_clients WHERE status='active';
ALTER TABLE accounting.ops_company_dirty ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ops_company_dirty FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.ops_company_refresh ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ops_company_refresh FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.ops_company_dirty,accounting.ops_company_refresh FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT ON accounting.ops_company_refresh TO accounting_app;
CREATE POLICY ops_refresh_read ON accounting.ops_company_refresh FOR SELECT TO accounting_app USING(accounting.ops_company_allowed(firm_id,company_id));

ALTER FUNCTION accounting.ops_company_metrics_internal(TEXT,TEXT,TEXT) RENAME TO ops_company_metrics_stage7;
CREATE FUNCTION accounting.ops_company_metrics_internal(target_firm TEXT,target_company TEXT,target_period TEXT) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE m JSONB; e JSONB; workflow JSONB; cases_open BIGINT; overdue BIGINT; oldest TEXT; risk TEXT; BEGIN
 m:=accounting.ops_company_metrics_stage7(target_firm,target_company,target_period);e:=m->'exceptions';
 SELECT count(*),count(*) FILTER(WHERE state='awaiting_info' AND due_on IS NOT NULL AND due_on<(clock_timestamp() AT TIME ZONE 'Asia/Shanghai')::date::text),min(created_at),md5(coalesce(string_agg(id||':'||version||':'||state,',' ORDER BY id),''))
 INTO cases_open,overdue,oldest,risk FROM accounting.case_tasks WHERE firm_id=target_firm AND company_id=target_company AND state<>'closed';
 workflow:=jsonb_build_object('open_cases',cases_open,'overdue_supplements',overdue,
  'exports_without_receipt',(SELECT count(*) FROM accounting.delivery_manifests m WHERE m.firm_id=target_firm AND m.company_id=target_company AND NOT EXISTS(SELECT 1 FROM accounting.delivery_receipts r WHERE r.export_id=m.export_id)),
  'latest_receipts_unknown',(SELECT count(*) FROM accounting.delivery_receipt_items i JOIN accounting.delivery_receipts r ON r.id=i.receipt_id WHERE r.firm_id=target_firm AND r.company_id=target_company AND i.raw_json::jsonb->>'status'='unknown' AND r.id=(SELECT r2.id FROM accounting.delivery_receipts r2 WHERE r2.export_id=r.export_id ORDER BY r2.created_at DESC,r2.id DESC LIMIT 1)),
  'latest_receipts_rejected',(SELECT count(*) FROM accounting.delivery_receipt_items i JOIN accounting.delivery_receipts r ON r.id=i.receipt_id WHERE r.firm_id=target_firm AND r.company_id=target_company AND i.raw_json::jsonb->>'status'='rejected' AND r.id=(SELECT r2.id FROM accounting.delivery_receipts r2 WHERE r2.export_id=r.export_id ORDER BY r2.created_at DESC,r2.id DESC LIMIT 1)));
 e:=e||jsonb_build_object('blocking_events',(e->>'blocking_events')::bigint+cases_open,'blocking_count',(e->>'blocking_count')::bigint+cases_open,
  'oldest_opened_at',CASE WHEN e->>'oldest_opened_at' IS NULL THEN oldest WHEN oldest IS NULL THEN e->>'oldest_opened_at' ELSE least(oldest,e->>'oldest_opened_at') END,
  'risk_fingerprint',md5(coalesce(e->>'risk_fingerprint','')||risk));
 RETURN m||jsonb_build_object('exceptions',e,'workflow',workflow); END $$;

CREATE FUNCTION accounting.ops_refresh_dirty(batch_limit INTEGER DEFAULT 20,deadline_limit INTEGER DEFAULT 5) RETURNS INTEGER LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE item RECORD; observed BIGINT; total INTEGER:=0; next_delay INTEGER; BEGIN
 IF NOT pg_has_role(session_user,'accounting_worker','MEMBER') THEN RAISE EXCEPTION 'Worker capability required' USING ERRCODE='42501'; END IF;
 IF batch_limit NOT BETWEEN 1 AND 100 OR deadline_limit NOT BETWEEN 0 AND 20 THEN RAISE EXCEPTION 'Invalid bounded monitor batch'; END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 FOR item IN SELECT d.firm_id,d.company_id,d.revision FROM accounting.ops_company_dirty d JOIN accounting.firm_clients f USING(firm_id,company_id) WHERE f.status='active' ORDER BY d.dirty_at,d.firm_id,d.company_id LIMIT batch_limit FOR UPDATE OF d LOOP
  PERFORM accounting.ops_refresh_company_alerts(item.firm_id,item.company_id);
  -- Refresh can itself age uncertain reservations and raise another dirty
  -- revision; those changes are reflected in the same fresh observation.
  DELETE FROM accounting.ops_company_dirty WHERE firm_id=item.firm_id AND company_id=item.company_id;
  UPDATE accounting.ops_company_refresh SET last_refreshed_at=clock_timestamp()::text,next_due_at=(clock_timestamp()+interval '30 seconds')::text WHERE firm_id=item.firm_id AND company_id=item.company_id;
  total:=total+1;
 END LOOP;
 FOR item IN SELECT r.firm_id,r.company_id FROM accounting.ops_company_refresh r JOIN accounting.firm_clients f USING(firm_id,company_id) WHERE f.status='active' AND r.next_due_at::timestamptz<=clock_timestamp() ORDER BY r.next_due_at,r.firm_id,r.company_id LIMIT deadline_limit FOR UPDATE OF r LOOP
  PERFORM accounting.ops_refresh_company_alerts(item.firm_id,item.company_id);
  DELETE FROM accounting.ops_company_dirty WHERE firm_id=item.firm_id AND company_id=item.company_id;
  UPDATE accounting.ops_company_refresh SET last_refreshed_at=clock_timestamp()::text,next_due_at=(clock_timestamp()+interval '30 seconds')::text WHERE firm_id=item.firm_id AND company_id=item.company_id;
  total:=total+1;
 END LOOP;
 RETURN total; END $$;
REVOKE ALL ON FUNCTION accounting.mark_ops_company_dirty(TEXT,TEXT),accounting.ops_mark_changed(),accounting.ops_company_metrics_internal(TEXT,TEXT,TEXT),accounting.ops_refresh_dirty(INTEGER,INTEGER) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.ops_refresh_dirty(INTEGER,INTEGER) TO accounting_worker;

-- Session time is explicit, bounded and server-clock-derived. No runtime role
-- can assign arbitrary accumulated seconds or change somebody else's record.
CREATE TABLE accounting.human_work_sessions(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,user_id TEXT NOT NULL REFERENCES accounting.users(id),
 book_id TEXT,event_id TEXT,activity TEXT NOT NULL CHECK(activity IN ('review','correction','supplement','month_close','business_entry')),
 state TEXT NOT NULL CHECK(state IN ('active','paused','completed')),credited_seconds INTEGER NOT NULL CHECK(credited_seconds BETWEEN 0 AND 28800),
 started_at TEXT NOT NULL,last_active_at TEXT NOT NULL,updated_at TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),
 start_key TEXT NOT NULL,start_fingerprint TEXT NOT NULL CHECK(length(start_fingerprint)=64),
 UNIQUE(firm_id,user_id,start_key),UNIQUE(id,firm_id,user_id),CHECK((book_id IS NULL)<>(event_id IS NULL)),
 CHECK((event_id IS NOT NULL AND activity='business_entry') OR (book_id IS NOT NULL AND activity<>'business_entry')),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES accounting.business_events(id,firm_id,company_id)
);
CREATE UNIQUE INDEX idx_human_one_active ON accounting.human_work_sessions(firm_id,user_id) WHERE state='active';
CREATE INDEX idx_human_work_scope ON accounting.human_work_sessions(firm_id,book_id,user_id,started_at);
CREATE TABLE accounting.human_work_commands(
 firm_id TEXT NOT NULL,user_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,session_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,response_json TEXT NOT NULL,
 created_at TEXT NOT NULL,PRIMARY KEY(firm_id,user_id,idempotency_key),FOREIGN KEY(session_id,firm_id,user_id) REFERENCES accounting.human_work_sessions(id,firm_id,user_id)
);
CREATE FUNCTION accounting.human_session_allowed(target_firm TEXT,target_company TEXT,target_book TEXT,target_event TEXT,target_user TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT target_firm=accounting.actor_firm() AND target_user=accounting.actor_user() AND accounting.actor_active() AND
 CASE WHEN target_book IS NOT NULL THEN accounting.ai_book_reviewer(target_book)
 ELSE accounting.business_member(target_firm,target_company) AND EXISTS(SELECT 1 FROM accounting.business_events e WHERE e.id=target_event AND e.firm_id=target_firm AND e.company_id=target_company AND e.applicant_id=target_user AND accounting.business_event_visible(e.id,e.firm_id,e.company_id)) END $$;
CREATE FUNCTION accounting.human_session_create_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NEW.state<>'active' OR NEW.credited_seconds<>0 OR NEW.version<>1 OR NEW.started_at<>NEW.updated_at OR NEW.started_at<>NEW.last_active_at OR abs(extract(epoch FROM clock_timestamp()-NEW.started_at::timestamptz))>10 THEN RAISE EXCEPTION 'A session begins at zero with a current server timestamp'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER human_session_create BEFORE INSERT ON accounting.human_work_sessions FOR EACH ROW EXECUTE FUNCTION accounting.human_session_create_guard();
CREATE FUNCTION accounting.advance_human_work_session(target_session TEXT,target_version INTEGER,target_action TEXT,target_key TEXT,target_fingerprint TEXT) RETURNS JSONB LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE s RECORD; prior RECORD; stamp TEXT; credit INTEGER:=0; next_state TEXT; result JSONB; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO s FROM accounting.human_work_sessions WHERE id=target_session AND firm_id=accounting.actor_firm() AND user_id=accounting.actor_user() FOR UPDATE;
 IF NOT FOUND OR NOT accounting.human_session_allowed(s.firm_id,s.company_id,s.book_id,s.event_id,s.user_id) THEN RAISE EXCEPTION 'Current session scope required' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM accounting.human_work_commands WHERE firm_id=s.firm_id AND user_id=s.user_id AND idempotency_key=target_key;
 IF FOUND THEN IF prior.request_fingerprint<>target_fingerprint THEN RAISE EXCEPTION 'Session idempotency conflict'; END IF; RETURN prior.response_json::jsonb; END IF;
 IF target_version<>s.version OR target_action NOT IN ('heartbeat','pause','resume','complete') OR (target_action IN ('heartbeat','pause') AND s.state<>'active') OR (target_action='resume' AND s.state<>'paused') OR (target_action='complete' AND s.state NOT IN ('active','paused')) OR length(target_key) NOT BETWEEN 1 AND 100 OR length(target_fingerprint)<>64 THEN RAISE EXCEPTION 'Session state/version conflict'; END IF;
 stamp:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 IF s.state='active' THEN credit:=least(60,greatest(0,floor(extract(epoch FROM stamp::timestamptz-s.last_active_at::timestamptz))::integer)); END IF;
 next_state:=CASE WHEN target_action IN ('heartbeat','resume') THEN 'active' WHEN target_action='pause' THEN 'paused' ELSE 'completed' END;
 IF s.credited_seconds+credit>=28800 AND next_state='active' THEN next_state:='paused'; END IF;
 UPDATE accounting.human_work_sessions SET state=next_state,credited_seconds=least(28800,s.credited_seconds+credit),last_active_at=stamp,updated_at=stamp,version=version+1 WHERE id=s.id RETURNING to_jsonb(human_work_sessions)-'firm_id'-'user_id'-'start_key'-'start_fingerprint' INTO result;
 INSERT INTO accounting.human_work_commands(firm_id,user_id,idempotency_key,session_id,request_fingerprint,response_json,created_at) VALUES(s.firm_id,s.user_id,target_key,s.id,target_fingerprint,result::text,stamp);
 RETURN result; END $$;
ALTER TABLE accounting.human_work_sessions ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.human_work_sessions FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.human_work_commands ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.human_work_commands FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.human_work_sessions,accounting.human_work_commands FROM PUBLIC,accounting_app,accounting_worker;
GRANT SELECT,INSERT ON accounting.human_work_sessions TO accounting_app;GRANT SELECT ON accounting.human_work_commands TO accounting_app;
CREATE POLICY human_sessions_read ON accounting.human_work_sessions FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND (accounting.human_session_allowed(firm_id,company_id,book_id,event_id,user_id) OR (book_id IS NOT NULL AND accounting.ai_book_reviewer(book_id))));
CREATE POLICY human_sessions_start ON accounting.human_work_sessions FOR INSERT TO accounting_app WITH CHECK(accounting.human_session_allowed(firm_id,company_id,book_id,event_id,user_id));
CREATE POLICY human_commands_read ON accounting.human_work_commands FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND user_id=accounting.actor_user() AND EXISTS(SELECT 1 FROM accounting.human_work_sessions s WHERE s.id=session_id AND accounting.human_session_allowed(s.firm_id,s.company_id,s.book_id,s.event_id,s.user_id)));
REVOKE ALL ON FUNCTION accounting.human_session_allowed(TEXT,TEXT,TEXT,TEXT,TEXT),accounting.human_session_create_guard(),accounting.advance_human_work_session(TEXT,INTEGER,TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.human_session_allowed(TEXT,TEXT,TEXT,TEXT,TEXT),accounting.advance_human_work_session(TEXT,INTEGER,TEXT,TEXT,TEXT) TO accounting_app;
