-- Customer supplied references; no official ledger, no automatic approval.
CREATE TABLE accounting.monthly_baselines(
 id TEXT PRIMARY KEY, book_id TEXT NOT NULL, ledger_id TEXT NOT NULL, firm_id TEXT NOT NULL, company_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('opening','closing')), version INTEGER NOT NULL CHECK(version>0), period TEXT NOT NULL,
 external_reference TEXT NOT NULL CHECK(length(trim(external_reference)) BETWEEN 1 AND 500), evidence_ref TEXT NOT NULL CHECK(length(trim(evidence_ref)) BETWEEN 1 AND 500), reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 original_format TEXT NOT NULL CHECK(original_format IN ('structured_entry','json','csv','legacy_snapshot','bridge_snapshot')),original_filename TEXT NOT NULL,original_content TEXT NOT NULL CHECK(length(original_content)>0),original_sha256 TEXT NOT NULL CHECK(length(original_sha256)=64),
 source_kind TEXT NOT NULL CHECK(source_kind IN ('manual','legacy_cutover','bridge_import')),source_id TEXT,lines_json TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id), created_at TEXT NOT NULL,request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 CHECK((source_kind='manual' AND source_id IS NULL) OR (source_kind<>'manual' AND source_id IS NOT NULL)),
 UNIQUE(book_id,kind,version),UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,request_key),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
CREATE TABLE accounting.monthly_baseline_confirmations(
 baseline_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,confirmed_by TEXT NOT NULL REFERENCES accounting.users(id),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),created_at TEXT NOT NULL,
 FOREIGN KEY(baseline_id,book_id,firm_id,company_id) REFERENCES accounting.monthly_baselines(id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.monthly_checks(
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL,ledger_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),opening_id TEXT NOT NULL,closing_id TEXT NOT NULL,inputs_json TEXT NOT NULL,report_json TEXT NOT NULL,input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 UNIQUE(book_id,version),UNIQUE(id,book_id,firm_id,company_id),UNIQUE(firm_id,created_by,request_key),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id),
 FOREIGN KEY(opening_id,book_id,firm_id,company_id) REFERENCES accounting.monthly_baselines(id,book_id,firm_id,company_id),FOREIGN KEY(closing_id,book_id,firm_id,company_id) REFERENCES accounting.monthly_baselines(id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.monthly_check_closures(
 check_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,closed_by TEXT NOT NULL REFERENCES accounting.users(id),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),created_at TEXT NOT NULL,
 FOREIGN KEY(check_id,book_id,firm_id,company_id) REFERENCES accounting.monthly_checks(id,book_id,firm_id,company_id)
);
CREATE TABLE accounting.recurring_plan_versions(
 plan_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),ledger_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),start_period TEXT NOT NULL,end_period TEXT,day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 28),description TEXT NOT NULL CHECK(length(trim(description)) BETWEEN 1 AND 500),evidence_ref TEXT NOT NULL CHECK(length(trim(evidence_ref)) BETWEEN 1 AND 500),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),lines_json TEXT NOT NULL,catalog_json TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 PRIMARY KEY(plan_id,version),UNIQUE(plan_id,version,ledger_id,firm_id,company_id),UNIQUE(firm_id,created_by,request_key),
 CHECK(end_period IS NULL OR end_period>=start_period),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),FOREIGN KEY(ledger_id,firm_id,company_id) REFERENCES accounting.ledgers(id,firm_id,company_id)
);
CREATE TABLE accounting.recurring_plan_approvals(
 plan_id TEXT NOT NULL,version INTEGER NOT NULL,ledger_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,approved_by TEXT NOT NULL REFERENCES accounting.users(id),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),created_at TEXT NOT NULL,
 PRIMARY KEY(plan_id,version),FOREIGN KEY(plan_id,version,ledger_id,firm_id,company_id) REFERENCES accounting.recurring_plan_versions(plan_id,version,ledger_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.recurring_generations(
 plan_id TEXT NOT NULL,version INTEGER NOT NULL,ledger_id TEXT NOT NULL,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,period TEXT NOT NULL,source_id TEXT NOT NULL UNIQUE,draft_id TEXT NOT NULL UNIQUE,created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 PRIMARY KEY(plan_id,book_id),UNIQUE(plan_id,ledger_id,period),UNIQUE(firm_id,created_by,request_key),FOREIGN KEY(plan_id,version,ledger_id,firm_id,company_id) REFERENCES accounting.recurring_plan_versions(plan_id,version,ledger_id,firm_id,company_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id),FOREIGN KEY(draft_id,source_id,book_id) REFERENCES accounting.drafts(id,source_id,book_id)
);
CREATE INDEX idx_monthly_baselines_book ON accounting.monthly_baselines(book_id,kind,version DESC);
CREATE INDEX idx_monthly_checks_book ON accounting.monthly_checks(book_id,version DESC);
CREATE INDEX idx_recurring_plan_ledger ON accounting.recurring_plan_versions(ledger_id,plan_id,version DESC);

CREATE FUNCTION accounting.monthly_catalog(target TEXT,lines JSONB) RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('code',a.code,'name',a.name,'category',a.category,'normal_balance',a.normal_balance,'auxiliary_requirements',a.auxiliary_requirements::jsonb) ORDER BY a.code),'[]'::jsonb)
 FROM accounting.accounts a WHERE a.book_id=target AND a.active=1 AND a.code IN(SELECT value->>'account_code' FROM jsonb_array_elements(lines)) $$;
CREATE FUNCTION accounting.monthly_lines_valid(target TEXT,lines JSONB,is_balance BOOLEAN) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE ln JSONB; a RECORD; kv RECORD; total_debit NUMERIC:=0;total_credit NUMERIC:=0; BEGIN
 IF jsonb_typeof(lines)<>'array' OR jsonb_array_length(lines) NOT BETWEEN (CASE WHEN is_balance THEN 1 ELSE 2 END) AND (CASE WHEN is_balance THEN 1000 ELSE 100 END) THEN RETURN false;END IF;
 FOR ln IN SELECT value FROM jsonb_array_elements(lines) LOOP
  SELECT * INTO a FROM accounting.accounts WHERE book_id=target AND code=ln->>'account_code' AND active=1;
  IF NOT FOUND OR jsonb_typeof(ln->'auxiliary_values') IS DISTINCT FROM 'object' OR (NOT is_balance AND NOT (ln->'auxiliary_values' ?& ARRAY(SELECT jsonb_array_elements_text(a.auxiliary_requirements::jsonb)))) THEN RETURN false;END IF;
  FOR kv IN SELECT * FROM jsonb_each(ln->'auxiliary_values') LOOP
   IF kv.key NOT IN ('department','employee','counterparty','project') OR jsonb_typeof(kv.value)<>'string' OR NOT accounting.catalog_aux_valid(target,kv.key,kv.value#>>'{}') THEN RETURN false;END IF;
   IF kv.key IN ('counterparty','project') AND NOT EXISTS(SELECT 1 FROM accounting.company_master_entities m JOIN accounting.books b ON b.firm_id=m.firm_id AND b.company_id=m.company_id WHERE b.id=target AND m.kind=kv.key AND m.id=kv.value#>>'{}' AND m.active=1) THEN RETURN false;END IF;
  END LOOP;
  IF is_balance THEN
   IF jsonb_typeof(ln->'balance_cents') IS DISTINCT FROM 'number' OR coalesce(ln->>'balance_cents','')!~'^-?[0-9]+$' OR abs((ln->>'balance_cents')::numeric)>99999999999999 THEN RETURN false;END IF;
  ELSE
   IF jsonb_typeof(ln->'debit_cents') IS DISTINCT FROM 'number' OR jsonb_typeof(ln->'credit_cents') IS DISTINCT FROM 'number' OR coalesce(ln->>'debit_cents','')!~'^[0-9]+$' OR coalesce(ln->>'credit_cents','')!~'^[0-9]+$' OR ((ln->>'debit_cents')::numeric>0)=((ln->>'credit_cents')::numeric>0) THEN RETURN false;END IF;
   total_debit:=total_debit+(ln->>'debit_cents')::numeric;total_credit:=total_credit+(ln->>'credit_cents')::numeric;
  END IF;
 END LOOP;
 IF is_balance THEN
  RETURN (SELECT count(*)=count(DISTINCT jsonb_build_array(value->'account_code',value->'auxiliary_values')) FROM jsonb_array_elements(lines));
 END IF;
 RETURN total_debit=total_credit AND total_debit BETWEEN 1 AND 99999999999999;
END $$;

CREATE FUNCTION accounting.monthly_derived_lines(target TEXT,kind TEXT,source_kind TEXT,source_id TEXT) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD;p RECORD;body JSONB; config JSONB; result JSONB:='[]';ln JSONB;master JSONB;aux JSONB;kv RECORD;code TEXT;mapped_value TEXT;wanted TEXT;BEGIN
 SELECT b1.*,lp.ledger_id INTO b FROM accounting.books b1 JOIN accounting.ledger_periods lp ON lp.book_id=b1.id WHERE b1.id=target;
 IF NOT FOUND OR NOT accounting.book_allowed(target) THEN RETURN NULL;END IF;
 wanted:=CASE WHEN kind='opening' THEN to_char(to_date(b.period||'-01','YYYY-MM-DD')-interval '1 month','YYYY-MM') ELSE b.period END;
 IF source_kind='legacy_cutover' THEN
  SELECT c.*,m.ledger_id,m.status,m.history_end INTO p FROM accounting.migration_cutovers c JOIN accounting.migration_projects m ON m.id=c.project_id WHERE c.id=source_id AND c.firm_id=b.firm_id AND c.company_id=b.company_id AND m.ledger_id=b.ledger_id AND m.status='active' AND substr(m.history_end,1,7)=wanted;
  IF NOT FOUND OR NOT accounting.book_allowed(p.book_id) THEN RETURN NULL;END IF;
  body:=p.payload_json::jsonb;
  FOR ln IN SELECT value FROM jsonb_array_elements(body->'closing_balances') LOOP
   SELECT x->>'target_code' INTO code FROM jsonb_array_elements(body->'account_bindings') x WHERE x->>'source_code'=ln->>'account_code';
   aux:='{}';
   FOR kv IN SELECT * FROM jsonb_each_text(coalesce(ln->'auxiliaries','{}')) LOOP
    SELECT x->>'target_id' INTO mapped_value FROM jsonb_array_elements(body->'master_bindings') x WHERE x->>'kind'=kv.key AND x->>'source_code'=kv.value;
    aux:=aux||jsonb_build_object(kv.key,coalesce(mapped_value,kv.value));
   END LOOP;
   result:=result||jsonb_build_array(jsonb_build_object('account_code',code,'auxiliary_values',aux,'balance_cents',(ln->>'closing')::numeric*100,'record_id',ln->'record_id'));
  END LOOP;
 ELSIF source_kind='bridge_import' THEN
  SELECT * INTO p FROM accounting.bridge_import_previews WHERE id=source_id AND firm_id=b.firm_id AND company_id=b.company_id AND ledger_id=b.ledger_id AND state='confirmed';
  IF NOT FOUND OR NOT accounting.book_allowed(p.book_id) THEN RETURN NULL;END IF;
  SELECT payload_json::jsonb INTO config FROM accounting.bridge_configurations WHERE ledger_id=p.ledger_id AND version=p.configuration_version;
  FOR ln IN SELECT value FROM jsonb_array_elements(p.records_json::jsonb) WHERE value->>'kind'='balance' AND value->>'period'=wanted LOOP
   SELECT x->>'local_code' INTO code FROM jsonb_array_elements(config->'accounts') x WHERE x->>'external_code'=ln->>'code';
   result:=result||jsonb_build_array(jsonb_build_object('account_code',code,'auxiliary_values','{}'::jsonb,'balance_cents',((ln->>'debit')::numeric-(ln->>'credit')::numeric)*100,'record_id',ln->'external_id'));
  END LOOP;
 ELSE RETURN NULL;END IF;
 RETURN result;
END $$;

CREATE FUNCTION accounting.monthly_native_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD; original RECORD;v_plan RECORD;prior INTEGER; expected JSONB;lines JSONB;actual JSONB;BEGIN
 SELECT bk.*,lp.ledger_id,p.status INTO b FROM accounting.books bk JOIN accounting.ledger_periods lp ON lp.book_id=bk.id JOIN accounting.periods p ON p.book_id=bk.id AND p.period=bk.period WHERE bk.id=NEW.book_id AND bk.firm_id=NEW.firm_id AND bk.company_id=NEW.company_id;
 IF NOT FOUND OR b.status<>'open' THEN RAISE EXCEPTION 'Open same-company period required' USING ERRCODE='23514';END IF;
 IF TG_TABLE_NAME IN ('monthly_baselines','monthly_checks','recurring_plan_versions','recurring_plan_approvals','recurring_generations') THEN
  IF NEW.ledger_id IS DISTINCT FROM b.ledger_id THEN RAISE EXCEPTION 'Wrong long-term ledger' USING ERRCODE='23514';END IF;
 END IF;
 IF TG_TABLE_NAME='monthly_baselines' THEN
  IF NEW.original_sha256 IS DISTINCT FROM encode(sha256(convert_to(NEW.original_content,'UTF8')),'hex') THEN RAISE EXCEPTION 'Original content hash mismatch' USING ERRCODE='23514';END IF;
  IF NOT accounting.monthly_lines_valid(NEW.book_id,NEW.lines_json::jsonb,true) OR NEW.version<>(SELECT coalesce(max(version),0)+1 FROM accounting.monthly_baselines WHERE book_id=NEW.book_id AND kind=NEW.kind) OR NEW.period IS DISTINCT FROM (CASE WHEN NEW.kind='opening' THEN to_char(to_date(b.period||'-01','YYYY-MM-DD')-interval '1 month','YYYY-MM') ELSE b.period END) THEN RAISE EXCEPTION 'Invalid baseline dimensions, period or version' USING ERRCODE='23514';END IF;
  IF NEW.source_kind<>'manual' AND NEW.lines_json::jsonb IS DISTINCT FROM accounting.monthly_derived_lines(NEW.book_id,NEW.kind,NEW.source_kind,NEW.source_id) THEN RAISE EXCEPTION 'Imported evidence must be preserved exactly' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='monthly_baseline_confirmations' THEN
  SELECT * INTO original FROM accounting.monthly_baselines WHERE id=NEW.baseline_id AND book_id=NEW.book_id;
  IF NOT FOUND OR original.created_by=NEW.confirmed_by OR original.version<>(SELECT max(version) FROM accounting.monthly_baselines WHERE book_id=NEW.book_id AND kind=original.kind) OR NOT accounting.monthly_lines_valid(NEW.book_id,original.lines_json::jsonb,true) THEN RAISE EXCEPTION 'Latest baseline and independent review required' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='monthly_checks' THEN
  IF NEW.inputs_json::jsonb IS DISTINCT FROM accounting.monthly_current_inputs(NEW.book_id,NEW.opening_id,NEW.closing_id) OR accounting.monthly_report_valid(NEW.book_id,NEW.opening_id,NEW.closing_id,NEW.inputs_json::jsonb,NEW.report_json::jsonb) IS DISTINCT FROM true THEN RAISE EXCEPTION 'Monthly check must match current deterministic facts and amounts' USING ERRCODE='23514';END IF;
  IF NEW.version<>(SELECT coalesce(max(version),0)+1 FROM accounting.monthly_checks WHERE book_id=NEW.book_id) OR NOT EXISTS(SELECT 1 FROM accounting.monthly_baselines x JOIN accounting.monthly_baseline_confirmations c ON c.baseline_id=x.id WHERE x.id=NEW.opening_id AND x.book_id=NEW.book_id AND x.kind='opening' AND x.version=(SELECT max(version) FROM accounting.monthly_baselines WHERE book_id=x.book_id AND kind=x.kind)) OR NOT EXISTS(SELECT 1 FROM accounting.monthly_baselines x JOIN accounting.monthly_baseline_confirmations c ON c.baseline_id=x.id WHERE x.id=NEW.closing_id AND x.book_id=NEW.book_id AND x.kind='closing' AND x.version=(SELECT max(version) FROM accounting.monthly_baselines WHERE book_id=x.book_id AND kind=x.kind))
  OR NEW.inputs_json::jsonb->>'book_id' IS DISTINCT FROM NEW.book_id OR NEW.inputs_json::jsonb->>'period' IS DISTINCT FROM b.period OR NEW.inputs_json::jsonb->>'opening_id' IS DISTINCT FROM NEW.opening_id OR NEW.inputs_json::jsonb->>'closing_id' IS DISTINCT FROM NEW.closing_id OR NEW.report_json::jsonb->>'input_fingerprint' IS DISTINCT FROM NEW.input_fingerprint OR NEW.report_json::jsonb->'official_report' IS DISTINCT FROM 'false'::jsonb THEN RAISE EXCEPTION 'Scoped confirmed references required; never an official report' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='monthly_check_closures' THEN
  SELECT * INTO original FROM accounting.monthly_checks WHERE id=NEW.check_id AND book_id=NEW.book_id;
  IF NOT FOUND OR original.inputs_json::jsonb IS DISTINCT FROM accounting.monthly_current_inputs(NEW.book_id,original.opening_id,original.closing_id) OR EXISTS(SELECT 1 FROM accounting.monthly_baselines n WHERE n.book_id=NEW.book_id AND ((n.kind='opening' AND n.version>(SELECT version FROM accounting.monthly_baselines WHERE id=original.opening_id)) OR (n.kind='closing' AND n.version>(SELECT version FROM accounting.monthly_baselines WHERE id=original.closing_id)))) THEN RAISE EXCEPTION 'Cannot close stale monthly check' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME='recurring_plan_versions' THEN
  SELECT coalesce(max(version),0)+1 INTO prior FROM accounting.recurring_plan_versions WHERE plan_id=NEW.plan_id;
  IF NEW.version<>prior OR EXISTS(SELECT 1 FROM accounting.recurring_plan_versions WHERE plan_id=NEW.plan_id AND (ledger_id<>NEW.ledger_id OR firm_id<>NEW.firm_id OR company_id<>NEW.company_id)) OR NEW.start_period !~'^[0-9]{4}-(0[1-9]|1[0-2])$' OR (NEW.end_period IS NOT NULL AND NEW.end_period !~'^[0-9]{4}-(0[1-9]|1[0-2])$') OR NOT accounting.monthly_lines_valid(NEW.book_id,NEW.lines_json::jsonb,false) OR NEW.catalog_json::jsonb IS DISTINCT FROM accounting.monthly_catalog(NEW.book_id,NEW.lines_json::jsonb) THEN RAISE EXCEPTION 'Explicit balanced amounts and current company catalog required' USING ERRCODE='23514';END IF;
 ELSIF TG_TABLE_NAME IN ('recurring_plan_approvals','recurring_generations') THEN
  SELECT * INTO v_plan FROM accounting.recurring_plan_versions WHERE plan_id=NEW.plan_id AND version=NEW.version AND ledger_id=NEW.ledger_id;
  IF NOT FOUND OR NEW.version<>(SELECT max(version) FROM accounting.recurring_plan_versions WHERE plan_id=NEW.plan_id) OR NOT accounting.monthly_lines_valid(NEW.book_id,v_plan.lines_json::jsonb,false) OR v_plan.catalog_json::jsonb IS DISTINCT FROM accounting.monthly_catalog(NEW.book_id,v_plan.lines_json::jsonb) THEN RAISE EXCEPTION 'Latest approved catalog-compatible plan required' USING ERRCODE='23514';END IF;
  IF TG_TABLE_NAME='recurring_plan_approvals' THEN
   IF v_plan.created_by=NEW.approved_by THEN RAISE EXCEPTION 'Independent plan approval required' USING ERRCODE='23514';END IF;
  ELSE
   IF NEW.period IS DISTINCT FROM b.period OR b.period<v_plan.start_period OR (v_plan.end_period IS NOT NULL AND b.period>v_plan.end_period) OR NOT EXISTS(SELECT 1 FROM accounting.recurring_plan_approvals WHERE plan_id=NEW.plan_id AND version=NEW.version)
   OR NOT EXISTS(SELECT 1 FROM accounting.sources s JOIN accounting.drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=NEW.source_id AND s.book_id=NEW.book_id AND d.id=NEW.draft_id AND d.status='draft' AND d.version=1 AND s.source_kind='business' AND s.external_id='monthly:'||NEW.plan_id||':'||NEW.period AND s.date=NEW.period||'-'||lpad(v_plan.day::text,2,'0') AND s.description=v_plan.description AND d.description=v_plan.description AND s.evidence_ref=v_plan.evidence_ref AND s.amount_cents=(SELECT sum((x->>'debit_cents')::bigint) FROM jsonb_array_elements(v_plan.lines_json::jsonb) x)) THEN RAISE EXCEPTION 'One unreviewed draft per plan and month required' USING ERRCODE='23514';END IF;
   SELECT coalesce(jsonb_agg(jsonb_build_object('account_code',l.account_code,'debit_cents',l.debit_cents,'credit_cents',l.credit_cents,'auxiliary_values',coalesce(a.values_json::jsonb,'{}')) ORDER BY l.ordinal),'[]') INTO actual FROM accounting.draft_lines l LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.ordinal=l.ordinal WHERE l.draft_id=NEW.draft_id AND l.book_id=NEW.book_id;
   IF actual IS DISTINCT FROM v_plan.lines_json::jsonb THEN RAISE EXCEPTION 'Generated lines must equal approved fixed plan' USING ERRCODE='23514';END IF;
  END IF;
 END IF;
 RETURN NEW;
END $$;

DO $$ DECLARE tab TEXT;write_scope TEXT;actor_col TEXT;read_scope TEXT;BEGIN
 FOREACH tab IN ARRAY ARRAY['monthly_baselines','monthly_baseline_confirmations','monthly_checks','monthly_check_closures','recurring_plan_versions','recurring_plan_approvals','recurring_generations'] LOOP
  EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',tab);EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',tab);
  EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',tab);EXECUTE format('GRANT SELECT,INSERT ON accounting.%I TO accounting_app',tab);
  read_scope:=CASE WHEN tab IN ('recurring_plan_versions','recurring_plan_approvals') THEN 'accounting.ledger_visible(ledger_id)' ELSE 'accounting.book_allowed(book_id)' END;
  write_scope:=CASE WHEN tab IN ('monthly_baselines','recurring_generations') THEN 'accounting.book_allowed(book_id,true)' ELSE 'accounting.catalog_manage(book_id)' END;
  actor_col:=CASE WHEN tab='monthly_baseline_confirmations' THEN 'confirmed_by' WHEN tab='monthly_check_closures' THEN 'closed_by' WHEN tab='recurring_plan_approvals' THEN 'approved_by' ELSE 'created_by' END;
  EXECUTE format('CREATE POLICY monthly_read ON accounting.%I FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND %s)',tab,read_scope);
  EXECUTE format('CREATE POLICY monthly_create ON accounting.%I FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND %I=accounting.actor_user() AND %s)',tab,actor_col,write_scope);
  EXECUTE format('CREATE TRIGGER monthly_native BEFORE INSERT ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.monthly_native_guard()',tab);
  EXECUTE format('CREATE TRIGGER monthly_immutable BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',tab);
 END LOOP;
END $$;
REVOKE ALL ON FUNCTION accounting.monthly_catalog(TEXT,JSONB),accounting.monthly_lines_valid(TEXT,JSONB,BOOLEAN),accounting.monthly_derived_lines(TEXT,TEXT,TEXT,TEXT),accounting.monthly_native_guard() FROM PUBLIC,accounting_app,accounting_worker;

CREATE FUNCTION accounting.monthly_current_inputs(target_book TEXT,target_opening TEXT,target_closing TEXT) RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ WITH verified_exports AS (SELECT x.* FROM accounting.exports x WHERE x.book_id=target_book AND EXISTS(SELECT 1 FROM delivery_manifests m JOIN delivery_receipts r ON r.export_id=m.export_id AND r.book_id=m.book_id
      WHERE m.export_id=x.id AND m.book_id=x.book_id AND jsonb_extract_path_text((r.body_json)::jsonb,'book_id')=jsonb_extract_path_text((m.payload_json)::jsonb,'book_id') AND jsonb_extract_path_text((r.body_json)::jsonb,'company_id')=jsonb_extract_path_text((m.payload_json)::jsonb,'company_id') AND jsonb_extract_path_text((r.body_json)::jsonb,'period')=jsonb_extract_path_text((m.payload_json)::jsonb,'period') AND jsonb_extract_path_text((r.body_json)::jsonb,'currency')=jsonb_extract_path_text((m.payload_json)::jsonb,'currency')
      AND NOT EXISTS(SELECT 1 FROM delivery_receipts newer WHERE newer.export_id=r.export_id AND newer.book_id=r.book_id AND (newer.created_at>r.created_at OR newer.created_at=r.created_at AND newer.id>r.id))
      AND (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((r.body_json)::jsonb,'items'))='array' THEN jsonb_extract_path((r.body_json)::jsonb,'items') ELSE '[]'::jsonb END) i(value))=(SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((m.payload_json)::jsonb,'items'))='array' THEN jsonb_extract_path((m.payload_json)::jsonb,'items') ELSE '[]'::jsonb END) expected(value))
      AND (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((r.body_json)::jsonb,'items'))='array' THEN jsonb_extract_path((r.body_json)::jsonb,'items') ELSE '[]'::jsonb END) i(value))=x.count
      AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((r.body_json)::jsonb,'items'))='array' THEN jsonb_extract_path((r.body_json)::jsonb,'items') ELSE '[]'::jsonb END) i(value)
        WHERE jsonb_extract_path_text((i.value)::jsonb,'status') IS NULL OR jsonb_extract_path_text((i.value)::jsonb,'status')<>'accepted' OR trim(COALESCE(jsonb_extract_path_text((i.value)::jsonb,'external_voucher_id'),''))=''
        OR (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((r.body_json)::jsonb,'items'))='array' THEN jsonb_extract_path((r.body_json)::jsonb,'items') ELSE '[]'::jsonb END) duplicate(value) WHERE jsonb_extract_path_text((duplicate.value)::jsonb,'draft_id')=jsonb_extract_path_text((i.value)::jsonb,'draft_id'))<>1
        OR (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((r.body_json)::jsonb,'items'))='array' THEN jsonb_extract_path((r.body_json)::jsonb,'items') ELSE '[]'::jsonb END) duplicate(value) WHERE jsonb_extract_path_text((duplicate.value)::jsonb,'external_voucher_id')=jsonb_extract_path_text((i.value)::jsonb,'external_voucher_id'))<>1
        OR EXISTS(SELECT 1 FROM delivery_receipt_items h WHERE h.book_id=r.book_id AND h.receipt_id<>r.id AND
          (h.claimed_draft_id<>jsonb_extract_path_text((i.value)::jsonb,'draft_id') AND h.external_voucher_id=jsonb_extract_path_text((i.value)::jsonb,'external_voucher_id')
           OR h.claimed_draft_id=jsonb_extract_path_text((i.value)::jsonb,'draft_id') AND trim(COALESCE(h.external_voucher_id,''))<>'' AND h.external_voucher_id<>jsonb_extract_path_text((i.value)::jsonb,'external_voucher_id')))
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((m.payload_json)::jsonb,'items'))='array' THEN jsonb_extract_path((m.payload_json)::jsonb,'items') ELSE '[]'::jsonb END) expected(value) WHERE jsonb_extract_path_text((expected.value)::jsonb,'draft_id')=jsonb_extract_path_text((i.value)::jsonb,'draft_id') AND (jsonb_extract_path_text((i.value)::jsonb,'date')) IS NOT DISTINCT FROM (jsonb_extract_path_text((expected.value)::jsonb,'date')) AND (jsonb_extract_path_text((i.value)::jsonb,'amount')) IS NOT DISTINCT FROM (jsonb_extract_path_text((expected.value)::jsonb,'amount')) AND (jsonb_extract_path_text((i.value)::jsonb,'description')) IS NOT DISTINCT FROM (jsonb_extract_path_text((expected.value)::jsonb,'description'))
          AND (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((i.value)::jsonb,'lines'))='array' THEN jsonb_extract_path((i.value)::jsonb,'lines') ELSE '[]'::jsonb END) line(value))=(SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((expected.value)::jsonb,'lines'))='array' THEN jsonb_extract_path((expected.value)::jsonb,'lines') ELSE '[]'::jsonb END) wanted(value))
          AND (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((i.value)::jsonb,'lines'))='array' THEN jsonb_extract_path((i.value)::jsonb,'lines') ELSE '[]'::jsonb END) line(value))>=2
          AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((i.value)::jsonb,'lines'))='array' THEN jsonb_extract_path((i.value)::jsonb,'lines') ELSE '[]'::jsonb END) line(value) WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((expected.value)::jsonb,'lines'))='array' THEN jsonb_extract_path((expected.value)::jsonb,'lines') ELSE '[]'::jsonb END) wanted(value)
            WHERE (jsonb_extract_path_text((line.value)::jsonb,'ordinal')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'ordinal')) AND (jsonb_extract_path_text((line.value)::jsonb,'account_code')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'account_code')) AND (jsonb_extract_path_text((line.value)::jsonb,'debit')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'debit')) AND (jsonb_extract_path_text((line.value)::jsonb,'credit')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'credit')) AND jsonb_typeof(line.value->'auxiliary_values')='object' AND (jsonb_extract_path_text((line.value)::jsonb,'auxiliary_values','department')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'auxiliary_values','department')) AND (jsonb_extract_path_text((line.value)::jsonb,'auxiliary_values','employee')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'auxiliary_values','employee')) AND (jsonb_extract_path_text((line.value)::jsonb,'auxiliary_values','counterparty')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'auxiliary_values','counterparty')) AND (jsonb_extract_path_text((line.value)::jsonb,'auxiliary_values','project')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'auxiliary_values','project')) AND line.value->'auxiliary_values'=wanted.value->'auxiliary_values')
            OR (SELECT count(*) FROM jsonb_array_elements(CASE WHEN jsonb_typeof(jsonb_extract_path((i.value)::jsonb,'lines'))='array' THEN jsonb_extract_path((i.value)::jsonb,'lines') ELSE '[]'::jsonb END) duplicate_line(value) WHERE jsonb_extract_path_text((duplicate_line.value)::jsonb,'ordinal')=jsonb_extract_path_text((line.value)::jsonb,'ordinal'))<>1)))
    )),
    verified_drafts AS(SELECT value->>'draft_id' AS draft_id FROM verified_exports x JOIN accounting.delivery_manifests m ON m.export_id=x.id CROSS JOIN jsonb_array_elements(m.payload_json::jsonb->'items')),
    facts AS(SELECT d.id,d.version,d.status,l.ordinal,l.account_code,l.debit_cents,l.credit_cents,coalesce(a.values_json::jsonb,'{}') AS aux,
      CASE WHEN d.status='draft' THEN 'unreviewed' WHEN d.status='approved' THEN 'approved_pending_delivery' WHEN EXISTS(SELECT 1 FROM verified_drafts vd WHERE vd.draft_id=d.id) THEN 'verified_delivery' ELSE 'exported_pending_receipt' END AS grp
      FROM accounting.drafts d JOIN accounting.sources s ON s.id=d.source_id AND s.book_id=d.book_id JOIN accounting.draft_lines l ON l.draft_id=d.id AND l.book_id=d.book_id LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal
      WHERE d.book_id=target_book AND NOT accounting.source_inactive(s.book_id,s.id) AND NOT EXISTS(SELECT 1 FROM accounting.ai_bank_links link WHERE link.bank_source_id=s.id AND link.book_id=s.book_id)),
    receipts AS(SELECT r.* FROM accounting.delivery_receipts r WHERE r.book_id=target_book AND r.id=(SELECT r2.id FROM accounting.delivery_receipts r2 WHERE r2.export_id=r.export_id ORDER BY r2.created_at DESC,r2.id DESC LIMIT 1))
    SELECT jsonb_build_object('book_id',b.id,'period',b.period,'opening_id',target_opening,'closing_id',target_closing,
      'facts',coalesce((SELECT jsonb_agg(jsonb_build_object('draft_id',id,'version',version,'status',status,'ordinal',ordinal,'account_code',account_code,'debit_cents',debit_cents,'credit_cents',credit_cents,'auxiliary_values',aux,'group',grp) ORDER BY id,ordinal) FROM facts),'[]'),
      'receipts',coalesce((SELECT jsonb_agg(jsonb_build_object('receipt_id',id,'export_id',export_id,'body_hash',request_fingerprint) ORDER BY created_at,id) FROM receipts),'[]')) FROM accounting.books b WHERE b.id=target_book $$;
REVOKE ALL ON FUNCTION accounting.monthly_current_inputs(TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker;
ALTER FUNCTION accounting.closing_fingerprint(TEXT) RENAME TO closing_fingerprint_stage16;
CREATE FUNCTION accounting.closing_fingerprint(target_book TEXT) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE extra JSONB;old_hash TEXT;BEGIN old_hash:=accounting.closing_fingerprint_stage16(target_book); SELECT jsonb_build_object('monthly_baselines',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM accounting.monthly_baselines t WHERE t.book_id=b.id),'monthly_baseline_confirmations',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM accounting.monthly_baseline_confirmations t WHERE t.book_id=b.id),'monthly_checks',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM accounting.monthly_checks t WHERE t.book_id=b.id),'monthly_check_closures',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM accounting.monthly_check_closures t WHERE t.book_id=b.id),'recurring_plan_versions',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM accounting.recurring_plan_versions t WHERE t.ledger_id IN(SELECT ledger_id FROM ledger_periods WHERE book_id=b.id)),'recurring_plan_approvals',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM accounting.recurring_plan_approvals t WHERE t.ledger_id IN(SELECT ledger_id FROM ledger_periods WHERE book_id=b.id)),'recurring_generations',(SELECT coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]') FROM accounting.recurring_generations t WHERE t.book_id=b.id)) INTO extra FROM accounting.books b WHERE b.id=target_book; RETURN encode(sha256(convert_to(old_hash||extra::text,'UTF8')),'hex');END $$;
ALTER FUNCTION accounting.closing_issues(TEXT) RENAME TO closing_issues_stage16;
CREATE FUNCTION accounting.closing_issues(target_book TEXT) RETURNS TABLE(category TEXT,object_type TEXT,object_id TEXT,detail TEXT) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'current scoped book reviewer required' USING ERRCODE='42501';END IF; RETURN QUERY SELECT * FROM accounting.closing_issues_stage16(target_book) UNION ALL SELECT m.category,m.object_type,m.object_id,m.detail FROM (SELECT c.book_id,'monthly_reference' AS category,'monthly_check' AS object_type,c.id AS object_id,'月度外部余额检查尚未人工核对，或依据已变化，须重新检查' AS detail FROM monthly_checks c
    WHERE c.version=(SELECT max(version) FROM monthly_checks WHERE book_id=c.book_id) AND (NOT EXISTS(SELECT 1 FROM monthly_check_closures x WHERE x.check_id=c.id) OR NOT(c.inputs_json::jsonb=accounting.monthly_current_inputs(c.book_id,c.opening_id,c.closing_id)) OR EXISTS(SELECT 1 FROM monthly_baselines o WHERE o.book_id=c.book_id AND o.kind='opening' AND o.version>(SELECT version FROM monthly_baselines WHERE id=c.opening_id)) OR EXISTS(SELECT 1 FROM monthly_baselines z WHERE z.book_id=c.book_id AND z.kind='closing' AND z.version>(SELECT version FROM monthly_baselines WHERE id=c.closing_id)))
    UNION ALL SELECT m.book_id,'monthly_reference','monthly_baseline',m.id,'月度外部余额尚未独立确认或未形成对应检查' FROM monthly_baselines m
    WHERE m.version=(SELECT max(version) FROM monthly_baselines WHERE book_id=m.book_id AND kind=m.kind) AND (NOT EXISTS(SELECT 1 FROM monthly_baseline_confirmations x WHERE x.baseline_id=m.id) OR NOT EXISTS(SELECT 1 FROM monthly_checks c WHERE c.book_id=m.book_id AND (c.opening_id=m.id OR c.closing_id=m.id)))
    UNION ALL SELECT b.id,'recurring_pending','recurring_plan',p.plan_id,'本月有已审批的固定事项计划尚未生成草稿，请核对实际发生情况' FROM recurring_plan_versions p JOIN recurring_plan_approvals a ON a.plan_id=p.plan_id AND a.version=p.version JOIN ledger_periods l ON l.ledger_id=p.ledger_id JOIN books b ON b.id=l.book_id
    WHERE p.version=(SELECT max(x.version) FROM recurring_plan_versions x JOIN recurring_plan_approvals reviewed ON reviewed.plan_id=x.plan_id AND reviewed.version=x.version WHERE x.plan_id=p.plan_id) AND b.period>=p.start_period AND (p.end_period IS NULL OR b.period<=p.end_period) AND NOT EXISTS(SELECT 1 FROM recurring_generations g WHERE g.plan_id=p.plan_id AND g.book_id=b.id)) m WHERE m.book_id=target_book;END $$;
REVOKE ALL ON FUNCTION accounting.closing_fingerprint_stage16(TEXT),accounting.closing_issues_stage16(TEXT),accounting.closing_fingerprint(TEXT),accounting.closing_issues(TEXT) FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.closing_fingerprint(TEXT),accounting.closing_issues(TEXT) TO accounting_app;

CREATE FUNCTION accounting.monthly_signed_money(value BIGINT) RETURNS TEXT LANGUAGE sql IMMUTABLE SET search_path=pg_catalog,accounting,pg_temp AS $$ SELECT CASE WHEN value IS NULL THEN NULL ELSE CASE WHEN value<0 THEN '-' ELSE '' END||accounting.delivery_cents(abs(value)) END $$;
CREATE FUNCTION accounting.monthly_report_valid(target TEXT,opening TEXT,closing TEXT,inputs JSONB,report JSONB) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE ln JSONB;dims JSONB;op JSONB;cl JSONB;baseline BIGINT;actual BIGINT;expected BIGINT;group_name TEXT;debit BIGINT;credit BIGINT;unknowns INTEGER:=0;differences INTEGER:=0;expected_count INTEGER;BEGIN
 SELECT lines_json::jsonb INTO op FROM accounting.monthly_baselines WHERE id=opening AND book_id=target;
 SELECT lines_json::jsonb INTO cl FROM accounting.monthly_baselines WHERE id=closing AND book_id=target;
 IF op IS NULL OR cl IS NULL OR jsonb_typeof(report->'lines') IS DISTINCT FROM 'array' THEN RETURN false;END IF;
 SELECT count(*) INTO expected_count FROM(SELECT value->>'account_code',value->'auxiliary_values' FROM jsonb_array_elements(op) UNION SELECT value->>'account_code',value->'auxiliary_values' FROM jsonb_array_elements(cl) UNION SELECT value->>'account_code',value->'auxiliary_values' FROM jsonb_array_elements(inputs->'facts')) k;
 IF jsonb_array_length(report->'lines')<>expected_count OR (SELECT count(*) FROM jsonb_array_elements(report->'lines'))<>(SELECT count(DISTINCT jsonb_build_array(value->'account_code',value->'auxiliary_values')) FROM jsonb_array_elements(report->'lines')) THEN RETURN false;END IF;
 FOR ln IN SELECT value FROM jsonb_array_elements(report->'lines') LOOP
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(op||cl||(inputs->'facts')) x WHERE x->>'account_code'=ln->>'account_code' AND x->'auxiliary_values'=ln->'auxiliary_values') THEN RETURN false;END IF;
  SELECT (value->>'balance_cents')::bigint INTO baseline FROM jsonb_array_elements(op) WHERE value->>'account_code'=ln->>'account_code' AND value->'auxiliary_values'=ln->'auxiliary_values';
  SELECT (value->>'balance_cents')::bigint INTO actual FROM jsonb_array_elements(cl) WHERE value->>'account_code'=ln->>'account_code' AND value->'auxiliary_values'=ln->'auxiliary_values';
  expected:=baseline;
  FOREACH group_name IN ARRAY ARRAY['verified_delivery','approved_pending_delivery','exported_pending_receipt','unreviewed'] LOOP
   SELECT coalesce(sum((value->>'debit_cents')::bigint),0),coalesce(sum((value->>'credit_cents')::bigint),0) INTO debit,credit FROM jsonb_array_elements(inputs->'facts') WHERE value->>'account_code'=ln->>'account_code' AND (value->'auxiliary_values'=ln->'auxiliary_values' OR ln->'auxiliary_values'='{}'::jsonb AND EXISTS(SELECT 1 FROM jsonb_array_elements(op||cl) x WHERE x->>'account_code'=ln->>'account_code' AND x->'auxiliary_values'='{}'::jsonb)) AND value->>'group'=group_name;
   IF ln->group_name IS DISTINCT FROM jsonb_build_object('debit',accounting.delivery_cents(debit),'credit',accounting.delivery_cents(credit),'net',accounting.monthly_signed_money(debit-credit)) THEN RETURN false;END IF;
   IF group_name='verified_delivery' THEN expected:=baseline+debit-credit;END IF;
  END LOOP;
  IF ln->>'baseline' IS DISTINCT FROM accounting.monthly_signed_money(baseline) OR ln->>'actual' IS DISTINCT FROM accounting.monthly_signed_money(actual) OR ln->>'reference_closing' IS DISTINCT FROM accounting.monthly_signed_money(expected) OR ln->>'difference' IS DISTINCT FROM accounting.monthly_signed_money(actual-expected) OR ln->>'baseline_state' IS DISTINCT FROM (CASE WHEN baseline IS NULL THEN 'unknown' ELSE 'confirmed_reference' END) THEN RETURN false;END IF;
  IF actual IS NULL OR expected IS NULL THEN unknowns:=unknowns+1;ELSIF actual<>expected THEN differences:=differences+1;END IF;
 END LOOP;
 RETURN coalesce(report->>'unknown_count'=unknowns::text AND report->>'difference_count'=differences::text AND report->>'currency'='CNY' AND report->>'coverage'='listed_dimensions_only',false);
END $$;
REVOKE ALL ON FUNCTION accounting.monthly_signed_money(BIGINT),accounting.monthly_report_valid(TEXT,TEXT,TEXT,JSONB,JSONB) FROM PUBLIC,accounting_app,accounting_worker;

DO $$ DECLARE tab TEXT;BEGIN
 FOREACH tab IN ARRAY ARRAY['monthly_baselines','monthly_baseline_confirmations','monthly_checks','monthly_check_closures','recurring_plan_versions','recurring_plan_approvals','recurring_generations'] LOOP
  EXECUTE format('CREATE TRIGGER batch2_company_write_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard()',tab);
  EXECUTE format('CREATE TRIGGER batch2_statement_gate BEFORE INSERT OR UPDATE OR DELETE ON accounting.%I FOR EACH STATEMENT EXECUTE FUNCTION accounting.compatibility_statement_gate()',tab);
 END LOOP;
END $$;
