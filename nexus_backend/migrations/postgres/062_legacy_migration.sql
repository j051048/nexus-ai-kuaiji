-- Historical evidence is an isolated archive, never current sources/drafts.
CREATE TABLE accounting.migration_projects(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ledger_id TEXT NOT NULL,book_id TEXT NOT NULL,
 external_book_id TEXT NOT NULL CHECK(length(external_book_id) BETWEEN 1 AND 200),source_system_key TEXT NOT NULL CHECK(length(source_system_key) BETWEEN 1 AND 100),
 source_product TEXT NOT NULL,product_version TEXT NOT NULL DEFAULT '',history_start TEXT NOT NULL,history_end TEXT NOT NULL,cutover_date TEXT NOT NULL,
 currency TEXT NOT NULL DEFAULT 'CNY' CHECK(currency='CNY'),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 status TEXT NOT NULL DEFAULT 'collecting' CHECK(status IN ('collecting','processing','needs_attention','ready','accepted','active','cancelled')),
 active_validation_id TEXT,create_key TEXT NOT NULL,create_fingerprint TEXT NOT NULL CHECK(length(create_fingerprint)=64),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,firm_id,company_id),UNIQUE(firm_id,created_by,create_key),
 FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),
 CHECK(length(history_start)=10 AND history_start::date::text=history_start AND length(history_end)=10 AND history_end::date::text=history_end AND length(cutover_date)=10 AND cutover_date::date::text=cutover_date AND history_start<=history_end AND cutover_date::date=history_end::date+1 AND extract(day FROM history_start::date)=1 AND extract(day FROM cutover_date::date)=1)
);
CREATE TABLE accounting.migration_project_access(
 project_id TEXT NOT NULL REFERENCES accounting.migration_projects(id),user_id TEXT NOT NULL REFERENCES accounting.users(id),role TEXT NOT NULL CHECK(role IN ('viewer','reviewer','manager')),
 granted_by TEXT NOT NULL REFERENCES accounting.users(id),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_at TEXT NOT NULL,PRIMARY KEY(project_id,user_id)
);
CREATE TABLE accounting.migration_files(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 category TEXT NOT NULL CHECK(category IN ('package','accounts','auxiliaries','vouchers','balances','open_items','attachment')),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),storage_key TEXT NOT NULL UNIQUE CHECK(storage_key~'^[0-9a-f]{2}/[0-9a-f]{32}$'),sha256 TEXT NOT NULL CHECK(sha256~'^[0-9a-f]{64}$'),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 52428800),media_type TEXT NOT NULL,mapping_json TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(mapping_json::jsonb)='object'),
 create_key TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(project_id,create_key),UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id)
);
CREATE TABLE accounting.migration_mapping_versions(
 project_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(jsonb_typeof(payload_json::jsonb)='object'),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,PRIMARY KEY(project_id,version),UNIQUE(project_id,request_key),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id)
);
CREATE TABLE accounting.migration_jobs(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,file_id TEXT,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 phase TEXT NOT NULL CHECK(phase IN ('parse','validate')),status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','retry_wait','succeeded','failed','stale','cancelled')),
 execution_user_id TEXT NOT NULL REFERENCES accounting.users(id),expected_version INTEGER NOT NULL CHECK(expected_version>0),attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10 AND attempt_count<=max_attempts),available_at TEXT NOT NULL,
 lease_token TEXT,lease_until TEXT,worker_id TEXT,error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120),error_detail TEXT NOT NULL DEFAULT '' CHECK(length(error_detail)<=1000),
 checkpoint_json TEXT NOT NULL DEFAULT '{}' CHECK(jsonb_typeof(checkpoint_json::jsonb)='object'),processed_count INTEGER NOT NULL DEFAULT 0 CHECK(processed_count>=0),record_count INTEGER NOT NULL DEFAULT 0 CHECK(record_count>=0),
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(project_id,request_key),UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id),FOREIGN KEY(file_id,project_id,firm_id,company_id) REFERENCES accounting.migration_files(id,project_id,firm_id,company_id),
 CHECK((phase='parse' AND file_id IS NOT NULL) OR (phase='validate' AND file_id IS NULL)),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL) OR (status IN ('queued','retry_wait') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL) OR (status IN ('succeeded','failed','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX idx_migration_active_job ON accounting.migration_jobs(project_id,coalesce(file_id,''),phase) WHERE status IN ('queued','running','retry_wait');
CREATE INDEX idx_migration_dispatch ON accounting.migration_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE TABLE accounting.migration_records(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,file_id TEXT NOT NULL,job_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('account','auxiliary','voucher','balance','open_item')),external_id TEXT NOT NULL CHECK(length(external_id) BETWEEN 1 AND 200),period TEXT NOT NULL DEFAULT '',
 payload_json TEXT NOT NULL CHECK(jsonb_typeof(payload_json::jsonb)='object'),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),row_number INTEGER NOT NULL CHECK(row_number>0),created_at TEXT NOT NULL,
 UNIQUE(job_id,kind,external_id,period),UNIQUE(job_id,file_id,row_number,kind,external_id),
 FOREIGN KEY(file_id,project_id,firm_id,company_id) REFERENCES accounting.migration_files(id,project_id,firm_id,company_id),FOREIGN KEY(job_id,project_id,firm_id,company_id) REFERENCES accounting.migration_jobs(id,project_id,firm_id,company_id)
);
CREATE INDEX idx_migration_records_page ON accounting.migration_records(project_id,kind,period,external_id,id);
CREATE TABLE accounting.migration_validations(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,job_id TEXT NOT NULL UNIQUE,input_version INTEGER NOT NULL CHECK(input_version>0),mapping_version INTEGER NOT NULL CHECK(mapping_version>=0),
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,basis_token TEXT NOT NULL,report_json TEXT NOT NULL CHECK(jsonb_typeof(report_json::jsonb)='object'),
 blocking_count INTEGER NOT NULL CHECK(blocking_count>=0),warning_count INTEGER NOT NULL CHECK(warning_count>=0),created_at TEXT NOT NULL,
 CHECK(coalesce(jsonb_typeof(report_json::jsonb->'blocking_count')='number' AND (report_json::jsonb->>'blocking_count')::integer=blocking_count AND jsonb_typeof(report_json::jsonb->'warning_count')='number' AND (report_json::jsonb->>'warning_count')::integer=warning_count AND jsonb_typeof(report_json::jsonb->'issues')='array' AND jsonb_typeof(report_json::jsonb->'coverage')='object',false)),
 UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id),FOREIGN KEY(job_id,project_id,firm_id,company_id) REFERENCES accounting.migration_jobs(id,project_id,firm_id,company_id)
);
CREATE TABLE accounting.migration_acceptances(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,validation_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 expected_version INTEGER NOT NULL CHECK(expected_version>0),basis_token TEXT NOT NULL,request_key TEXT NOT NULL,
 accepted_by TEXT NOT NULL REFERENCES accounting.users(id),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_at TEXT NOT NULL,
 UNIQUE(project_id,request_key),UNIQUE(id,project_id,firm_id,company_id),FOREIGN KEY(validation_id,project_id,firm_id,company_id) REFERENCES accounting.migration_validations(id,project_id,firm_id,company_id)
);
CREATE TABLE accounting.migration_cutovers(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL UNIQUE,validation_id TEXT NOT NULL,acceptance_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 payload_json TEXT NOT NULL CHECK(jsonb_typeof(payload_json::jsonb)='object'),request_key TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(project_id,request_key),FOREIGN KEY(validation_id,project_id,firm_id,company_id) REFERENCES accounting.migration_validations(id,project_id,firm_id,company_id),FOREIGN KEY(acceptance_id,project_id,firm_id,company_id) REFERENCES accounting.migration_acceptances(id,project_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.migration_company_schedule(firm_id TEXT NOT NULL,company_id TEXT NOT NULL,last_dispatch_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_dispatch_seq>=0),PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id));
CREATE INDEX idx_migration_projects_scope ON accounting.migration_projects(firm_id,company_id,status,updated_at DESC,id);
CREATE INDEX idx_migration_files_project ON accounting.migration_files(project_id,created_at,id);
CREATE INDEX idx_migration_jobs_project ON accounting.migration_jobs(project_id,created_at DESC,id);
CREATE TABLE accounting.migration_file_replacements(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,old_file_id TEXT NOT NULL UNIQUE,new_file_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 request_key TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 1000),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(project_id,request_key),CHECK(old_file_id<>new_file_id),FOREIGN KEY(old_file_id,project_id,firm_id,company_id) REFERENCES accounting.migration_files(id,project_id,firm_id,company_id),FOREIGN KEY(new_file_id,project_id,firm_id,company_id) REFERENCES accounting.migration_files(id,project_id,firm_id,company_id)
);

CREATE FUNCTION accounting.migration_book_member(target TEXT,target_user TEXT,review_access BOOLEAN DEFAULT false) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM accounting.books b JOIN accounting.firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN accounting.users u ON u.id=target_user AND u.active=1
 LEFT JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN accounting.company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN accounting.book_access a ON a.book_id=b.id AND a.firm_id=b.firm_id AND a.user_id=u.id
 WHERE b.id=target AND (m.role IN ('owner','admin') OR (a.user_id IS NOT NULL AND
 ((m.role IS NOT NULL AND (NOT review_access OR (m.role='reviewer' AND a.role='reviewer')))
 OR (m.role IS NULL AND cm.role IN ('owner','admin','finance') AND (NOT review_access OR (cm.role='finance' AND a.role='reviewer')))))))
$$;
CREATE FUNCTION accounting.migration_allowed(target TEXT,required_role TEXT DEFAULT 'viewer') RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT required_role IN ('viewer','reviewer','manager') AND accounting.actor_active() AND EXISTS(
 SELECT 1 FROM accounting.migration_projects p WHERE p.id=target AND p.firm_id=accounting.actor_firm()
 AND accounting.book_allowed(p.book_id) AND
 ((accounting.firm_manager(p.firm_id) AND accounting.ledger_manage(p.ledger_id) AND accounting.catalog_manage(p.book_id))
 OR ((EXISTS(SELECT 1 FROM accounting.migration_project_access a WHERE a.project_id=p.id AND a.user_id=accounting.actor_user()
 AND (required_role='viewer' OR a.role='manager' OR (required_role='reviewer' AND a.role='reviewer'))))
 AND (required_role='viewer' OR accounting.catalog_manage(p.book_id))
 AND (required_role<>'manager' OR accounting.ledger_manage(p.ledger_id)))))
$$;
CREATE FUNCTION accounting.migration_manage(target TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT accounting.migration_allowed(target,'manager') $$;
CREATE FUNCTION accounting.migration_bootstrap(target TEXT,target_user TEXT,target_role TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_user=accounting.actor_user() AND target_role='manager' AND EXISTS(SELECT 1 FROM accounting.migration_projects p WHERE p.id=target AND p.created_by=accounting.actor_user() AND p.firm_id=accounting.actor_firm() AND accounting.catalog_manage(p.book_id) AND accounting.ledger_manage(p.ledger_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_project_access a WHERE a.project_id=p.id))
$$;
CREATE FUNCTION accounting.migration_basis(target TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT encode(sha256(convert_to(jsonb_build_object('project',jsonb_build_array(p.id,p.version,p.ledger_id,p.book_id,p.external_book_id,p.source_system_key,p.history_start,p.history_end,p.cutover_date,p.currency),
 'files',coalesce((SELECT jsonb_agg(jsonb_build_array(f.id,f.category,f.sha256,f.mapping_json::jsonb) ORDER BY f.id) FROM accounting.migration_files f WHERE f.project_id=p.id),'[]'::jsonb),
 'mapping',coalesce((SELECT jsonb_build_array(m.version,m.payload_json::jsonb) FROM accounting.migration_mapping_versions m WHERE m.project_id=p.id ORDER BY m.version DESC LIMIT 1),'[]'::jsonb),
 'parsed',coalesce((SELECT jsonb_agg(jsonb_build_array(q.id,q.file_id,q.record_count) ORDER BY q.file_id) FROM accounting.migration_jobs q WHERE q.project_id=p.id AND q.phase='parse' AND q.status='succeeded' AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_jobs q2 WHERE q2.project_id=p.id AND q2.file_id=q.file_id AND q2.phase='parse' AND q2.status='succeeded' AND (q2.created_at,q2.id)>(q.created_at,q.id))),'[]'::jsonb),
 'records',coalesce((SELECT jsonb_agg(jsonb_build_array(r.job_id,r.id,r.fingerprint) ORDER BY r.job_id,r.id) FROM accounting.migration_records r JOIN accounting.migration_jobs q ON q.id=r.job_id WHERE r.project_id=p.id AND q.status='succeeded' AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_jobs q2 WHERE q2.project_id=p.id AND q2.file_id=q.file_id AND q2.phase='parse' AND q2.status='succeeded' AND (q2.created_at,q2.id)>(q.created_at,q.id))),'[]'::jsonb),
 'accounts',coalesce((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.code) FROM accounting.accounts a WHERE a.book_id=p.book_id),'[]'::jsonb),
 'masters',coalesce((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) FROM accounting.company_master_entities e WHERE e.firm_id=p.firm_id AND e.company_id=p.company_id),'[]'::jsonb),
 'aliases',coalesce((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM accounting.company_master_aliases a WHERE a.firm_id=p.firm_id AND a.company_id=p.company_id),'[]'::jsonb),
 'replacements',coalesce((SELECT jsonb_agg(jsonb_build_array(x.old_file_id,x.new_file_id) ORDER BY x.id) FROM accounting.migration_file_replacements x WHERE x.project_id=p.id),'[]'::jsonb))::text,'UTF8')),'hex')
 FROM accounting.migration_projects p WHERE p.id=target AND accounting.migration_allowed(p.id)
$$;
CREATE FUNCTION accounting.migration_execution_allowed(target_job TEXT) RETURNS BOOLEAN
 LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.migration_jobs;p accounting.migration_projects;old_user TEXT;old_firm TEXT;result BOOLEAN;BEGIN
 SELECT * INTO j FROM accounting.migration_jobs WHERE id=target_job;IF j.id IS NULL THEN RETURN false;END IF;
 SELECT * INTO p FROM accounting.migration_projects WHERE id=j.project_id;
 old_user:=accounting.actor_user();old_firm:=accounting.actor_firm();
 PERFORM set_config('app.user_id',j.execution_user_id,true),set_config('app.firm_id',j.firm_id,true);
 result:=accounting.migration_manage(j.project_id) AND p.version=j.expected_version AND p.status NOT IN ('accepted','active','cancelled') AND (j.phase<>'parse' OR NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements x WHERE x.old_file_id=j.file_id));
 PERFORM set_config('app.user_id',old_user,true),set_config('app.firm_id',old_firm,true);RETURN result;
 END $$;
CREATE FUNCTION accounting.migration_worker_login() RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolname=session_user AND NOT r.rolsuper AND NOT r.rolbypassrls AND pg_has_role(session_user,'accounting_worker','MEMBER'))
$$;
CREATE FUNCTION accounting.migration_live_write(target_job TEXT,target_phase TEXT) RETURNS BOOLEAN LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.migration_worker_login() AND EXISTS(SELECT 1 FROM accounting.migration_jobs j WHERE j.id=target_job
 AND j.id=current_setting('app.migration_job_id',true) AND j.lease_token=current_setting('app.migration_token',true)
 AND j.execution_user_id=accounting.actor_user() AND j.firm_id=accounting.actor_firm() AND j.phase=target_phase AND j.status='running'
 AND j.lease_until::timestamptz>clock_timestamp() AND accounting.migration_execution_allowed(j.id))
$$;

CREATE FUNCTION accounting.migration_project_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'collecting' OR NEW.version<>1 OR NEW.active_validation_id IS NOT NULL OR NEW.created_by<>accounting.actor_user()
  OR NOT accounting.catalog_manage(NEW.book_id) OR NOT accounting.ledger_manage(NEW.ledger_id)
  OR NOT EXISTS(SELECT 1 FROM accounting.ledger_periods l JOIN accounting.books b ON b.id=l.book_id JOIN accounting.periods q ON q.book_id=b.id AND q.period=b.period WHERE l.ledger_id=NEW.ledger_id AND l.book_id=NEW.book_id AND q.status='open' AND left(NEW.cutover_date,7)=b.period)
  OR EXISTS(SELECT 1 FROM accounting.migration_projects p WHERE p.firm_id=NEW.firm_id AND p.source_system_key=NEW.source_system_key AND p.external_book_id=NEW.external_book_id AND (p.ledger_id<>NEW.ledger_id OR p.company_id<>NEW.company_id))
  THEN RAISE EXCEPTION 'Migration target requires an open reviewed ledger and stable external identity' USING ERRCODE='23514';END IF;
 ELSE
  IF (NEW.id,NEW.firm_id,NEW.company_id,NEW.ledger_id,NEW.book_id,NEW.external_book_id,NEW.source_system_key,NEW.source_product,NEW.product_version,NEW.history_start,NEW.history_end,NEW.cutover_date,NEW.currency,NEW.created_by,NEW.created_at,NEW.create_key,NEW.create_fingerprint)
  IS DISTINCT FROM (OLD.id,OLD.firm_id,OLD.company_id,OLD.ledger_id,OLD.book_id,OLD.external_book_id,OLD.source_system_key,OLD.source_product,OLD.product_version,OLD.history_start,OLD.history_end,OLD.cutover_date,OLD.currency,OLD.created_by,OLD.created_at,OLD.create_key,OLD.create_fingerprint)
  OR NEW.version NOT IN (OLD.version,OLD.version+1) OR OLD.status IN ('active','cancelled') THEN RAISE EXCEPTION 'Migration identity is immutable' USING ERRCODE='23514';END IF;
  IF current_user='accounting_app' AND (NOT accounting.migration_manage(OLD.id) OR NEW.status NOT IN ('collecting','processing','cancelled') OR NEW.active_validation_id IS NOT NULL OR OLD.status='accepted') THEN RAISE EXCEPTION 'API cannot publish validation, acceptance or cutover' USING ERRCODE='42501';END IF;
 END IF;RETURN NEW;
 END $$;
CREATE TRIGGER migration_project_scope BEFORE INSERT OR UPDATE ON accounting.migration_projects FOR EACH ROW EXECUTE FUNCTION accounting.migration_project_guard();
CREATE FUNCTION accounting.migration_access_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects;BEGIN
 SELECT * INTO p FROM accounting.migration_projects WHERE id=CASE WHEN TG_OP='DELETE' THEN OLD.project_id ELSE NEW.project_id END;
 IF NOT accounting.migration_manage(p.id) AND NOT (TG_OP='INSERT' AND accounting.migration_bootstrap(p.id,NEW.user_id,NEW.role)) THEN RAISE EXCEPTION 'Migration grant requires project manager' USING ERRCODE='42501';END IF;
 IF TG_OP<>'DELETE' AND (NEW.granted_by<>accounting.actor_user() OR NOT accounting.migration_book_member(p.book_id,NEW.user_id,NEW.role<>'viewer')
 OR (NEW.role='manager' AND NOT EXISTS(SELECT 1 FROM accounting.ledger_managers WHERE ledger_id=p.ledger_id AND user_id=NEW.user_id))) THEN RAISE EXCEPTION 'Migration grant never creates book or ledger permissions' USING ERRCODE='42501';END IF;
 IF TG_OP='UPDATE' AND (NEW.project_id,NEW.user_id) IS DISTINCT FROM (OLD.project_id,OLD.user_id) THEN RAISE EXCEPTION 'Migration grant identity is immutable';END IF;
 RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;END $$;
CREATE TRIGGER migration_access_scope BEFORE INSERT OR UPDATE OR DELETE ON accounting.migration_project_access FOR EACH ROW EXECUTE FUNCTION accounting.migration_access_guard();
CREATE FUNCTION accounting.migration_input_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects;BEGIN SELECT * INTO p FROM accounting.migration_projects WHERE id=NEW.project_id;
 IF NOT accounting.migration_manage(p.id) OR p.status IN ('accepted','active','cancelled') OR NEW.created_by<>accounting.actor_user() THEN RAISE EXCEPTION 'Migration input requires current collecting manager' USING ERRCODE='42501';END IF;
 IF TG_TABLE_NAME='migration_mapping_versions' THEN
  IF NEW.version<>(SELECT coalesce(max(version),0)+1 FROM accounting.migration_mapping_versions WHERE project_id=NEW.project_id) THEN RAISE EXCEPTION 'Mapping versions must append consecutively';END IF;
 END IF;
 RETURN NEW;END $$;
CREATE TRIGGER migration_file_scope BEFORE INSERT ON accounting.migration_files FOR EACH ROW EXECUTE FUNCTION accounting.migration_input_guard();
CREATE TRIGGER migration_mapping_scope BEFORE INSERT ON accounting.migration_mapping_versions FOR EACH ROW EXECUTE FUNCTION accounting.migration_input_guard();
CREATE FUNCTION accounting.migration_job_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects;BEGIN
 SELECT * INTO p FROM accounting.migration_projects WHERE id=NEW.project_id;
 IF TG_OP='INSERT' AND (NEW.status<>'queued' OR NEW.execution_user_id<>accounting.actor_user() OR NEW.expected_version<>p.version OR NEW.attempt_count<>0 OR NEW.max_attempts<>3 OR NEW.processed_count<>0 OR NEW.record_count<>0 OR NEW.checkpoint_json::jsonb<>'{}'::jsonb OR NEW.error_code<>'' OR NEW.error_detail<>'' OR NOT accounting.migration_manage(p.id) OR p.status IN ('accepted','active','cancelled') OR (NEW.phase='parse' AND (EXISTS(SELECT 1 FROM accounting.migration_files f WHERE f.id=NEW.file_id AND f.category='attachment') OR EXISTS(SELECT 1 FROM accounting.migration_file_replacements x WHERE x.old_file_id=NEW.file_id)))) THEN RAISE EXCEPTION 'API may only enqueue current authorized migration inputs' USING ERRCODE='23514';END IF;
 IF TG_OP='UPDATE' THEN
 IF (NEW.id,NEW.project_id,NEW.file_id,NEW.firm_id,NEW.company_id,NEW.phase,NEW.execution_user_id,NEW.request_key,NEW.request_fingerprint,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.project_id,OLD.file_id,OLD.firm_id,OLD.company_id,OLD.phase,OLD.execution_user_id,OLD.request_key,OLD.request_fingerprint,OLD.created_at) THEN RAISE EXCEPTION 'Migration execution principal and origin are immutable';END IF;
 IF current_user='accounting_app' AND NOT accounting.migration_worker_login() AND (OLD.status NOT IN ('failed','stale','cancelled') OR NEW.status<>'queued' OR NEW.execution_user_id<>accounting.actor_user() OR NEW.expected_version<>p.version OR NEW.attempt_count<>OLD.attempt_count OR NEW.max_attempts<>OLD.attempt_count+3 OR NEW.processed_count<>OLD.processed_count OR NEW.record_count<>OLD.record_count OR NEW.checkpoint_json<>OLD.checkpoint_json OR NOT accounting.migration_manage(p.id)) THEN RAISE EXCEPTION 'API may only request a bounded retry without replacing executor' USING ERRCODE='23514';END IF;
 IF current_user='accounting_app' AND accounting.migration_worker_login() AND (NEW.status<>OLD.status OR NEW.lease_token IS DISTINCT FROM OLD.lease_token OR NEW.lease_until IS DISTINCT FROM OLD.lease_until OR NEW.worker_id IS DISTINCT FROM OLD.worker_id OR NOT accounting.migration_live_write(OLD.id,OLD.phase) OR NEW.expected_version<>OLD.expected_version OR NEW.processed_count<OLD.processed_count OR NEW.record_count<OLD.record_count) THEN RAISE EXCEPTION 'Worker progress requires a live delegated lease' USING ERRCODE='23514';END IF;
 END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_job_scope BEFORE INSERT OR UPDATE ON accounting.migration_jobs FOR EACH ROW EXECUTE FUNCTION accounting.migration_job_guard();
CREATE FUNCTION accounting.migration_output_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.migration_jobs;p accounting.migration_projects;BEGIN
 SELECT * INTO j FROM accounting.migration_jobs WHERE id=NEW.job_id;SELECT * INTO p FROM accounting.migration_projects WHERE id=NEW.project_id;
 IF NOT accounting.migration_live_write(j.id,CASE WHEN TG_TABLE_NAME='migration_records' THEN 'parse' ELSE 'validate' END) OR j.project_id<>NEW.project_id OR j.firm_id<>NEW.firm_id OR j.company_id<>NEW.company_id THEN RAISE EXCEPTION 'Historical output requires live actual worker lease and current delegation' USING ERRCODE='42501';END IF;
 IF TG_TABLE_NAME='migration_records' THEN
 IF NEW.file_id<>j.file_id THEN RAISE EXCEPTION 'Historical record file must match parse generation';END IF;
 ELSE
 IF NEW.input_version<>p.version OR NEW.mapping_version<>(SELECT coalesce(max(version),0) FROM accounting.migration_mapping_versions WHERE project_id=p.id) OR NEW.basis_token IS DISTINCT FROM accounting.migration_basis(p.id) THEN RAISE EXCEPTION 'Historical validation basis is stale' USING ERRCODE='23514';END IF;
 END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_record_scope BEFORE INSERT ON accounting.migration_records FOR EACH ROW EXECUTE FUNCTION accounting.migration_output_guard();
CREATE TRIGGER migration_validation_scope BEFORE INSERT ON accounting.migration_validations FOR EACH ROW EXECUTE FUNCTION accounting.migration_output_guard();

CREATE FUNCTION accounting.migration_acceptance_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects;v accounting.migration_validations;BEGIN
 SELECT * INTO p FROM accounting.migration_projects WHERE id=NEW.project_id FOR UPDATE;SELECT * INTO v FROM accounting.migration_validations WHERE id=NEW.validation_id;
 IF NEW.accepted_by<>accounting.actor_user() OR NOT accounting.migration_allowed(p.id,'reviewer') OR NEW.accepted_by=p.created_by
 OR NEW.accepted_by=coalesce((SELECT created_by FROM accounting.migration_mapping_versions WHERE project_id=p.id ORDER BY version DESC LIMIT 1),p.created_by)
 OR p.status<>'ready' OR p.active_validation_id IS DISTINCT FROM v.id OR v.project_id<>p.id OR v.firm_id<>NEW.firm_id OR v.company_id<>NEW.company_id OR v.blocking_count<>0 OR v.input_version<>p.version OR NEW.expected_version<>p.version
 OR v.basis_token IS DISTINCT FROM NEW.basis_token OR v.basis_token IS DISTINCT FROM accounting.migration_basis(p.id) OR NOT coalesce((v.report_json::jsonb->'coverage'->>'complete')::boolean,false)
 OR NOT EXISTS(SELECT 1 FROM accounting.migration_jobs WHERE id=v.job_id AND status='succeeded' AND phase='validate')
 THEN RAISE EXCEPTION 'Acceptance requires current complete validation and an independent reviewer' USING ERRCODE='23514';END IF;
 UPDATE accounting.migration_projects SET status='accepted',updated_at=NEW.created_at WHERE id=p.id;RETURN NEW;END $$;
CREATE TRIGGER migration_acceptance_scope BEFORE INSERT ON accounting.migration_acceptances FOR EACH ROW EXECUTE FUNCTION accounting.migration_acceptance_guard();
CREATE FUNCTION accounting.migration_cutover_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects;v accounting.migration_validations;a accounting.migration_acceptances;BEGIN
 SELECT * INTO p FROM accounting.migration_projects WHERE id=NEW.project_id FOR UPDATE;SELECT * INTO v FROM accounting.migration_validations WHERE id=NEW.validation_id;SELECT * INTO a FROM accounting.migration_acceptances WHERE id=NEW.acceptance_id;
 IF NEW.created_by<>accounting.actor_user() OR NOT accounting.migration_manage(p.id) OR p.status<>'accepted' OR NEW.book_id<>p.book_id OR NEW.firm_id<>p.firm_id OR NEW.company_id<>p.company_id
 OR p.active_validation_id IS DISTINCT FROM v.id OR a.project_id<>p.id OR a.validation_id<>v.id OR a.expected_version<>p.version OR v.basis_token IS DISTINCT FROM a.basis_token OR v.basis_token IS DISTINCT FROM accounting.migration_basis(p.id)
 OR v.blocking_count<>0 OR NOT EXISTS(SELECT 1 FROM accounting.periods q JOIN accounting.books b ON b.id=q.book_id AND b.period=q.period WHERE b.id=p.book_id AND q.status='open')
 OR EXISTS(SELECT 1 FROM accounting.sources WHERE book_id=p.book_id) OR EXISTS(SELECT 1 FROM accounting.drafts WHERE book_id=p.book_id) OR EXISTS(SELECT 1 FROM accounting.exports WHERE book_id=p.book_id)
 THEN RAISE EXCEPTION 'Cutover requires fresh accepted validation, current manager and empty open target' USING ERRCODE='23514';END IF;
 UPDATE accounting.migration_projects SET status='active',updated_at=NEW.created_at WHERE id=p.id;RETURN NEW;END $$;
CREATE TRIGGER migration_cutover_scope BEFORE INSERT ON accounting.migration_cutovers FOR EACH ROW EXECUTE FUNCTION accounting.migration_cutover_guard();

DO $$ DECLARE item TEXT;BEGIN
 FOREACH item IN ARRAY ARRAY['migration_projects','migration_project_access','migration_files','migration_mapping_versions','migration_jobs','migration_records','migration_validations','migration_acceptances','migration_cutovers','migration_company_schedule'] LOOP
 EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 END LOOP;
 FOREACH item IN ARRAY ARRAY['migration_files','migration_mapping_versions','migration_records','migration_validations','migration_acceptances','migration_cutovers'] LOOP
 EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',item||'_immutable',item);
 END LOOP;END $$;
CREATE TRIGGER migration_projects_no_delete BEFORE DELETE ON accounting.migration_projects FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE TRIGGER migration_jobs_no_delete BEFORE DELETE ON accounting.migration_jobs FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
GRANT SELECT,INSERT,UPDATE ON accounting.migration_projects,accounting.migration_project_access TO accounting_app;
GRANT DELETE ON accounting.migration_project_access TO accounting_app;
GRANT SELECT,INSERT ON accounting.migration_files,accounting.migration_mapping_versions,accounting.migration_records,accounting.migration_validations,accounting.migration_acceptances,accounting.migration_cutovers TO accounting_app;
GRANT SELECT(id,project_id,file_id,firm_id,company_id,phase,status,execution_user_id,expected_version,attempt_count,max_attempts,available_at,error_code,error_detail,checkpoint_json,processed_count,record_count,request_key,request_fingerprint,created_at,updated_at,finished_at),INSERT,UPDATE ON accounting.migration_jobs TO accounting_app;
CREATE POLICY migration_projects_read ON accounting.migration_projects FOR SELECT TO accounting_app USING(accounting.migration_allowed(id));
CREATE POLICY migration_projects_create ON accounting.migration_projects FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.catalog_manage(book_id) AND accounting.ledger_manage(ledger_id));
CREATE POLICY migration_projects_write ON accounting.migration_projects FOR UPDATE TO accounting_app USING(accounting.migration_manage(id)) WITH CHECK(accounting.migration_manage(id));
CREATE POLICY migration_access_read ON accounting.migration_project_access FOR SELECT TO accounting_app USING(accounting.migration_manage(project_id) OR (user_id=accounting.actor_user() AND accounting.migration_allowed(project_id)));
CREATE POLICY migration_access_create ON accounting.migration_project_access FOR INSERT TO accounting_app WITH CHECK(accounting.migration_manage(project_id) OR accounting.migration_bootstrap(project_id,user_id,role));
CREATE POLICY migration_access_update ON accounting.migration_project_access FOR UPDATE TO accounting_app USING(accounting.migration_manage(project_id)) WITH CHECK(accounting.migration_manage(project_id));
CREATE POLICY migration_access_delete ON accounting.migration_project_access FOR DELETE TO accounting_app USING(accounting.migration_manage(project_id));
DO $$ DECLARE item TEXT;BEGIN
 FOREACH item IN ARRAY ARRAY['migration_files','migration_mapping_versions','migration_jobs','migration_records','migration_validations','migration_acceptances','migration_cutovers'] LOOP
 EXECUTE format('CREATE POLICY %I ON accounting.%I FOR SELECT TO accounting_app USING(accounting.migration_allowed(project_id))',item||'_read',item);
 END LOOP;
 FOREACH item IN ARRAY ARRAY['migration_files','migration_mapping_versions','migration_jobs'] LOOP
 EXECUTE format('CREATE POLICY %I ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(accounting.migration_manage(project_id))',item||'_create',item);
 END LOOP;END $$;
CREATE POLICY migration_jobs_update ON accounting.migration_jobs FOR UPDATE TO accounting_app USING(accounting.migration_manage(project_id)) WITH CHECK(accounting.migration_manage(project_id));
CREATE POLICY migration_records_create ON accounting.migration_records FOR INSERT TO accounting_app WITH CHECK(accounting.migration_live_write(job_id,'parse'));
CREATE POLICY migration_validations_create ON accounting.migration_validations FOR INSERT TO accounting_app WITH CHECK(accounting.migration_live_write(job_id,'validate'));
CREATE POLICY migration_acceptances_create ON accounting.migration_acceptances FOR INSERT TO accounting_app WITH CHECK(accounting.migration_allowed(project_id,'reviewer'));
CREATE POLICY migration_cutovers_create ON accounting.migration_cutovers FOR INSERT TO accounting_app WITH CHECK(accounting.migration_manage(project_id));

CREATE FUNCTION accounting.migration_recover() RETURNS INTEGER LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.migration_jobs;outcome TEXT;n INTEGER:=0;stamp TEXT;BEGIN
 IF NOT accounting.migration_worker_login() THEN RAISE EXCEPTION 'Dedicated worker login required' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);stamp:=accounting.ocr_utc_now();
 FOR j IN SELECT * FROM accounting.migration_jobs WHERE status='running' AND lease_until::timestamptz<=clock_timestamp() ORDER BY created_at,id FOR UPDATE LOOP
 outcome:=CASE WHEN NOT accounting.migration_execution_allowed(j.id) THEN 'stale' WHEN j.attempt_count>=j.max_attempts THEN 'failed' ELSE 'retry_wait' END;
 UPDATE accounting.migration_jobs SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='lease_expired',error_detail='后台迁移任务已恢复，旧响应不会发布',updated_at=stamp,available_at=stamp,finished_at=CASE WHEN outcome='retry_wait' THEN NULL ELSE stamp END WHERE id=j.id;n:=n+1;
 END LOOP;RETURN n;END $$;
CREATE FUNCTION accounting.migration_claim(target_worker TEXT,lease_seconds INTEGER DEFAULT 120) RETURNS SETOF accounting.migration_jobs LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.migration_jobs;stamp TEXT;seq BIGINT;BEGIN
 IF NOT accounting.migration_worker_login() OR length(btrim(target_worker)) NOT BETWEEN 1 AND 120 OR lease_seconds NOT BETWEEN 10 AND 600 THEN RAISE EXCEPTION 'Dedicated worker and bounded lease required' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);PERFORM accounting.migration_recover();
 IF (SELECT count(*) FROM accounting.migration_jobs WHERE status='running')>=2 THEN RETURN;END IF;
 INSERT INTO accounting.migration_company_schedule(firm_id,company_id) SELECT DISTINCT firm_id,company_id FROM accounting.migration_jobs WHERE status IN ('queued','retry_wait') ON CONFLICT DO NOTHING;
 FOR j IN SELECT q.* FROM accounting.migration_jobs q JOIN accounting.migration_company_schedule s ON s.firm_id=q.firm_id AND s.company_id=q.company_id
 WHERE q.status IN ('queued','retry_wait') AND q.available_at::timestamptz<=clock_timestamp() AND NOT EXISTS(SELECT 1 FROM accounting.migration_jobs a WHERE a.firm_id=q.firm_id AND a.company_id=q.company_id AND a.status='running')
 ORDER BY s.last_dispatch_seq,q.created_at,q.id FOR UPDATE OF q LOOP
 stamp:=accounting.ocr_utc_now();
 IF NOT accounting.migration_execution_allowed(j.id) THEN UPDATE accounting.migration_jobs SET status='stale',error_code='scope_changed',error_detail='迁移版本或执行人员权限已经改变',updated_at=stamp,finished_at=stamp WHERE id=j.id;CONTINUE;END IF;
 IF j.attempt_count>=j.max_attempts THEN UPDATE accounting.migration_jobs SET status='failed',error_code='attempts_exhausted',updated_at=stamp,finished_at=stamp WHERE id=j.id;CONTINUE;END IF;
 SELECT coalesce(max(last_dispatch_seq),0)+1 INTO seq FROM accounting.migration_company_schedule;
 UPDATE accounting.migration_company_schedule SET last_dispatch_seq=seq WHERE firm_id=j.firm_id AND company_id=j.company_id;
 UPDATE accounting.migration_jobs SET status='running',attempt_count=attempt_count+1,lease_token=md5(random()::text||clock_timestamp()::text||j.id),lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),worker_id=target_worker,updated_at=stamp,error_code='',error_detail='' WHERE id=j.id RETURNING * INTO j;
 UPDATE accounting.migration_projects SET status='processing',active_validation_id=NULL,updated_at=stamp WHERE id=j.project_id;
 RETURN NEXT j;RETURN;
 END LOOP;END $$;
CREATE FUNCTION accounting.migration_check(target_job TEXT,target_token TEXT) RETURNS BOOLEAN LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.migration_worker_login() AND EXISTS(SELECT 1 FROM accounting.migration_jobs j WHERE j.id=target_job AND j.lease_token=target_token AND j.status='running' AND j.lease_until::timestamptz>clock_timestamp() AND accounting.migration_execution_allowed(j.id))
$$;
CREATE FUNCTION accounting.migration_heartbeat(target_job TEXT,target_token TEXT,target_checkpoint TEXT,target_processed INTEGER,target_records INTEGER,lease_seconds INTEGER DEFAULT 120) RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.migration_jobs;BEGIN
 IF lease_seconds NOT BETWEEN 10 AND 600 OR jsonb_typeof(target_checkpoint::jsonb)<>'object' OR target_processed<0 OR target_records<0 THEN RAISE EXCEPTION 'Invalid checkpoint or lease bounds';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 IF NOT accounting.migration_check(target_job,target_token) THEN RETURN false;END IF;
 SELECT * INTO j FROM accounting.migration_jobs WHERE id=target_job FOR UPDATE;
 IF target_processed<j.processed_count OR target_records<j.record_count OR (j.phase='parse' AND target_records<>(SELECT count(*) FROM accounting.migration_records WHERE job_id=j.id)) THEN RETURN false;END IF;
 UPDATE accounting.migration_jobs SET checkpoint_json=target_checkpoint,processed_count=target_processed,record_count=target_records,lease_until=to_char((clock_timestamp()+make_interval(secs=>lease_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'),updated_at=accounting.ocr_utc_now() WHERE id=j.id;RETURN true;END $$;
CREATE FUNCTION accounting.migration_finish(target_job TEXT,target_token TEXT,target_status TEXT,target_error_code TEXT DEFAULT '',target_error_detail TEXT DEFAULT '',retry_delay_seconds INTEGER DEFAULT 0) RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.migration_jobs;v accounting.migration_validations;outcome TEXT;stamp TEXT;old_user TEXT;old_firm TEXT;BEGIN
 IF target_status NOT IN ('succeeded','failed','stale','cancelled','retry_wait') OR retry_delay_seconds NOT BETWEEN 0 AND 3600 OR length(target_error_code)>120 OR length(target_error_detail)>1000 OR NOT accounting.migration_worker_login() THEN RAISE EXCEPTION 'Invalid migration finish request' USING ERRCODE='42501';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 SELECT * INTO j FROM accounting.migration_jobs WHERE id=target_job AND lease_token=target_token AND status='running' AND lease_until::timestamptz>clock_timestamp() FOR UPDATE;
 IF j.id IS NULL THEN RETURN false;END IF;
 outcome:=CASE WHEN NOT accounting.migration_execution_allowed(j.id) THEN 'stale' WHEN target_status='retry_wait' AND j.attempt_count>=j.max_attempts THEN 'failed' ELSE target_status END;
 IF outcome='succeeded' AND j.phase='validate' THEN
 SELECT * INTO v FROM accounting.migration_validations WHERE job_id=j.id;
 old_user:=accounting.actor_user();old_firm:=accounting.actor_firm();PERFORM set_config('app.user_id',j.execution_user_id,true),set_config('app.firm_id',j.firm_id,true);
 IF v.id IS NULL OR v.basis_token IS DISTINCT FROM accounting.migration_basis(j.project_id) THEN outcome:='stale';END IF;
 PERFORM set_config('app.user_id',old_user,true),set_config('app.firm_id',old_firm,true);
 END IF;
 stamp:=accounting.ocr_utc_now();
 UPDATE accounting.migration_jobs SET status=outcome,lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code=CASE WHEN outcome='stale' THEN 'scope_changed' ELSE target_error_code END,error_detail=target_error_detail,record_count=CASE WHEN phase='parse' THEN (SELECT count(*) FROM accounting.migration_records WHERE job_id=j.id) ELSE record_count END,updated_at=stamp,finished_at=CASE WHEN outcome='retry_wait' THEN NULL ELSE stamp END,available_at=to_char((clock_timestamp()+make_interval(secs=>retry_delay_seconds)) AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"') WHERE id=j.id;
 IF outcome='succeeded' AND j.phase='validate' THEN UPDATE accounting.migration_projects SET status=CASE WHEN v.blocking_count=0 THEN 'ready' ELSE 'needs_attention' END,active_validation_id=v.id,updated_at=stamp WHERE id=j.project_id;END IF;
 RETURN true;END $$;
CREATE FUNCTION accounting.migration_cancel(target_job TEXT,reason TEXT) RETURNS BOOLEAN LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE j accounting.migration_jobs;stamp TEXT;BEGIN
 IF length(btrim(reason)) NOT BETWEEN 1 AND 1000 THEN RAISE EXCEPTION 'Cancellation reason required';END IF;
 PERFORM pg_advisory_xact_lock(6420031786281903101);SELECT * INTO j FROM accounting.migration_jobs WHERE id=target_job AND accounting.migration_manage(project_id) FOR UPDATE;
 IF j.id IS NULL OR j.status NOT IN ('queued','running','retry_wait') THEN RETURN false;END IF;stamp:=accounting.ocr_utc_now();
 UPDATE accounting.migration_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,worker_id=NULL,error_code='user_cancelled',error_detail=reason,updated_at=stamp,finished_at=stamp WHERE id=j.id;RETURN true;END $$;

CREATE UNIQUE INDEX idx_migration_cutover_one_target ON accounting.migration_cutovers(book_id);
CREATE TABLE accounting.migration_external_keys(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ledger_id TEXT NOT NULL,source_system_key TEXT NOT NULL,external_book_id TEXT NOT NULL,
 kind TEXT NOT NULL,period TEXT NOT NULL,external_id TEXT NOT NULL,project_id TEXT NOT NULL,record_id TEXT NOT NULL REFERENCES accounting.migration_records(id),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),created_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,ledger_id,source_system_key,external_book_id,kind,period,external_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
ALTER TABLE accounting.migration_external_keys ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.migration_external_keys FORCE ROW LEVEL SECURITY;
GRANT SELECT ON accounting.migration_external_keys TO accounting_app;
CREATE POLICY migration_external_keys_read ON accounting.migration_external_keys FOR SELECT TO accounting_app USING(accounting.migration_allowed(project_id));
CREATE TRIGGER migration_external_keys_immutable BEFORE UPDATE OR DELETE ON accounting.migration_external_keys FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.migration_register_external_keys() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects;BEGIN
 SELECT * INTO p FROM accounting.migration_projects WHERE id=NEW.project_id;
 IF EXISTS(SELECT 1 FROM accounting.migration_records r JOIN accounting.migration_jobs q ON q.id=r.job_id JOIN accounting.migration_external_keys k ON k.firm_id=p.firm_id AND k.ledger_id=p.ledger_id AND k.source_system_key=p.source_system_key AND k.external_book_id=p.external_book_id AND k.kind=r.kind AND k.period=r.period AND k.external_id=r.external_id WHERE r.project_id=p.id AND q.status='succeeded' AND q.phase='parse' AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_jobs n WHERE n.project_id=p.id AND n.file_id=q.file_id AND n.phase='parse' AND n.status='succeeded' AND (n.created_at,n.id)>(q.created_at,q.id)) AND k.fingerprint<>r.fingerprint) THEN RAISE EXCEPTION 'Previously activated external history has conflicting content' USING ERRCODE='23514';END IF;
 INSERT INTO accounting.migration_external_keys(firm_id,company_id,ledger_id,source_system_key,external_book_id,kind,period,external_id,project_id,record_id,fingerprint,created_at)
 SELECT p.firm_id,p.company_id,p.ledger_id,p.source_system_key,p.external_book_id,r.kind,r.period,r.external_id,p.id,r.id,r.fingerprint,NEW.created_at FROM accounting.migration_records r JOIN accounting.migration_jobs q ON q.id=r.job_id WHERE r.project_id=p.id AND q.phase='parse' AND q.status='succeeded' AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_jobs n WHERE n.project_id=p.id AND n.file_id=q.file_id AND n.phase='parse' AND n.status='succeeded' AND (n.created_at,n.id)>(q.created_at,q.id)) ON CONFLICT DO NOTHING;
 RETURN NEW;END $$;
CREATE TRIGGER migration_register_keys AFTER INSERT ON accounting.migration_cutovers FOR EACH ROW EXECUTE FUNCTION accounting.migration_register_external_keys();
REVOKE ALL ON FUNCTION accounting.migration_book_member(TEXT,TEXT,BOOLEAN),accounting.migration_allowed(TEXT,TEXT),accounting.migration_manage(TEXT),accounting.migration_basis(TEXT),accounting.migration_execution_allowed(TEXT),accounting.migration_worker_login(),accounting.migration_live_write(TEXT,TEXT),accounting.migration_project_guard(),accounting.migration_access_guard(),accounting.migration_input_guard(),accounting.migration_job_guard(),accounting.migration_output_guard(),accounting.migration_acceptance_guard(),accounting.migration_cutover_guard(),accounting.migration_recover(),accounting.migration_claim(TEXT,INTEGER),accounting.migration_check(TEXT,TEXT),accounting.migration_heartbeat(TEXT,TEXT,TEXT,INTEGER,INTEGER,INTEGER),accounting.migration_finish(TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER),accounting.migration_cancel(TEXT,TEXT),accounting.migration_register_external_keys() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.migration_book_member(TEXT,TEXT,BOOLEAN),accounting.migration_allowed(TEXT,TEXT),accounting.migration_manage(TEXT),accounting.migration_basis(TEXT),accounting.migration_worker_login(),accounting.migration_live_write(TEXT,TEXT),accounting.migration_cancel(TEXT,TEXT) TO accounting_app;
GRANT EXECUTE ON FUNCTION accounting.migration_recover(),accounting.migration_claim(TEXT,INTEGER),accounting.migration_check(TEXT,TEXT),accounting.migration_heartbeat(TEXT,TEXT,TEXT,INTEGER,INTEGER,INTEGER),accounting.migration_finish(TEXT,TEXT,TEXT,TEXT,TEXT,INTEGER) TO accounting_worker;
CREATE FUNCTION accounting.migration_replacement_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects;BEGIN
 SELECT * INTO p FROM accounting.migration_projects WHERE id=NEW.project_id;
 IF NOT accounting.migration_manage(p.id) OR p.status IN ('accepted','active','cancelled') OR NEW.created_by<>accounting.actor_user()
 OR NOT EXISTS(SELECT 1 FROM accounting.migration_files o JOIN accounting.migration_files n ON n.category=o.category AND n.project_id=o.project_id WHERE o.id=NEW.old_file_id AND n.id=NEW.new_file_id AND o.project_id=p.id)
 OR EXISTS(SELECT 1 FROM accounting.migration_file_replacements x WHERE x.old_file_id=NEW.old_file_id OR x.new_file_id=NEW.new_file_id OR x.old_file_id=NEW.new_file_id)
 THEN RAISE EXCEPTION 'Original correction must replace a current same-category file in a mutable project' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_replacement_scope BEFORE INSERT ON accounting.migration_file_replacements FOR EACH ROW EXECUTE FUNCTION accounting.migration_replacement_guard();
CREATE TRIGGER migration_replacements_immutable BEFORE UPDATE OR DELETE ON accounting.migration_file_replacements FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
ALTER TABLE accounting.migration_file_replacements ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.migration_file_replacements FORCE ROW LEVEL SECURITY;
GRANT SELECT,INSERT ON accounting.migration_file_replacements TO accounting_app;
CREATE POLICY migration_replacements_read ON accounting.migration_file_replacements FOR SELECT TO accounting_app USING(accounting.migration_allowed(project_id));
CREATE POLICY migration_replacements_create ON accounting.migration_file_replacements FOR INSERT TO accounting_app WITH CHECK(accounting.migration_manage(project_id));
REVOKE ALL ON FUNCTION accounting.migration_replacement_guard() FROM PUBLIC;
REVOKE ALL ON FUNCTION accounting.migration_bootstrap(TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.migration_bootstrap(TEXT,TEXT,TEXT) TO accounting_app;
CREATE TABLE accounting.migration_source_bindings(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,ledger_id TEXT NOT NULL,source_system_key TEXT NOT NULL,external_book_id TEXT NOT NULL,created_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,source_system_key,external_book_id),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
ALTER TABLE accounting.migration_source_bindings ENABLE ROW LEVEL SECURITY;ALTER TABLE accounting.migration_source_bindings FORCE ROW LEVEL SECURITY;
CREATE TRIGGER migration_source_bindings_immutable BEFORE UPDATE OR DELETE ON accounting.migration_source_bindings FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.migration_bind_source() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
 IF NEW.firm_id<>accounting.actor_firm() OR NEW.created_by<>accounting.actor_user() OR NOT accounting.catalog_manage(NEW.book_id) OR NOT accounting.ledger_manage(NEW.ledger_id) THEN RAISE EXCEPTION 'Binding requires current reviewed ledger manager' USING ERRCODE='42501';END IF;
 INSERT INTO accounting.migration_source_bindings(firm_id,company_id,ledger_id,source_system_key,external_book_id,created_at) VALUES(NEW.firm_id,NEW.company_id,NEW.ledger_id,NEW.source_system_key,NEW.external_book_id,NEW.created_at) ON CONFLICT DO NOTHING;
 IF NOT EXISTS(SELECT 1 FROM accounting.migration_source_bindings b WHERE b.firm_id=NEW.firm_id AND b.source_system_key=NEW.source_system_key AND b.external_book_id=NEW.external_book_id AND b.company_id=NEW.company_id AND b.ledger_id=NEW.ledger_id) THEN RAISE EXCEPTION 'External source identity cannot be redirected' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_source_binding BEFORE INSERT ON accounting.migration_projects FOR EACH ROW EXECUTE FUNCTION accounting.migration_bind_source();
REVOKE ALL ON FUNCTION accounting.migration_bind_source() FROM PUBLIC;
