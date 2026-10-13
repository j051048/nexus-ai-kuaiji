-- Each rule is explicitly scoped to a book/period; original reviewed samples
-- cannot be opened by a grant to another book, including the same company.
CREATE TABLE accounting.learning_samples (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL REFERENCES accounting.books(id),period TEXT NOT NULL,
 draft_id TEXT NOT NULL UNIQUE,source_id TEXT NOT NULL,reviewed_version INTEGER NOT NULL CHECK(reviewed_version>0),
 approved_version INTEGER NOT NULL CHECK(approved_version=reviewed_version+1),
 snapshot_json TEXT NOT NULL CHECK(jsonb_typeof(snapshot_json::jsonb)='object'),original_json TEXT NOT NULL CHECK(jsonb_typeof(original_json::jsonb)='object'),
 final_json TEXT NOT NULL CHECK(jsonb_typeof(final_json::jsonb)='object'),corrections_json TEXT NOT NULL CHECK(jsonb_typeof(corrections_json::jsonb)='array'),
 evidence_fingerprint TEXT NOT NULL CHECK(length(evidence_fingerprint)=64),sample_fingerprint TEXT NOT NULL CHECK(length(sample_fingerprint)=64),
 corrected INTEGER NOT NULL CHECK(corrected IN (0,1)),reviewed_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES accounting.drafts(id,book_id),FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id),UNIQUE(id,book_id)
);
CREATE INDEX idx_learning_samples_book ON accounting.learning_samples(book_id,created_at DESC,id DESC);
CREATE TABLE accounting.learning_rules (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL REFERENCES accounting.books(id),period TEXT NOT NULL,
 current_version INTEGER NOT NULL CHECK(current_version>0),state_version INTEGER NOT NULL CHECK(state_version>0),
 status TEXT NOT NULL CHECK(status IN ('draft','published','retired')),evaluation_id TEXT,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id),UNIQUE(id,book_id)
);
CREATE INDEX idx_learning_rules_book ON accounting.learning_rules(book_id,status);
CREATE TABLE accounting.learning_rule_versions (
 rule_id TEXT NOT NULL,book_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),
 definition_json TEXT NOT NULL CHECK(jsonb_typeof(definition_json::jsonb)='object'),definition_fingerprint TEXT NOT NULL CHECK(length(definition_fingerprint)=64),
 sample_ids_json TEXT NOT NULL CHECK(jsonb_typeof(sample_ids_json::jsonb)='array' AND jsonb_array_length(sample_ids_json::jsonb) BETWEEN 1 AND 100),reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,PRIMARY KEY(rule_id,version),
 FOREIGN KEY(rule_id,book_id) REFERENCES accounting.learning_rules(id,book_id)
);
CREATE TABLE accounting.learning_rule_evaluations (
 id TEXT PRIMARY KEY,rule_id TEXT NOT NULL,book_id TEXT NOT NULL,rule_version INTEGER NOT NULL,
 negative_sample_ids_json TEXT NOT NULL CHECK(jsonb_typeof(negative_sample_ids_json::jsonb)='array' AND jsonb_array_length(negative_sample_ids_json::jsonb) BETWEEN 1 AND 100),
 input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),report_json TEXT NOT NULL CHECK(jsonb_typeof(report_json::jsonb)='object'),
 db_input_fingerprint TEXT NOT NULL CHECK(length(db_input_fingerprint)=32),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(rule_id,book_id) REFERENCES accounting.learning_rules(id,book_id),FOREIGN KEY(rule_id,rule_version) REFERENCES accounting.learning_rule_versions(rule_id,version)
);

-- Pure, bounded money and journal representations mirror Python integer cents.
-- These helpers are invoker functions: table reads remain subject to current RLS.
CREATE FUNCTION accounting.learning_money_cents(value TEXT) RETURNS BIGINT LANGUAGE plpgsql IMMUTABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE result NUMERIC; BEGIN
 IF value IS NULL OR value !~ '^(0|[1-9][0-9]{0,11})([.][0-9]{1,2})?$' THEN RETURN NULL; END IF;
 result:=value::numeric*100;
 IF result>99999999999999 THEN RETURN NULL; END IF;
 RETURN result::bigint; END $$;
CREATE FUNCTION accounting.learning_canonical_lines(lines JSONB) RETURNS JSONB LANGUAGE plpgsql IMMUTABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE item JSONB; aux JSONB; result JSONB:='[]'::jsonb; debit BIGINT; credit BIGINT; BEGIN
 IF jsonb_typeof(lines) IS DISTINCT FROM 'array' THEN RETURN NULL; END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(lines) LOOP
  aux:=COALESCE(item->'auxiliary_values','{}'::jsonb);
  debit:=accounting.learning_money_cents(item->>'debit');credit:=accounting.learning_money_cents(item->>'credit');
  IF jsonb_typeof(item) IS DISTINCT FROM 'object' OR jsonb_typeof(item->'account_code') IS DISTINCT FROM 'string'
   OR debit IS NULL OR credit IS NULL OR jsonb_typeof(aux) IS DISTINCT FROM 'object'
  THEN RETURN NULL; END IF;
  result:=result||jsonb_build_array(jsonb_build_object('account_code',item->>'account_code','debit',debit,'credit',credit,'aux',aux));
 END LOOP;
 RETURN COALESCE((SELECT jsonb_agg(value ORDER BY value->>'account_code',(value->>'debit')::bigint,(value->>'credit')::bigint,(value->'aux')::text) FROM jsonb_array_elements(result)),'[]'::jsonb);
 END $$;
CREATE FUNCTION accounting.learning_matches(definition JSONB,snapshot JSONB) RETURNS BOOLEAN LANGUAGE sql IMMUTABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT COALESCE(definition->>'source_kind'=snapshot->>'source_kind'
  AND definition->'conditions'->>'business_type'=snapshot->>'business_type'
  AND definition->'conditions'->>'direction'=snapshot->'payload'->>'direction'
  AND definition->'conditions'->>'payment_status'=snapshot->'payload'->>'payment_status'
  AND (definition->'conditions'->>'category' IS NULL OR definition->'conditions'->>'category'=snapshot->'payload'->>'category')
  AND definition->'conditions'->>'counterparty'=snapshot->'payload'->>'counterparty',false)
 $$;
CREATE FUNCTION accounting.learning_recognized(target_event TEXT,target_fact INTEGER,target_amount BIGINT) RETURNS JSONB LANGUAGE plpgsql STABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE document JSONB; documents INTEGER:=0; gross BIGINT:=0;net BIGINT:=0;tax BIGINT:=0; g BIGINT;n BIGINT;t BIGINT; BEGIN
 FOR document IN SELECT docs.value FROM accounting.ai_runs r CROSS JOIN LATERAL jsonb_array_elements(
  CASE WHEN jsonb_typeof(r.result_json::jsonb->'documents')='array' THEN r.result_json::jsonb->'documents' ELSE '[]'::jsonb END) docs
  WHERE r.event_id=target_event AND r.kind='ocr' AND r.status='succeeded' AND r.input_version=target_fact
   AND NOT EXISTS(SELECT 1 FROM accounting.case_supersessions retired WHERE retired.old_attachment_id=r.attachment_id AND retired.event_id=r.event_id) AND NOT EXISTS(
   SELECT 1 FROM accounting.ai_runs newer WHERE newer.event_id=r.event_id AND newer.attachment_id=r.attachment_id AND newer.kind='ocr'
    AND (newer.created_at>r.created_at OR newer.created_at=r.created_at AND newer.id>r.id)) LOOP
  documents:=documents+1;g:=accounting.learning_money_cents(document->>'gross_amount');n:=accounting.learning_money_cents(document->>'net_amount');t:=accounting.learning_money_cents(document->>'tax_amount');
  IF document->>'currency' IS DISTINCT FROM 'CNY' OR g IS NULL OR n IS NULL OR t IS NULL THEN RETURN '{}'::jsonb; END IF;
  gross:=gross+g;net:=net+n;tax:=tax+t;
 END LOOP;
 IF documents=0 OR gross<>target_amount OR net+tax<>target_amount THEN RETURN '{}'::jsonb; END IF;
 RETURN jsonb_build_object('net',(net/100)::text||'.'||lpad((net%100)::text,2,'0'),'tax',(tax/100)::text||'.'||lpad((tax%100)::text,2,'0'));
 END $$;
CREATE FUNCTION accounting.learning_sample_truth(target_book TEXT,target_draft TEXT,target_source TEXT,snapshot JSONB,final_value JSONB) RETURNS BOOLEAN LANGUAGE plpgsql STABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE current_lines JSONB; source_row RECORD; draft_row RECORD; event_row RECORD; expected_payload JSONB; BEGIN
 IF NOT accounting.book_allowed(target_book) THEN RETURN false; END IF;
 SELECT * INTO draft_row FROM accounting.drafts WHERE id=target_draft AND book_id=target_book AND source_id=target_source AND status IN ('approved','exported');
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT * INTO source_row FROM accounting.sources WHERE id=target_source AND book_id=target_book;
 IF NOT FOUND THEN RETURN false; END IF;
 SELECT COALESCE(jsonb_agg(jsonb_build_object('account_code',l.account_code,'debit',l.debit_cents,'credit',l.credit_cents,'aux',COALESCE(a.values_json::jsonb,'{}'::jsonb))
  ORDER BY l.account_code,l.debit_cents,l.credit_cents,COALESCE(a.values_json::jsonb,'{}'::jsonb)::text),'[]'::jsonb) INTO current_lines
  FROM accounting.draft_lines l LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.ordinal=l.ordinal AND a.book_id=l.book_id
  WHERE l.draft_id=target_draft AND l.book_id=target_book;
 IF current_lines IS DISTINCT FROM accounting.learning_canonical_lines(final_value->'lines') OR final_value->>'description' IS DISTINCT FROM draft_row.description
  OR snapshot->>'source_kind' IS DISTINCT FROM source_row.source_kind
  OR snapshot->'source' IS DISTINCT FROM jsonb_build_object('id',source_row.id,'date',source_row.date,'counterparty',source_row.counterparty,
     'direction',source_row.direction,'amount_cents',source_row.amount_cents,'fingerprint',source_row.fingerprint) THEN RETURN false; END IF;
 IF source_row.source_kind='business' THEN
  SELECT e.* INTO event_row FROM accounting.business_mappings m JOIN accounting.business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id
   WHERE m.draft_id=target_draft AND m.book_id=target_book AND m.source_id=target_source;
  IF NOT FOUND OR snapshot->'payload' IS DISTINCT FROM event_row.payload::jsonb OR snapshot->>'business_type' IS DISTINCT FROM event_row.template_id
   OR snapshot->'fact_version' IS DISTINCT FROM to_jsonb(event_row.fact_version)
   OR snapshot->'context' IS DISTINCT FROM jsonb_build_object('department_id',event_row.department_id,'applicant_id',event_row.applicant_id)
   OR snapshot->'recognized' IS DISTINCT FROM accounting.learning_recognized(event_row.id,event_row.fact_version,source_row.amount_cents)
  THEN RETURN false; END IF;
 ELSE
  expected_payload:=jsonb_build_object('occurred_on',source_row.date,'description',source_row.description,'counterparty',source_row.counterparty,
   'amount',(source_row.amount_cents/100)::text||'.'||lpad((source_row.amount_cents%100)::text,2,'0'),'direction',source_row.direction,'payment_status','paid','category','other','project','');
  IF snapshot->'payload' IS DISTINCT FROM expected_payload OR snapshot->>'business_type' IS DISTINCT FROM 'daily_cashflow'
   OR snapshot->'fact_version' IS DISTINCT FROM 'null'::jsonb OR snapshot->'context' IS DISTINCT FROM '{}'::jsonb OR snapshot->'recognized' IS DISTINCT FROM '{}'::jsonb THEN RETURN false; END IF;
 END IF;
 RETURN true; END $$;
CREATE FUNCTION accounting.learning_render_matches(target_book TEXT,definition JSONB,snapshot JSONB,final_value JSONB) RETURNS BOOLEAN LANGUAGE plpgsql STABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE line JSONB; target_code TEXT; field TEXT; side TEXT; amount BIGINT; gross BIGINT; net BIGINT; tax BIGINT;
  debit_net INTEGER:=0;debit_tax INTEGER:=0;credit_net INTEGER:=0;credit_tax INTEGER:=0;
  required TEXT; aux JSONB; aux_value TEXT; requirements JSONB; result JSONB:='[]'::jsonb; period_value TEXT;
 BEGIN
 IF NOT accounting.book_allowed(target_book) OR jsonb_typeof(definition->'lines') IS DISTINCT FROM 'array'
  OR jsonb_array_length(definition->'lines') NOT BETWEEN 2 AND 20
  OR definition->>'source_kind' IS NULL OR definition->>'source_kind' NOT IN ('bank','business')
  OR definition->'conditions'->>'direction' NOT IN ('expense','income')
  OR definition->'conditions'->>'payment_status' NOT IN ('paid','unpaid')
  OR definition->'conditions'->>'business_type' NOT IN ('reimbursement','procurement_expense','daily_cashflow')
  OR COALESCE(length(trim(definition->'conditions'->>'counterparty')),0) NOT BETWEEN 1 AND 200
 THEN RETURN false; END IF;
 IF definition->>'source_kind'='business' AND (definition->'conditions'->>'category' IS NULL OR definition->'conditions'->>'category' NOT IN ('office','travel','rent','communication','other'))
  OR definition->>'source_kind'='bank' AND (definition->'conditions'->>'category' IS NOT NULL OR definition->'conditions'->>'business_type'<>'daily_cashflow' OR definition->'conditions'->>'payment_status'<>'paid')
  OR definition->'conditions'->>'business_type'='reimbursement' AND (definition->'conditions'->>'direction'<>'expense' OR definition->'conditions'->>'payment_status'<>'unpaid')
  OR definition->'conditions'->>'business_type'='procurement_expense' AND definition->'conditions'->>'direction'<>'expense'
  OR definition->'conditions'->>'business_type'='daily_cashflow' AND definition->'conditions'->>'payment_status'<>'paid' THEN RETURN false; END IF;
 SELECT period INTO period_value FROM accounting.books WHERE id=target_book;
 gross:=accounting.learning_money_cents(snapshot->'payload'->>'amount');
 IF gross IS NULL OR gross<=0 OR substr(snapshot->'payload'->>'occurred_on',1,7) IS DISTINCT FROM period_value THEN RETURN false; END IF;
 net:=accounting.learning_money_cents(snapshot->'recognized'->>'net');tax:=accounting.learning_money_cents(snapshot->'recognized'->>'tax');
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(definition->'lines') l WHERE l->>'amount_field'<>'gross')
  AND (net IS NULL OR tax IS NULL OR net+tax<>gross) THEN RETURN false; END IF;
 FOR line IN SELECT value FROM jsonb_array_elements(definition->'lines') LOOP
  target_code:=line->>'account_code';field:=line->>'amount_field';side:=line->>'side';
  IF target_code IS NULL OR field IS NULL OR side IS NULL OR field NOT IN ('gross','net','tax') OR side NOT IN ('debit','credit')
   OR (definition->>'source_kind'='bank' AND field<>'gross') THEN RETURN false; END IF;
  IF side='debit' THEN
   IF field IN ('gross','net') THEN debit_net:=debit_net+1; END IF;
   IF field IN ('gross','tax') THEN debit_tax:=debit_tax+1; END IF;
  ELSE
   IF field IN ('gross','net') THEN credit_net:=credit_net+1; END IF;
   IF field IN ('gross','tax') THEN credit_tax:=credit_tax+1; END IF;
  END IF;
  SELECT auxiliary_requirements::jsonb INTO requirements FROM accounting.accounts WHERE book_id=target_book AND accounts.code=target_code AND active=1;
  IF NOT FOUND THEN RETURN false; END IF;
  amount:=CASE field WHEN 'gross' THEN gross WHEN 'net' THEN net ELSE tax END;
  IF amount=0 THEN CONTINUE; END IF;
  aux:='{}'::jsonb;
  FOR required IN SELECT jsonb_array_elements_text(requirements) LOOP
   aux_value:=CASE required WHEN 'department' THEN snapshot->'context'->>'department_id'
    WHEN 'employee' THEN snapshot->'context'->>'applicant_id' WHEN 'counterparty' THEN snapshot->'payload'->>'counterparty'
    WHEN 'project' THEN snapshot->'payload'->>'project' ELSE NULL END;
   IF aux_value IS NULL OR trim(aux_value)='' OR NOT accounting.catalog_aux_valid(target_book,required,aux_value) THEN RETURN false; END IF;
   aux:=aux||jsonb_build_object(required,aux_value);
  END LOOP;
  result:=result||jsonb_build_array(jsonb_build_object('account_code',target_code,'debit',CASE WHEN side='debit' THEN amount ELSE 0 END,'credit',CASE WHEN side='credit' THEN amount ELSE 0 END,'aux',aux));
 END LOOP;
 IF debit_net<>1 OR debit_tax<>1 OR credit_net<>1 OR credit_tax<>1 THEN RETURN false; END IF;
 SELECT COALESCE(jsonb_agg(value ORDER BY value->>'account_code',(value->>'debit')::bigint,(value->>'credit')::bigint,(value->'aux')::text),'[]'::jsonb) INTO result FROM jsonb_array_elements(result);
 RETURN COALESCE(result=accounting.learning_canonical_lines(final_value->'lines'),false);
 END $$;
CREATE FUNCTION accounting.learning_db_context(target_rule TEXT,target_book TEXT,target_version INTEGER) RETURNS TEXT LANGUAGE sql STABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT md5(jsonb_build_object('definition',v.definition_json::jsonb,'version',v.version,'positive',v.sample_ids_json::jsonb,
  'catalog',COALESCE((SELECT jsonb_agg(jsonb_build_array(a.code,a.name,a.active,a.version,a.auxiliary_requirements::jsonb) ORDER BY a.code) FROM accounting.accounts a WHERE a.book_id=target_book),'[]'::jsonb),
  'samples',COALESCE((SELECT jsonb_agg(jsonb_build_array(s.id,s.sample_fingerprint,s.snapshot_json::jsonb,s.final_json::jsonb,d.status IN ('approved','exported'),d.approved_by) ORDER BY s.id)
   FROM accounting.learning_samples s JOIN accounting.drafts d ON d.id=s.draft_id AND d.book_id=s.book_id WHERE s.book_id=target_book),'[]'::jsonb))::text)
 FROM accounting.learning_rule_versions v WHERE v.rule_id=target_rule AND v.book_id=target_book AND v.version=target_version AND accounting.book_allowed(target_book)
 $$;
CREATE FUNCTION accounting.learning_db_evaluation(target_rule TEXT,target_book TEXT,target_version INTEGER,negative_ids JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE definition JSONB; positive_ids JSONB; sample RECORD; positives INTEGER; negatives INTEGER; scanned INTEGER:=0; matched INTEGER:=0;
  passed BOOLEAN:=true;
 BEGIN
 SELECT definition_json::jsonb,sample_ids_json::jsonb INTO definition,positive_ids FROM accounting.learning_rule_versions
  WHERE rule_id=target_rule AND book_id=target_book AND version=target_version;
 IF NOT FOUND OR NOT accounting.book_allowed(target_book) OR jsonb_typeof(negative_ids) IS DISTINCT FROM 'array'
  OR jsonb_array_length(negative_ids) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'learning requires real positive and negative examples'; END IF;
 positives:=jsonb_array_length(positive_ids);negatives:=jsonb_array_length(negative_ids);
 IF positives NOT BETWEEN 1 AND 100 OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(positive_ids))<>positives
  OR (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(negative_ids))<>negatives
  OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(positive_ids) p WHERE negative_ids @> jsonb_build_array(p.value))
 THEN RAISE EXCEPTION 'learning positive and negative examples must be distinct'; END IF;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(positive_ids||negative_ids) refs(sample_id) WHERE NOT EXISTS(
  SELECT 1 FROM accounting.learning_samples s JOIN accounting.drafts d ON d.id=s.draft_id AND d.book_id=s.book_id
  WHERE s.id=refs.sample_id AND s.book_id=target_book AND d.status IN ('approved','exported') AND d.approved_by=s.reviewed_by AND d.version>=s.approved_version))
 THEN RAISE EXCEPTION 'learning evaluation requires current approved lineage'; END IF;
 FOR sample IN SELECT s.*,d.status AS draft_status,d.approved_by FROM accounting.learning_samples s JOIN accounting.drafts d ON d.id=s.draft_id AND d.book_id=s.book_id WHERE s.book_id=target_book ORDER BY s.id LOOP
  scanned:=scanned+1;
  IF scanned>2000 THEN RAISE EXCEPTION 'learning evaluation exceeds MVP sample bound'; END IF;
  -- Validate EVERY sample before matching. A manufactured counterexample
  -- must not escape authenticity checks simply by changing its counterparty.
  IF NOT accounting.learning_sample_truth(target_book,sample.draft_id,sample.source_id,sample.snapshot_json::jsonb,sample.final_json::jsonb)
   OR sample.draft_status NOT IN ('approved','exported') OR sample.approved_by IS DISTINCT FROM sample.reviewed_by THEN passed:=false; END IF;
  IF positive_ids @> jsonb_build_array(sample.id) AND NOT accounting.learning_matches(definition,sample.snapshot_json::jsonb) THEN passed:=false; END IF;
  IF NOT accounting.learning_matches(definition,sample.snapshot_json::jsonb) THEN CONTINUE; END IF;
  matched:=matched+1;
  IF negative_ids @> jsonb_build_array(sample.id) OR sample.draft_status NOT IN ('approved','exported') OR sample.approved_by IS DISTINCT FROM sample.reviewed_by
   OR NOT accounting.learning_render_matches(target_book,definition,sample.snapshot_json::jsonb,sample.final_json::jsonb) THEN passed:=false; END IF;
 END LOOP;
 RETURN jsonb_build_object('passed',passed,'positive_count',positives,'negative_count',negatives,'scanned_count',scanned,'matched_count',matched);
 END $$;

CREATE FUNCTION accounting.learning_sample_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.catalog_manage(NEW.book_id) OR NEW.firm_id<>accounting.actor_firm() OR NEW.reviewed_by<>accounting.actor_user()
  OR NOT EXISTS(SELECT 1 FROM accounting.drafts d JOIN accounting.books b ON b.id=d.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
   WHERE d.id=NEW.draft_id AND d.book_id=NEW.book_id AND d.source_id=NEW.source_id AND d.status='approved' AND d.version=NEW.approved_version
    AND d.approved_by=NEW.reviewed_by AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND b.period=NEW.period AND p.status='open')
 THEN RAISE EXCEPTION 'learning sample requires current reviewed source lineage' USING ERRCODE='42501'; END IF;
 IF NOT accounting.learning_sample_truth(NEW.book_id,NEW.draft_id,NEW.source_id,NEW.snapshot_json::jsonb,NEW.final_json::jsonb)
 THEN RAISE EXCEPTION 'learning sample does not match frozen journal and canonical source facts'; END IF;
 RETURN NEW; END $$;
CREATE FUNCTION accounting.learning_rule_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE evaluation RECORD; server_report JSONB; BEGIN
 IF NOT accounting.catalog_manage(NEW.book_id) OR NEW.firm_id<>accounting.actor_firm() OR NOT EXISTS(
  SELECT 1 FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
  WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND b.period=NEW.period AND p.status='open')
 THEN RAISE EXCEPTION 'learning rule requires reviewer and open own book' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'draft' OR NEW.current_version<>1 OR NEW.state_version<>1 OR NEW.evaluation_id IS NOT NULL OR NEW.created_by<>accounting.actor_user()
  THEN RAISE EXCEPTION 'new learning rules are unpublished drafts'; END IF;
 ELSE
  IF (NEW.id,NEW.book_id,NEW.firm_id,NEW.company_id,NEW.period,NEW.created_by,NEW.created_at) IS DISTINCT FROM
     (OLD.id,OLD.book_id,OLD.firm_id,OLD.company_id,OLD.period,OLD.created_by,OLD.created_at) OR NEW.state_version<>OLD.state_version+1
  THEN RAISE EXCEPTION 'learning rule identity and state version are protected'; END IF;
  IF NEW.current_version<>OLD.current_version THEN
   IF NEW.current_version<>OLD.current_version+1 OR NEW.status<>'draft' OR NEW.evaluation_id IS NOT NULL
   THEN RAISE EXCEPTION 'learning revisions require next immutable draft version'; END IF;
  ELSIF NEW.status='published' THEN
   SELECT * INTO evaluation FROM accounting.learning_rule_evaluations e WHERE e.id=NEW.evaluation_id AND e.rule_id=NEW.id AND e.book_id=NEW.book_id AND e.rule_version=NEW.current_version
    AND e.report_json::jsonb->>'passed'='true';
   IF OLD.status<>'draft' OR NOT FOUND
   THEN RAISE EXCEPTION 'learning publication requires passing current evaluation'; END IF;
   IF evaluation.db_input_fingerprint IS DISTINCT FROM accounting.learning_db_context(NEW.id,NEW.book_id,NEW.current_version)
    THEN RAISE EXCEPTION 'learning evaluation database context is stale'; END IF;
   server_report:=accounting.learning_db_evaluation(NEW.id,NEW.book_id,NEW.current_version,evaluation.negative_sample_ids_json::jsonb);
   IF server_report->>'passed' IS DISTINCT FROM 'true' THEN RAISE EXCEPTION 'learning publication no longer passes deterministic evaluation'; END IF;
  ELSIF NEW.status<>'retired' OR OLD.status='retired' THEN RAISE EXCEPTION 'invalid learning rule transition'; END IF;
 END IF;
 RETURN NEW; END $$;
CREATE FUNCTION accounting.learning_version_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.catalog_manage(NEW.book_id) OR NEW.created_by<>accounting.actor_user() OR NOT EXISTS(
  SELECT 1 FROM accounting.learning_rules r JOIN accounting.books b ON b.id=r.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
  WHERE r.id=NEW.rule_id AND r.book_id=NEW.book_id AND r.current_version=NEW.version AND r.status='draft' AND p.status='open')
  OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.sample_ids_json::jsonb) AS refs(sample_id) WHERE NOT EXISTS(
   SELECT 1 FROM accounting.learning_samples s JOIN accounting.drafts d ON d.id=s.draft_id AND d.book_id=s.book_id
   WHERE s.id=refs.sample_id AND s.book_id=NEW.book_id AND s.firm_id=accounting.actor_firm() AND d.status IN ('approved','exported') AND d.approved_by=s.reviewed_by))
 THEN RAISE EXCEPTION 'learning version requires approved examples in the same authorized book' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;
CREATE FUNCTION accounting.learning_evaluation_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE computed JSONB; claimed JSONB; BEGIN
 IF NOT accounting.catalog_manage(NEW.book_id) OR NEW.created_by<>accounting.actor_user() OR NOT EXISTS(
  SELECT 1 FROM accounting.learning_rules r JOIN accounting.books b ON b.id=r.book_id JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period
  WHERE r.id=NEW.rule_id AND r.book_id=NEW.book_id AND r.current_version=NEW.rule_version AND r.status='draft' AND p.status='open')
  OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(NEW.negative_sample_ids_json::jsonb) AS refs(sample_id) WHERE NOT EXISTS(
   SELECT 1 FROM accounting.learning_samples s JOIN accounting.drafts d ON d.id=s.draft_id AND d.book_id=s.book_id
   WHERE s.id=refs.sample_id AND s.book_id=NEW.book_id AND s.firm_id=accounting.actor_firm() AND d.status IN ('approved','exported') AND d.approved_by=s.reviewed_by))
 THEN RAISE EXCEPTION 'learning evaluation requires approved counterexamples in the same authorized book' USING ERRCODE='42501'; END IF;
 computed:=accounting.learning_db_evaluation(NEW.rule_id,NEW.book_id,NEW.rule_version,NEW.negative_sample_ids_json::jsonb);
 claimed:=jsonb_build_object('passed',NEW.report_json::jsonb->'passed','positive_count',NEW.report_json::jsonb->'positive_count',
  'negative_count',NEW.report_json::jsonb->'negative_count','scanned_count',NEW.report_json::jsonb->'scanned_count','matched_count',NEW.report_json::jsonb->'matched_count');
 IF computed IS DISTINCT FROM claimed THEN RAISE EXCEPTION 'learning evaluation report does not match deterministic database result'; END IF;
 NEW.db_input_fingerprint:=accounting.learning_db_context(NEW.rule_id,NEW.book_id,NEW.rule_version);
 RETURN NEW; END $$;
CREATE FUNCTION accounting.learning_history_immutable() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN RAISE EXCEPTION 'learning history is immutable'; END $$;
REVOKE ALL ON FUNCTION accounting.learning_sample_guard(),accounting.learning_rule_guard(),accounting.learning_version_guard(),accounting.learning_evaluation_guard(),accounting.learning_history_immutable() FROM PUBLIC;
REVOKE ALL ON FUNCTION accounting.learning_money_cents(TEXT),accounting.learning_canonical_lines(JSONB),accounting.learning_matches(JSONB,JSONB),
 accounting.learning_recognized(TEXT,INTEGER,BIGINT),accounting.learning_sample_truth(TEXT,TEXT,TEXT,JSONB,JSONB),
 accounting.learning_render_matches(TEXT,JSONB,JSONB,JSONB),accounting.learning_db_context(TEXT,TEXT,INTEGER),accounting.learning_db_evaluation(TEXT,TEXT,INTEGER,JSONB) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.learning_money_cents(TEXT),accounting.learning_canonical_lines(JSONB),accounting.learning_matches(JSONB,JSONB),
 accounting.learning_recognized(TEXT,INTEGER,BIGINT),accounting.learning_sample_truth(TEXT,TEXT,TEXT,JSONB,JSONB),
 accounting.learning_render_matches(TEXT,JSONB,JSONB,JSONB),accounting.learning_db_context(TEXT,TEXT,INTEGER),accounting.learning_db_evaluation(TEXT,TEXT,INTEGER,JSONB) TO accounting_app;
CREATE TRIGGER learning_samples_capture BEFORE INSERT ON accounting.learning_samples FOR EACH ROW EXECUTE FUNCTION accounting.learning_sample_guard();
CREATE TRIGGER learning_samples_immutable BEFORE UPDATE OR DELETE ON accounting.learning_samples FOR EACH ROW EXECUTE FUNCTION accounting.learning_history_immutable();
CREATE TRIGGER learning_rule_mutation BEFORE INSERT OR UPDATE ON accounting.learning_rules FOR EACH ROW EXECUTE FUNCTION accounting.learning_rule_guard();
CREATE TRIGGER learning_version_insert BEFORE INSERT ON accounting.learning_rule_versions FOR EACH ROW EXECUTE FUNCTION accounting.learning_version_guard();
CREATE TRIGGER learning_versions_immutable BEFORE UPDATE OR DELETE ON accounting.learning_rule_versions FOR EACH ROW EXECUTE FUNCTION accounting.learning_history_immutable();
CREATE TRIGGER learning_evaluation_insert BEFORE INSERT ON accounting.learning_rule_evaluations FOR EACH ROW EXECUTE FUNCTION accounting.learning_evaluation_guard();
CREATE TRIGGER learning_evaluations_immutable BEFORE UPDATE OR DELETE ON accounting.learning_rule_evaluations FOR EACH ROW EXECUTE FUNCTION accounting.learning_history_immutable();
ALTER TABLE accounting.learning_samples ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.learning_samples FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.learning_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.learning_rules FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.learning_rule_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.learning_rule_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.learning_rule_evaluations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.learning_rule_evaluations FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.learning_samples,accounting.learning_rules,accounting.learning_rule_versions,accounting.learning_rule_evaluations FROM PUBLIC,accounting_worker;
GRANT SELECT,INSERT ON accounting.learning_samples,accounting.learning_rule_versions,accounting.learning_rule_evaluations TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.learning_rules TO accounting_app;
CREATE POLICY learning_samples_read ON accounting.learning_samples FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY learning_samples_capture ON accounting.learning_samples FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND reviewed_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
CREATE POLICY learning_rules_read ON accounting.learning_rules FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
CREATE POLICY learning_rules_create ON accounting.learning_rules FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
CREATE POLICY learning_rules_update ON accounting.learning_rules FOR UPDATE TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.catalog_manage(book_id)) WITH CHECK(firm_id=accounting.actor_firm() AND accounting.catalog_manage(book_id));
CREATE POLICY learning_versions_read ON accounting.learning_rule_versions FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY learning_versions_create ON accounting.learning_rule_versions FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
CREATE POLICY learning_evaluations_read ON accounting.learning_rule_evaluations FOR SELECT TO accounting_app USING(accounting.book_allowed(book_id));
CREATE POLICY learning_evaluations_create ON accounting.learning_rule_evaluations FOR INSERT TO accounting_app WITH CHECK(created_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
