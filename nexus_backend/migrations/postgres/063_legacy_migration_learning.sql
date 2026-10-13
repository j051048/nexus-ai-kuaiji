-- Expand live validation basis before historical formula publication.
CREATE OR REPLACE FUNCTION accounting.migration_basis(target TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT encode(sha256(convert_to(jsonb_build_object('project',jsonb_build_array(p.id,p.version,p.ledger_id,p.book_id,p.external_book_id,p.source_system_key,p.history_start,p.history_end,p.cutover_date,p.currency),
 'files',coalesce((SELECT jsonb_agg(jsonb_build_array(f.id,f.category,f.sha256,f.mapping_json::jsonb) ORDER BY f.id) FROM accounting.migration_files f WHERE f.project_id=p.id),'[]'::jsonb),
 'mapping',coalesce((SELECT jsonb_build_array(m.version,m.payload_json::jsonb) FROM accounting.migration_mapping_versions m WHERE m.project_id=p.id ORDER BY m.version DESC LIMIT 1),'[]'::jsonb),
 'parsed',coalesce((SELECT jsonb_agg(jsonb_build_array(q.id,q.file_id,q.record_count) ORDER BY q.file_id) FROM accounting.migration_jobs q WHERE q.project_id=p.id AND q.phase='parse' AND q.status='succeeded' AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_jobs q2 WHERE q2.project_id=p.id AND q2.file_id=q.file_id AND q2.phase='parse' AND q2.status='succeeded' AND (q2.created_at,q2.id)>(q.created_at,q.id))),'[]'::jsonb),
 'records',coalesce((SELECT jsonb_agg(jsonb_build_array(r.job_id,r.id,r.fingerprint) ORDER BY r.job_id,r.id) FROM accounting.migration_records r JOIN accounting.migration_jobs q ON q.id=r.job_id WHERE r.project_id=p.id AND q.status='succeeded' AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements z WHERE z.old_file_id=q.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_jobs q2 WHERE q2.project_id=p.id AND q2.file_id=q.file_id AND q2.phase='parse' AND q2.status='succeeded' AND (q2.created_at,q2.id)>(q.created_at,q.id))),'[]'::jsonb),
 'accounts',coalesce((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.code) FROM accounting.accounts a WHERE a.book_id=p.book_id),'[]'::jsonb),
 'masters',coalesce((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) FROM accounting.company_master_entities e WHERE e.firm_id=p.firm_id AND e.company_id=p.company_id),'[]'::jsonb),
 'aliases',coalesce((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM accounting.company_master_aliases a WHERE a.firm_id=p.firm_id AND a.company_id=p.company_id),'[]'::jsonb),
 'period_status',(SELECT q.status FROM accounting.periods q JOIN accounting.books b ON b.id=q.book_id AND b.period=q.period WHERE b.id=p.book_id),'departments',coalesce((SELECT jsonb_agg(to_jsonb(d) ORDER BY d.id) FROM accounting.departments d WHERE d.company_id=p.company_id),'[]'::jsonb),'people',coalesce((SELECT jsonb_agg(jsonb_build_array(c.user_id,c.role,c.department_id,u.active) ORDER BY c.user_id) FROM accounting.company_memberships c JOIN accounting.users u ON u.id=c.user_id WHERE c.company_id=p.company_id),'[]'::jsonb),'replacements',coalesce((SELECT jsonb_agg(jsonb_build_array(x.old_file_id,x.new_file_id) ORDER BY x.id) FROM accounting.migration_file_replacements x WHERE x.project_id=p.id),'[]'::jsonb))::text,'UTF8')),'hex')
 FROM accounting.migration_projects p WHERE p.id=target AND accounting.migration_allowed(p.id)
$$;
CREATE OR REPLACE FUNCTION accounting.migration_acceptance_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE p accounting.migration_projects;v accounting.migration_validations;BEGIN
 SELECT * INTO p FROM accounting.migration_projects WHERE id=NEW.project_id FOR UPDATE;SELECT * INTO v FROM accounting.migration_validations WHERE id=NEW.validation_id;
 IF NEW.accepted_by<>accounting.actor_user() OR NOT accounting.migration_allowed(p.id,'reviewer') OR NEW.accepted_by=p.created_by
 OR NEW.accepted_by=coalesce((SELECT created_by FROM accounting.migration_mapping_versions WHERE project_id=p.id ORDER BY version DESC LIMIT 1),p.created_by)
 OR NOT EXISTS(SELECT 1 FROM accounting.periods q JOIN accounting.books b ON b.id=q.book_id AND b.period=q.period WHERE b.id=p.book_id AND q.status='open') OR p.status<>'ready' OR p.active_validation_id IS DISTINCT FROM v.id OR v.project_id<>p.id OR v.firm_id<>NEW.firm_id OR v.company_id<>NEW.company_id OR v.blocking_count<>0 OR v.input_version<>p.version OR NEW.expected_version<>p.version
 OR v.basis_token IS DISTINCT FROM NEW.basis_token OR v.basis_token IS DISTINCT FROM accounting.migration_basis(p.id) OR NOT coalesce((v.report_json::jsonb->'coverage'->>'complete')::boolean,false)
 OR NOT EXISTS(SELECT 1 FROM accounting.migration_jobs WHERE id=v.job_id AND status='succeeded' AND phase='validate')
 THEN RAISE EXCEPTION 'Acceptance requires current complete validation and an independent reviewer' USING ERRCODE='23514';END IF;
 UPDATE accounting.migration_projects SET status='accepted',updated_at=NEW.created_at WHERE id=p.id;RETURN NEW;END $$;
-- Private historical examples and deliberately published current-book formulas.
CREATE TABLE accounting.migration_learning_candidates(
 id TEXT PRIMARY KEY,project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),status TEXT NOT NULL DEFAULT 'candidate' CHECK(status IN ('candidate','reviewed','rejected','published','retired')),
 definition_json TEXT NOT NULL CHECK(jsonb_typeof(definition_json::jsonb)='object'),positive_record_ids_json TEXT NOT NULL CHECK(jsonb_typeof(positive_record_ids_json::jsonb)='array' AND jsonb_array_length(positive_record_ids_json::jsonb) BETWEEN 1 AND 100),
 eligibility_json TEXT NOT NULL CHECK(jsonb_typeof(eligibility_json::jsonb)='object'),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(project_id,request_key),UNIQUE(id,project_id,book_id,firm_id,company_id),FOREIGN KEY(project_id,firm_id,company_id) REFERENCES accounting.migration_projects(id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.migration_learning_samples(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,record_id TEXT NOT NULL REFERENCES accounting.migration_records(id),project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 polarity TEXT NOT NULL CHECK(polarity IN ('positive','negative')),snapshot_json TEXT NOT NULL CHECK(jsonb_typeof(snapshot_json::jsonb)='object'),final_json TEXT NOT NULL CHECK(jsonb_typeof(final_json::jsonb)='object'),
 quality_json TEXT NOT NULL CHECK(quality_json::jsonb->>'source_type'='historical'),fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,record_id),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES accounting.migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.migration_learning_reviews(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,input_version INTEGER NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approve','reject')),evidence_confirmed INTEGER NOT NULL CHECK(evidence_confirmed=1),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),reviewed_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,request_key),UNIQUE(candidate_id,input_version),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES accounting.migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.migration_learning_shadows(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,input_version INTEGER NOT NULL,
 negative_record_ids_json TEXT NOT NULL CHECK(jsonb_typeof(negative_record_ids_json::jsonb)='array' AND jsonb_array_length(negative_record_ids_json::jsonb) BETWEEN 1 AND 100),
 context_fingerprint TEXT NOT NULL CHECK(length(context_fingerprint)=64),report_json TEXT NOT NULL CHECK(jsonb_typeof(report_json::jsonb)='object'),passed INTEGER NOT NULL CHECK(passed IN (0,1) AND coalesce((report_json::jsonb->>'passed')::boolean=(passed=1),false)),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,request_key),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES accounting.migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.migration_learning_rules(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL UNIQUE,project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version=1),
 definition_json TEXT NOT NULL CHECK(jsonb_typeof(definition_json::jsonb)='object'),shadow_id TEXT NOT NULL REFERENCES accounting.migration_learning_shadows(id),context_fingerprint TEXT NOT NULL CHECK(length(context_fingerprint)=64),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),published_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,request_key),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES accounting.migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.migration_learning_retirements(
 id TEXT PRIMARY KEY,candidate_id TEXT NOT NULL,rule_id TEXT NOT NULL UNIQUE REFERENCES accounting.migration_learning_rules(id),project_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(candidate_id,request_key),FOREIGN KEY(candidate_id,project_id,book_id,firm_id,company_id) REFERENCES accounting.migration_learning_candidates(id,project_id,book_id,firm_id,company_id)
);
CREATE INDEX idx_migration_learning_candidates_page ON accounting.migration_learning_candidates(project_id,book_id,created_at DESC,id);
CREATE INDEX idx_migration_learning_samples_candidate ON accounting.migration_learning_samples(candidate_id,polarity,record_id);
CREATE INDEX idx_migration_learning_rules_book ON accounting.migration_learning_rules(book_id,firm_id,company_id);

CREATE FUNCTION accounting.migration_learning_open(target_project TEXT) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.migration_allowed(target_project,'reviewer') AND EXISTS(SELECT 1 FROM accounting.migration_projects p JOIN accounting.migration_cutovers c ON c.project_id=p.id JOIN accounting.books b ON b.id=p.book_id JOIN accounting.periods q ON q.book_id=b.id AND q.period=b.period WHERE p.id=target_project AND p.status='active' AND q.status='open')
$$;
CREATE FUNCTION accounting.migration_learning_fact(target_project TEXT,target_record TEXT) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE r accounting.migration_records;p accounting.migration_projects;c accounting.migration_cutovers;payload JSONB;line JSONB;bank JSONB;bank_count INTEGER:=0;cp_code TEXT;cp_id TEXT;cp_name TEXT;cp_value TEXT;target_code TEXT;required JSONB;debit BIGINT;credit BIGINT;total_debit BIGINT:=0;total_credit BIGINT:=0;final JSONB:='[]'::jsonb;direction TEXT;BEGIN
 IF NOT accounting.migration_allowed(target_project) THEN RETURN NULL;END IF;
 SELECT * INTO p FROM accounting.migration_projects WHERE id=target_project AND status='active';SELECT * INTO c FROM accounting.migration_cutovers WHERE project_id=p.id;
 SELECT a.* INTO r FROM accounting.migration_records a JOIN accounting.migration_jobs j ON j.id=a.job_id WHERE a.id=target_record AND a.project_id=p.id AND a.kind='voucher' AND j.phase='parse' AND j.status='succeeded'
 AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements x WHERE x.old_file_id=a.file_id) AND NOT EXISTS(SELECT 1 FROM accounting.migration_jobs n WHERE n.project_id=p.id AND n.file_id=a.file_id AND n.phase='parse' AND n.status='succeeded' AND (n.created_at,n.id)>(j.created_at,j.id));
 IF p.id IS NULL OR c.id IS NULL OR r.id IS NULL THEN RETURN NULL;END IF;payload:=r.payload_json::jsonb;
 IF coalesce(payload->>'status','')<>'posted' OR coalesce((payload->>'is_reversal')::boolean,true) OR jsonb_typeof(payload->'lines') IS DISTINCT FROM 'array' OR jsonb_array_length(payload->'lines')<>2 THEN RETURN NULL;END IF;
 FOR line IN SELECT value FROM jsonb_array_elements(payload->'lines') LOOP
 IF line->>'description' LIKE '%结转%' OR line->>'description' LIKE '%损益%' OR EXISTS(SELECT 1 FROM jsonb_object_keys(coalesce(line->'auxiliaries','{}'::jsonb)) key WHERE key NOT IN ('counterparty','bank_account')) THEN RETURN NULL;END IF;
 IF left(line->>'account_code',4)='1002' OR coalesce(line->'auxiliaries'->>'bank_account','')<>'' THEN bank:=line;bank_count:=bank_count+1;END IF;
 cp_value:=line->'auxiliaries'->>'counterparty';IF cp_value IS NOT NULL THEN IF cp_code IS NOT NULL AND cp_code<>cp_value THEN RETURN NULL;END IF;cp_code:=cp_value;END IF;
 END LOOP;
 IF bank_count<>1 OR cp_code IS NULL THEN RETURN NULL;END IF;
 SELECT e.id,e.name INTO cp_id,cp_name FROM jsonb_array_elements(c.payload_json::jsonb->'master_bindings') m JOIN accounting.company_master_entities e ON e.id=m->>'target_id' AND e.firm_id=p.firm_id AND e.company_id=p.company_id AND e.kind='counterparty' AND e.active=1 WHERE m->>'kind'='counterparty' AND m->>'source_code'=cp_code;
 IF cp_id IS NULL THEN RETURN NULL;END IF;
 FOR line IN SELECT value FROM jsonb_array_elements(payload->'lines') LOOP
 debit:=accounting.learning_money_cents(line->>'debit');credit:=accounting.learning_money_cents(line->>'credit');
 IF debit IS NULL OR credit IS NULL OR debit<0 OR credit<0 OR (debit=0)=(credit=0) THEN RETURN NULL;END IF;total_debit:=total_debit+debit;total_credit:=total_credit+credit;
 SELECT m->>'target_code' INTO target_code FROM jsonb_array_elements(c.payload_json::jsonb->'account_bindings') m WHERE m->>'source_code'=line->>'account_code';
 SELECT auxiliary_requirements::jsonb INTO required FROM accounting.accounts WHERE book_id=p.book_id AND code=target_code AND active=1;
 IF NOT FOUND OR NOT(required<@'["counterparty"]'::jsonb) THEN RETURN NULL;END IF;
 final:=final||jsonb_build_array(jsonb_build_object('account_code',target_code,'debit',accounting.delivery_cents(debit),'credit',accounting.delivery_cents(credit),'auxiliary_values',CASE WHEN required?'counterparty' THEN jsonb_build_object('counterparty',cp_name) ELSE '{}'::jsonb END));
 END LOOP;
 IF total_debit<=0 OR total_debit<>total_credit THEN RETURN NULL;END IF;
 SELECT m->>'target_code' INTO target_code FROM jsonb_array_elements(c.payload_json::jsonb->'account_bindings') m WHERE m->>'source_code'=bank->>'account_code';
 IF NOT EXISTS(SELECT 1 FROM accounting.accounts WHERE book_id=p.book_id AND code=target_code AND active=1 AND category='asset') THEN RETURN NULL;END IF;
 direction:=CASE WHEN accounting.learning_money_cents(bank->>'credit')>0 THEN 'expense' ELSE 'income' END;
 RETURN jsonb_build_object('snapshot',jsonb_build_object('source_type','historical','source_kind','bank','business_type','daily_cashflow','counterparty_id',cp_id,'payload',jsonb_build_object('occurred_on',payload->>'date','amount',accounting.delivery_cents(total_debit),'direction',direction,'payment_status','paid','counterparty',cp_name),'context','{}'::jsonb,'recognized','{}'::jsonb),'final',jsonb_build_object('lines',final));END $$;
CREATE FUNCTION accounting.migration_learning_context_internal(target_candidate TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT encode(sha256(convert_to(jsonb_build_object('definition',c.definition_json::jsonb,'cutover',(SELECT z.payload_json::jsonb FROM accounting.migration_cutovers z WHERE z.project_id=c.project_id),'period',(SELECT q.status FROM accounting.periods q JOIN accounting.books b ON b.id=q.book_id AND b.period=q.period WHERE b.id=c.book_id),
 'accounts',coalesce((SELECT jsonb_agg(to_jsonb(a) ORDER BY a.code) FROM accounting.accounts a WHERE a.book_id=c.book_id),'[]'::jsonb),'masters',accounting.learning_master_context(c.book_id),
 'samples',coalesce((SELECT jsonb_agg(jsonb_build_array(s.record_id,s.polarity,s.fingerprint,r.fingerprint,f.sha256) ORDER BY s.record_id) FROM accounting.migration_learning_samples s JOIN accounting.migration_records r ON r.id=s.record_id JOIN accounting.migration_files f ON f.id=r.file_id WHERE s.candidate_id=c.id),'[]'::jsonb),
 'review',coalesce((SELECT jsonb_agg(jsonb_build_array(r.id,r.decision,r.reviewed_by) ORDER BY r.id) FROM accounting.migration_learning_reviews r WHERE r.candidate_id=c.id),'[]'::jsonb))::text,'UTF8')),'hex')
 FROM accounting.migration_learning_candidates c WHERE c.id=target_candidate
$$;
CREATE FUNCTION accounting.migration_learning_context(target_candidate TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT accounting.migration_learning_context_internal(target_candidate) WHERE EXISTS(SELECT 1 FROM accounting.migration_learning_candidates c WHERE c.id=target_candidate AND accounting.migration_allowed(c.project_id)) $$;
CREATE FUNCTION accounting.migration_learning_evaluate(target_candidate TEXT,negative_ids JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.migration_learning_candidates;key TEXT;v_polarity TEXT;fact JSONB;sample accounting.migration_learning_samples;definition JSONB;matched BOOLEAN;equal_value BOOLEAN;eligible BOOLEAN;passed BOOLEAN:=true;line JSONB;required JSONB;rendered JSONB;outcomes JSONB:='[]'::jsonb;BEGIN
 SELECT * INTO c FROM accounting.migration_learning_candidates WHERE id=target_candidate AND accounting.migration_learning_open(project_id);
 IF c.id IS NULL OR jsonb_typeof(negative_ids) IS DISTINCT FROM 'array' OR jsonb_array_length(negative_ids) NOT BETWEEN 1 AND 100 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(negative_ids) x WHERE c.positive_record_ids_json::jsonb?x) OR (SELECT count(DISTINCT x) FROM jsonb_array_elements_text(negative_ids) x)<>jsonb_array_length(negative_ids) THEN RAISE EXCEPTION 'Bounded nonoverlapping historical negative examples required' USING ERRCODE='23514';END IF;
 definition:=c.definition_json::jsonb;
 FOR key,v_polarity IN SELECT value,'positive' FROM jsonb_array_elements_text(c.positive_record_ids_json::jsonb) UNION ALL SELECT value,'negative' FROM jsonb_array_elements_text(negative_ids) LOOP
 fact:=accounting.migration_learning_fact(c.project_id,key);SELECT * INTO sample FROM accounting.migration_learning_samples WHERE candidate_id=c.id AND record_id=key AND migration_learning_samples.polarity=v_polarity;
 eligible:=fact IS NOT NULL AND sample.id IS NOT NULL AND sample.snapshot_json::jsonb=fact->'snapshot' AND sample.final_json::jsonb=fact->'final';matched:=false;equal_value:=false;
 IF eligible THEN
 matched:=definition->'conditions'->>'counterparty_id'=fact->'snapshot'->>'counterparty_id' AND definition->'conditions'->>'direction'=fact->'snapshot'->'payload'->>'direction' AND accounting.learning_identity_valid(c.book_id,definition);
 IF matched THEN
 rendered:='[]'::jsonb;
 FOR line IN SELECT value FROM jsonb_array_elements(definition->'lines') LOOP
 SELECT auxiliary_requirements::jsonb INTO required FROM accounting.accounts WHERE book_id=c.book_id AND code=line->>'account_code' AND active=1;
 IF NOT FOUND OR NOT(required<@'["counterparty"]'::jsonb) THEN eligible:=false;EXIT;END IF;
 rendered:=rendered||jsonb_build_array(jsonb_build_object('account_code',line->>'account_code','debit',CASE WHEN line->>'side'='debit' THEN fact->'snapshot'->'payload'->>'amount' ELSE '0.00' END,'credit',CASE WHEN line->>'side'='credit' THEN fact->'snapshot'->'payload'->>'amount' ELSE '0.00' END,'auxiliary_values',CASE WHEN required?'counterparty' THEN jsonb_build_object('counterparty',fact->'snapshot'->'payload'->>'counterparty') ELSE '{}'::jsonb END));
 END LOOP;
 equal_value:=eligible AND accounting.learning_canonical_lines(rendered)=accounting.learning_canonical_lines(fact->'final'->'lines');END IF;END IF;
 IF NOT eligible OR (v_polarity='positive' AND NOT(matched AND equal_value)) OR (v_polarity='negative' AND matched) THEN passed:=false;END IF;
 outcomes:=outcomes||jsonb_build_array(jsonb_build_object('record_id',key,'polarity',v_polarity,'eligible',eligible,'matched',matched,'equal',equal_value));
 END LOOP;
 RETURN jsonb_build_object('passed',passed,'positive_count',jsonb_array_length(c.positive_record_ids_json::jsonb),'negative_count',jsonb_array_length(negative_ids),'outcomes',outcomes,'source_type','historical','writes_current_ledger',false);END $$;

CREATE FUNCTION accounting.migration_learning_candidate_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE d JSONB;BEGIN
 IF TG_OP='UPDATE' THEN
 IF (NEW.id,NEW.project_id,NEW.book_id,NEW.firm_id,NEW.company_id,NEW.definition_json,NEW.positive_record_ids_json,NEW.eligibility_json,NEW.reason,NEW.request_key,NEW.request_fingerprint,NEW.created_by,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.project_id,OLD.book_id,OLD.firm_id,OLD.company_id,OLD.definition_json,OLD.positive_record_ids_json,OLD.eligibility_json,OLD.reason,OLD.request_key,OLD.request_fingerprint,OLD.created_by,OLD.created_at) OR NEW.version<>OLD.version+1 OR current_user='accounting_app' OR NOT((OLD.status='candidate' AND NEW.status IN ('reviewed','rejected')) OR (OLD.status='reviewed' AND NEW.status='published') OR (OLD.status='published' AND NEW.status='retired')) THEN RAISE EXCEPTION 'Historical candidate lineage is immutable' USING ERRCODE='23514';END IF;RETURN NEW;END IF;
 d:=NEW.definition_json::jsonb;
 IF NOT accounting.migration_learning_open(NEW.project_id) OR NEW.created_by<>accounting.actor_user() OR NEW.firm_id<>accounting.actor_firm() OR NEW.status<>'candidate' OR NEW.version<>1 OR NOT EXISTS(SELECT 1 FROM accounting.migration_projects p WHERE p.id=NEW.project_id AND p.book_id=NEW.book_id AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id)
 OR coalesce(d->>'source_kind','')<>'bank' OR coalesce(d->'conditions'->>'business_type','')<>'daily_cashflow' OR coalesce(d->'conditions'->>'payment_status','')<>'paid' OR d->'conditions'->>'category' IS NOT NULL OR coalesce(d->'conditions'->>'direction','') NOT IN ('income','expense') OR coalesce(length(btrim(d->'conditions'->>'counterparty')),0) NOT BETWEEN 1 AND 200
 OR jsonb_typeof(d->'lines') IS DISTINCT FROM 'array' OR jsonb_array_length(d->'lines')<>2 OR (SELECT count(*) FROM jsonb_array_elements(d->'lines') l WHERE l->>'side'='debit' AND l->>'amount_field'='gross')<>1 OR (SELECT count(*) FROM jsonb_array_elements(d->'lines') l WHERE l->>'side'='credit' AND l->>'amount_field'='gross')<>1
 OR NOT EXISTS(SELECT 1 FROM accounting.migration_cutovers z CROSS JOIN LATERAL jsonb_array_elements(z.payload_json::jsonb->'master_bindings') m JOIN accounting.company_master_entities e ON e.id=m->>'target_id' AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.kind='counterparty' AND e.active=1 WHERE z.project_id=NEW.project_id AND m->>'kind'='counterparty' AND e.id=d->'conditions'->>'counterparty_id')
 OR (SELECT count(DISTINCT x) FROM jsonb_array_elements_text(NEW.positive_record_ids_json::jsonb) x)<>jsonb_array_length(NEW.positive_record_ids_json::jsonb)
 THEN RAISE EXCEPTION 'Historical candidates require explicit current bank formula and stable cutover identity' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_learning_candidate_guard BEFORE INSERT OR UPDATE ON accounting.migration_learning_candidates FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_candidate_guard();
CREATE FUNCTION accounting.migration_learning_sample_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.migration_learning_candidates;fact JSONB;BEGIN
 SELECT * INTO c FROM accounting.migration_learning_candidates WHERE id=NEW.candidate_id;
 IF NOT accounting.migration_learning_open(c.project_id) OR NEW.created_by<>accounting.actor_user()
 OR NOT EXISTS(SELECT 1 FROM accounting.migration_records r JOIN accounting.migration_jobs j ON j.id=r.job_id WHERE r.id=NEW.record_id AND r.project_id=c.project_id AND j.phase='parse' AND j.status='succeeded' AND NOT EXISTS(SELECT 1 FROM accounting.migration_file_replacements x WHERE x.old_file_id=r.file_id))
 THEN RAISE EXCEPTION 'Historical sample requires current scoped original' USING ERRCODE='42501';END IF;
 IF NEW.polarity='positive' THEN
 IF c.status<>'candidate' OR c.created_by<>accounting.actor_user() OR NOT(c.positive_record_ids_json::jsonb?NEW.record_id) THEN RAISE EXCEPTION 'Positive historical example identity is immutable';END IF;
 ELSE
 IF c.status<>'reviewed' OR c.created_by=accounting.actor_user() OR c.positive_record_ids_json::jsonb?NEW.record_id THEN RAISE EXCEPTION 'Negative historical examples require independent review';END IF;
 END IF;
 fact:=accounting.migration_learning_fact(c.project_id,NEW.record_id);
 IF coalesce((NEW.quality_json::jsonb->>'eligible')::boolean,false) IS DISTINCT FROM (fact IS NOT NULL) OR NEW.snapshot_json::jsonb IS DISTINCT FROM coalesce(fact->'snapshot','{}'::jsonb) OR NEW.final_json::jsonb IS DISTINCT FROM coalesce(fact->'final','{}'::jsonb) THEN RAISE EXCEPTION 'Historical example cannot invent source facts or reviewed answers' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_learning_sample_truth BEFORE INSERT ON accounting.migration_learning_samples FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_sample_guard();
CREATE FUNCTION accounting.migration_learning_review_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.migration_learning_candidates;BEGIN SELECT * INTO c FROM accounting.migration_learning_candidates WHERE id=NEW.candidate_id FOR UPDATE;
 IF NOT accounting.migration_learning_open(c.project_id) OR NEW.reviewed_by<>accounting.actor_user() OR NEW.reviewed_by=c.created_by OR c.status<>'candidate' OR c.version<>NEW.input_version OR NEW.evidence_confirmed<>1 OR (SELECT count(*) FROM accounting.migration_learning_samples s WHERE s.candidate_id=c.id AND s.polarity='positive')<>jsonb_array_length(c.positive_record_ids_json::jsonb) THEN RAISE EXCEPTION 'Another accountant must independently check complete historical originals' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_learning_review_scope BEFORE INSERT ON accounting.migration_learning_reviews FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_review_guard();
CREATE FUNCTION accounting.migration_learning_shadow_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.migration_learning_candidates;expected JSONB;BEGIN SELECT * INTO c FROM accounting.migration_learning_candidates WHERE id=NEW.candidate_id;
 IF NOT accounting.migration_learning_open(c.project_id) OR NEW.created_by<>accounting.actor_user() OR NEW.created_by=c.created_by OR c.status<>'reviewed' OR NEW.input_version<>c.version OR NEW.context_fingerprint IS DISTINCT FROM accounting.migration_learning_context_internal(c.id) THEN RAISE EXCEPTION 'Historical shadow context or reviewer is stale' USING ERRCODE='23514';END IF;
 expected:=accounting.migration_learning_evaluate(c.id,NEW.negative_record_ids_json::jsonb);
 IF NEW.report_json::jsonb IS DISTINCT FROM expected OR NEW.passed IS DISTINCT FROM (CASE WHEN (expected->>'passed')::boolean THEN 1 ELSE 0 END) THEN RAISE EXCEPTION 'Historical shadow must equal deterministic positive and negative replay' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_learning_shadow_truth BEFORE INSERT ON accounting.migration_learning_shadows FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_shadow_guard();
CREATE FUNCTION accounting.migration_learning_rule_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.migration_learning_candidates;s accounting.migration_learning_shadows;BEGIN
 SELECT * INTO c FROM accounting.migration_learning_candidates WHERE id=NEW.candidate_id FOR UPDATE;SELECT * INTO s FROM accounting.migration_learning_shadows WHERE id=NEW.shadow_id AND candidate_id=c.id;
 IF NOT accounting.migration_learning_open(c.project_id) OR NEW.published_by<>accounting.actor_user() OR NEW.published_by=c.created_by OR c.status<>'reviewed' OR s.id IS NULL OR s.passed<>1 OR s.input_version<>c.version OR NEW.version<>1 OR NEW.definition_json::jsonb IS DISTINCT FROM c.definition_json::jsonb OR NEW.context_fingerprint IS DISTINCT FROM s.context_fingerprint OR s.context_fingerprint IS DISTINCT FROM accounting.migration_learning_context_internal(c.id)
 OR s.report_json::jsonb IS DISTINCT FROM accounting.migration_learning_evaluate(c.id,s.negative_record_ids_json::jsonb)
 OR NOT accounting.learning_identity_valid(c.book_id,c.definition_json::jsonb) OR accounting.learning_conflicts(c.book_id,'',c.definition_json::jsonb)
 OR EXISTS(SELECT 1 FROM accounting.migration_learning_rules r WHERE r.book_id=c.book_id AND NOT EXISTS(SELECT 1 FROM accounting.migration_learning_retirements x WHERE x.rule_id=r.id) AND accounting.learning_overlaps(c.book_id,r.definition_json::jsonb,c.definition_json::jsonb))
 THEN RAISE EXCEPTION 'Historical publication requires independent current replay without conflicting rule' USING ERRCODE='23514';END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_learning_rule_scope BEFORE INSERT ON accounting.migration_learning_rules FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_rule_guard();
CREATE FUNCTION accounting.migration_learning_retirement_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE c accounting.migration_learning_candidates;BEGIN SELECT * INTO c FROM accounting.migration_learning_candidates WHERE id=NEW.candidate_id FOR UPDATE;
 IF NOT accounting.migration_learning_open(c.project_id) OR NEW.created_by<>accounting.actor_user() OR c.status<>'published' OR NOT EXISTS(SELECT 1 FROM accounting.migration_learning_rules r WHERE r.id=NEW.rule_id AND r.candidate_id=c.id) THEN RAISE EXCEPTION 'Only current project accountant can retire published formula' USING ERRCODE='42501';END IF;RETURN NEW;END $$;
CREATE TRIGGER migration_learning_retirement_scope BEFORE INSERT ON accounting.migration_learning_retirements FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_retirement_guard();
CREATE FUNCTION accounting.migration_learning_publish_state() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE next_status TEXT;BEGIN
 IF TG_TABLE_NAME='migration_learning_reviews' THEN next_status:=CASE WHEN NEW.decision='approve' THEN 'reviewed' ELSE 'rejected' END;
 ELSIF TG_TABLE_NAME='migration_learning_rules' THEN next_status:='published';ELSE next_status:='retired';END IF;
 UPDATE accounting.migration_learning_candidates SET status=next_status,version=version+1,updated_at=NEW.created_at WHERE id=NEW.candidate_id;RETURN NEW;END $$;
CREATE TRIGGER migration_learning_review_publish AFTER INSERT ON accounting.migration_learning_reviews FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_publish_state();
CREATE TRIGGER migration_learning_rule_publish AFTER INSERT ON accounting.migration_learning_rules FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_publish_state();
CREATE TRIGGER migration_learning_retirement_publish AFTER INSERT ON accounting.migration_learning_retirements FOR EACH ROW EXECUTE FUNCTION accounting.migration_learning_publish_state();

DO $$ DECLARE item TEXT;BEGIN
 FOREACH item IN ARRAY ARRAY['migration_learning_candidates','migration_learning_samples','migration_learning_reviews','migration_learning_shadows','migration_learning_rules','migration_learning_retirements'] LOOP
 EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',item);EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',item);
 EXECUTE format('GRANT SELECT,INSERT ON accounting.%I TO accounting_app',item);
 EXECUTE format('CREATE POLICY %I ON accounting.%I FOR SELECT TO accounting_app USING(accounting.migration_allowed(project_id))',item||'_read',item);
 EXECUTE format('CREATE POLICY %I ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(accounting.migration_learning_open(project_id))',item||'_write',item);
 IF item<>'migration_learning_candidates' THEN EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',item||'_immutable',item);END IF;
 END LOOP;END $$;
CREATE TRIGGER migration_learning_candidates_no_delete BEFORE DELETE ON accounting.migration_learning_candidates FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard();
CREATE FUNCTION accounting.migration_learning_public_rules(target_book TEXT) RETURNS TABLE(id TEXT,version INTEGER,definition_json TEXT) LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT r.id,r.version,r.definition_json FROM accounting.migration_learning_rules r JOIN accounting.migration_projects p ON p.id=r.project_id JOIN accounting.migration_cutovers z ON z.project_id=p.id
 WHERE r.book_id=target_book AND r.firm_id=accounting.actor_firm() AND accounting.book_allowed(target_book,true) AND p.status='active'
 AND NOT EXISTS(SELECT 1 FROM accounting.migration_learning_retirements x WHERE x.rule_id=r.id)
 AND r.context_fingerprint=accounting.migration_learning_context_internal(r.candidate_id) ORDER BY r.id
$$;
REVOKE ALL ON FUNCTION accounting.migration_learning_open(TEXT),accounting.migration_learning_fact(TEXT,TEXT),accounting.migration_learning_context_internal(TEXT),accounting.migration_learning_context(TEXT),accounting.migration_learning_evaluate(TEXT,JSONB),accounting.migration_learning_candidate_guard(),accounting.migration_learning_sample_guard(),accounting.migration_learning_review_guard(),accounting.migration_learning_shadow_guard(),accounting.migration_learning_rule_guard(),accounting.migration_learning_retirement_guard(),accounting.migration_learning_publish_state(),accounting.migration_learning_public_rules(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.migration_learning_open(TEXT),accounting.migration_learning_fact(TEXT,TEXT),accounting.migration_learning_context(TEXT),accounting.migration_learning_evaluate(TEXT,JSONB),accounting.migration_learning_public_rules(TEXT) TO accounting_app;
