-- Evaluation truth is reviewed evidence + an independent human confirmation.
-- Reports are recomputed natively; a caller cannot persist fabricated passed rows.
CREATE TABLE accounting.quality_standards (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,period TEXT NOT NULL,
 sample_id TEXT NOT NULL,draft_id TEXT NOT NULL,source_id TEXT NOT NULL,
 snapshot_json TEXT NOT NULL CHECK(jsonb_typeof(snapshot_json::jsonb)='object'),final_json TEXT NOT NULL CHECK(jsonb_typeof(final_json::jsonb)='object'),
 evidence_json TEXT NOT NULL CHECK(jsonb_typeof(evidence_json::jsonb)='object'),labels_json TEXT NOT NULL CHECK(jsonb_typeof(labels_json::jsonb)='array' AND jsonb_array_length(labels_json::jsonb)<=100),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 UNIQUE(id,book_id),UNIQUE(firm_id,created_by,request_key),FOREIGN KEY(sample_id,book_id) REFERENCES accounting.learning_samples(id,book_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id),FOREIGN KEY(draft_id,book_id) REFERENCES accounting.drafts(id,book_id),FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id)
);
CREATE INDEX idx_quality_standards_page ON accounting.quality_standards(book_id,created_at DESC,id DESC);
CREATE TABLE accounting.quality_confirmations (
 standard_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,confirmed_by TEXT NOT NULL REFERENCES accounting.users(id),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),evidence_checked INTEGER NOT NULL CHECK(evidence_checked=1),created_at TEXT NOT NULL,
 FOREIGN KEY(standard_id,book_id) REFERENCES accounting.quality_standards(id,book_id)
);
CREATE TABLE accounting.quality_runs (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,period TEXT NOT NULL,
 standard_ids_json TEXT NOT NULL CHECK(jsonb_typeof(standard_ids_json::jsonb)='array' AND jsonb_array_length(standard_ids_json::jsonb) BETWEEN 1 AND 100),
 settings_json TEXT NOT NULL CHECK(jsonb_typeof(settings_json::jsonb)='object'),input_fingerprint TEXT NOT NULL,report_json TEXT NOT NULL CHECK(jsonb_typeof(report_json::jsonb)='object'),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,request_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),
 UNIQUE(firm_id,created_by,request_key),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE INDEX idx_quality_runs_page ON accounting.quality_runs(book_id,created_at DESC,id DESC);

CREATE FUNCTION accounting.quality_evidence(target_book TEXT,target_sample TEXT) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_sample accounting.learning_samples;v_event TEXT;v_ref TEXT; BEGIN
 IF NOT accounting.catalog_manage(target_book) THEN RAISE EXCEPTION 'Quality reviewer scope denied' USING ERRCODE='42501';END IF;
 SELECT * INTO v_sample FROM accounting.learning_samples WHERE id=target_sample AND book_id=target_book AND firm_id=accounting.actor_firm();
 IF NOT FOUND THEN RAISE EXCEPTION 'Reviewed sample absent' USING ERRCODE='23514';END IF;
 SELECT event_id INTO v_event FROM accounting.business_mappings WHERE book_id=target_book AND draft_id=v_sample.draft_id AND source_id=v_sample.source_id;
 SELECT COALESCE((SELECT evidence_ref FROM accounting.source_evidence e WHERE e.source_id=s.id AND e.book_id=s.book_id ORDER BY e.id DESC LIMIT 1),s.evidence_ref) INTO v_ref FROM accounting.sources s WHERE s.id=v_sample.source_id AND s.book_id=target_book;
 RETURN jsonb_build_object('event_id',v_event,'evidence_ref',v_ref,
  'attachments',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',a.id,'sha256',a.sha256,'size_bytes',a.size_bytes,'filename',a.filename,'media_type',a.media_type) ORDER BY a.id) FROM accounting.business_attachments a WHERE a.event_id=v_event),'[]'::jsonb),
  'ocr',COALESCE((SELECT jsonb_agg(jsonb_build_object('id',r.id,'attachment_id',r.attachment_id,'input_version',r.input_version,'input_fingerprint',r.input_fingerprint,'provider',r.provider,'model',r.model,'prompt_version',r.prompt_version,'result',r.result_json::jsonb) ORDER BY r.id)
   FROM accounting.ai_runs r WHERE r.event_id=v_event AND r.kind='ocr' AND r.status='succeeded' AND r.input_version=(v_sample.snapshot_json::jsonb->>'fact_version')::integer
    AND NOT EXISTS(SELECT 1 FROM accounting.case_supersessions x WHERE x.event_id=r.event_id AND x.old_attachment_id=r.attachment_id)),'[]'::jsonb));
 END $$;

CREATE FUNCTION accounting.quality_standard_current(target_standard TEXT) RETURNS BOOLEAN LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE v_row accounting.quality_standards; BEGIN
 SELECT * INTO v_row FROM accounting.quality_standards WHERE id=target_standard AND firm_id=accounting.actor_firm();
 IF NOT FOUND OR NOT accounting.catalog_manage(v_row.book_id) THEN RETURN false;END IF;
 RETURN NOT accounting.source_inactive(v_row.book_id,v_row.source_id) AND NOT accounting.revision_source_blocked(v_row.book_id,v_row.source_id)
 AND accounting.learning_sample_truth(v_row.book_id,v_row.draft_id,v_row.source_id,v_row.snapshot_json::jsonb,v_row.final_json::jsonb)
 AND v_row.evidence_json::jsonb=accounting.quality_evidence(v_row.book_id,v_row.sample_id);
 END $$;

CREATE FUNCTION accounting.quality_standard_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE v_sample accounting.learning_samples;v_label JSONB;v_ocr JSONB;v_doc JSONB; BEGIN
 IF NOT accounting.catalog_manage(NEW.book_id) OR NEW.firm_id<>accounting.actor_firm() OR NEW.created_by<>accounting.actor_user() THEN RAISE EXCEPTION 'Quality standard scope denied' USING ERRCODE='42501';END IF;
 SELECT * INTO v_sample FROM accounting.learning_samples WHERE id=NEW.sample_id AND book_id=NEW.book_id AND firm_id=NEW.firm_id;
 IF NOT FOUND OR v_sample.company_id<>NEW.company_id OR v_sample.period<>NEW.period OR v_sample.draft_id<>NEW.draft_id OR v_sample.source_id<>NEW.source_id
 OR NEW.snapshot_json::jsonb IS DISTINCT FROM v_sample.snapshot_json::jsonb OR NEW.final_json::jsonb IS DISTINCT FROM v_sample.final_json::jsonb
 OR NOT accounting.learning_sample_truth(NEW.book_id,NEW.draft_id,NEW.source_id,NEW.snapshot_json::jsonb,NEW.final_json::jsonb)
 OR accounting.source_inactive(NEW.book_id,NEW.source_id) OR accounting.revision_source_blocked(NEW.book_id,NEW.source_id)
 OR NEW.evidence_json::jsonb IS DISTINCT FROM accounting.quality_evidence(NEW.book_id,NEW.sample_id) THEN
  RAISE EXCEPTION 'Quality standard must freeze current reviewed truth' USING ERRCODE='23514';END IF;
 IF COALESCE(length(trim(NEW.evidence_json::jsonb->>'evidence_ref')),0)=0 AND jsonb_array_length(NEW.evidence_json::jsonb->'attachments')=0 THEN RAISE EXCEPTION 'Original evidence required' USING ERRCODE='23514';END IF;
 FOR v_label IN SELECT value FROM jsonb_array_elements(NEW.labels_json::jsonb) LOOP
  IF jsonb_typeof(v_label) IS DISTINCT FROM 'object' OR NOT v_label ?& ARRAY['extraction_id','document_index','field','page','value']
   OR jsonb_typeof(v_label->'field') IS DISTINCT FROM 'string' OR v_label->>'field' NOT IN ('invoice_number','issued_on','seller_name','seller_tax_id','buyer_name','buyer_tax_id','gross_amount','net_amount','tax_amount','currency')
   OR jsonb_typeof(v_label->'document_index') IS DISTINCT FROM 'number' OR v_label->>'document_index' !~ '^(0|[1-9][0-9]?)$' OR (v_label->>'document_index')::integer>19 OR jsonb_typeof(v_label->'page') IS DISTINCT FROM 'number' OR v_label->>'page' !~ '^[1-6]$'
   OR jsonb_typeof(v_label->'value') NOT IN ('string','null') OR length(v_label->>'value')>500 THEN RAISE EXCEPTION 'Invalid human field label' USING ERRCODE='23514';END IF;
  SELECT value INTO v_ocr FROM jsonb_array_elements(NEW.evidence_json::jsonb->'ocr') WHERE value->>'id'=v_label->>'extraction_id';
  v_doc:=v_ocr->'result'->'documents'->(v_label->>'document_index')::integer;
  IF v_doc IS NULL OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_doc->'pages') p WHERE p->>'page'=v_label->>'page') THEN RAISE EXCEPTION 'Label must reference actual frozen original page' USING ERRCODE='23514';END IF;
  IF v_label->>'field' IN ('gross_amount','net_amount','tax_amount') AND v_label->'value'<>'null'::jsonb AND accounting.learning_money_cents(v_label->>'value') IS NULL THEN RAISE EXCEPTION 'Exact money label required' USING ERRCODE='23514';END IF;
 END LOOP;
 IF EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.labels_json::jsonb) l GROUP BY l->>'extraction_id',l->>'document_index',l->>'field' HAVING count(*)>1) THEN RAISE EXCEPTION 'Duplicate labels denied' USING ERRCODE='23514';END IF;
 RETURN NEW;END $$;

CREATE FUNCTION accounting.quality_confirmation_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE v_row accounting.quality_standards; BEGIN
 SELECT * INTO v_row FROM accounting.quality_standards WHERE id=NEW.standard_id AND book_id=NEW.book_id;
 IF NOT FOUND OR NOT accounting.catalog_manage(NEW.book_id) OR NEW.firm_id<>accounting.actor_firm() OR NEW.firm_id<>v_row.firm_id OR NEW.confirmed_by<>accounting.actor_user() THEN RAISE EXCEPTION 'Quality confirmation scope denied' USING ERRCODE='42501';END IF;
 IF v_row.created_by=NEW.confirmed_by OR NOT accounting.quality_standard_current(NEW.standard_id) THEN RAISE EXCEPTION 'Another reviewer must check current original evidence' USING ERRCODE='23514';END IF;
 RETURN NEW;END $$;

CREATE FUNCTION accounting.quality_context(target_book TEXT,selected JSONB,settings JSONB) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.catalog_manage(target_book) THEN RAISE EXCEPTION 'Quality replay scope denied' USING ERRCODE='42501';END IF;
 IF jsonb_typeof(selected) IS DISTINCT FROM 'array' OR jsonb_array_length(selected) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'Bounded standards required' USING ERRCODE='23514';END IF;
 RETURN md5(jsonb_build_object('settings',settings,'master',accounting.learning_master_context(target_book),
  'standards',COALESCE((SELECT jsonb_agg(jsonb_build_array(s.id,s.snapshot_json::jsonb,s.final_json::jsonb,s.labels_json::jsonb,s.evidence_json::jsonb,accounting.quality_standard_current(s.id),c.confirmed_by) ORDER BY s.id) FROM accounting.quality_standards s LEFT JOIN accounting.quality_confirmations c ON c.standard_id=s.id WHERE s.book_id=target_book AND selected ? s.id),'[]'::jsonb),
  'accounts',COALESCE((SELECT jsonb_agg(jsonb_build_array(a.code,a.active,a.version,a.auxiliary_requirements::jsonb) ORDER BY a.code) FROM accounting.accounts a WHERE a.book_id=target_book),'[]'::jsonb),
  'templates',COALESCE((SELECT jsonb_agg(jsonb_build_array(t.id,t.current_version,t.active,v.definition::jsonb) ORDER BY t.id) FROM accounting.journal_templates t JOIN accounting.journal_template_versions v ON v.template_id=t.id AND v.version=t.current_version WHERE t.book_id=target_book),'[]'::jsonb),
  'rules',COALESCE((SELECT jsonb_agg(jsonb_build_array(r.id,r.current_version,r.state_version,r.status,v.definition_json::jsonb,accounting.learning_counterparty_identity(target_book,v.definition_json::jsonb->'conditions'->>'counterparty')) ORDER BY r.id) FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.book_id=target_book),'[]'::jsonb),
  'models',COALESCE((SELECT jsonb_agg(jsonb_build_array(r.id,r.status,r.input_fingerprint,r.result_json::jsonb) ORDER BY r.id) FROM accounting.ai_runs r WHERE r.model=settings->>'model' AND r.prompt_version=settings->>'prompt_version' AND EXISTS(SELECT 1 FROM accounting.quality_standards s WHERE s.book_id=target_book AND selected ? s.id AND s.evidence_json::jsonb->>'event_id'=r.event_id)),'[]'::jsonb))::text);
 END $$;

CREATE FUNCTION accounting.quality_render(target_book TEXT,definition JSONB,snapshot JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_line JSONB;v_amount BIGINT;v_aux JSONB;v_required TEXT;v_value TEXT;v_lines JSONB:='[]'::jsonb; BEGIN
 FOR v_line IN SELECT value FROM jsonb_array_elements(definition->'lines') LOOP
  v_amount:=accounting.learning_money_cents(CASE v_line->>'amount_field' WHEN 'gross' THEN snapshot->'payload'->>'amount' ELSE snapshot->'recognized'->>(v_line->>'amount_field') END);
  IF v_amount IS NULL THEN RETURN NULL;END IF;
  IF v_amount=0 THEN CONTINUE;END IF;
  v_aux:='{}'::jsonb;
  FOR v_required IN SELECT jsonb_array_elements_text(auxiliary_requirements::jsonb) FROM accounting.accounts WHERE book_id=target_book AND code=v_line->>'account_code' AND active=1 LOOP
   v_value:=CASE v_required WHEN 'department' THEN snapshot->'context'->>'department_id' WHEN 'employee' THEN snapshot->'context'->>'applicant_id' ELSE snapshot->'payload'->>v_required END;
   IF v_value IS NOT NULL THEN v_aux:=v_aux||jsonb_build_object(v_required,v_value);END IF;
  END LOOP;
  v_lines:=v_lines||jsonb_build_array(jsonb_build_object('account_code',v_line->>'account_code','debit',accounting.delivery_cents(CASE WHEN v_line->>'side'='debit' THEN v_amount ELSE 0 END),'credit',accounting.delivery_cents(CASE WHEN v_line->>'side'='credit' THEN v_amount ELSE 0 END),'auxiliary_values',v_aux));
 END LOOP;
 IF NOT accounting.learning_render_matches(target_book,definition,snapshot,jsonb_build_object('lines',v_lines)) THEN RETURN NULL;END IF;
 RETURN jsonb_build_object('lines',v_lines);
 END $$;

CREATE FUNCTION accounting.quality_candidate(target_book TEXT,v_standard accounting.quality_standards,settings JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_mode TEXT:=settings->>'mode';v_snapshot JSONB:=v_standard.snapshot_json::jsonb;v_row RECORD;v_count INTEGER;v_definition JSONB;v_candidate JSONB;v_provenance JSONB;v_label JSONB;v_original JSONB;v_document JSONB;v_labels JSONB:='[]'::jsonb;v_ids JSONB:='[]'::jsonb;BEGIN
 IF v_mode IN ('stored_model','stored_ocr') THEN
  IF v_mode='stored_ocr' THEN
   FOR v_label IN SELECT value FROM jsonb_array_elements(v_standard.labels_json::jsonb) LOOP
    SELECT value INTO v_original FROM jsonb_array_elements(v_standard.evidence_json::jsonb->'ocr') WHERE value->>'id'=v_label->>'extraction_id';
    SELECT * INTO v_row FROM accounting.ai_runs WHERE event_id=v_standard.evidence_json::jsonb->>'event_id' AND attachment_id=v_original->>'attachment_id' AND input_version=(v_snapshot->>'fact_version')::integer AND kind='ocr' AND status='succeeded' AND model=settings->>'model' AND prompt_version=settings->>'prompt_version' ORDER BY created_at DESC,id DESC LIMIT 1;
    IF NOT FOUND THEN RETURN jsonb_build_object('candidate',NULL,'provenance',jsonb_build_object('reason','指定版本缺少该原件的成功 OCR 结果'));END IF;
    v_document:=v_row.result_json::jsonb->'documents'->(v_label->>'document_index')::integer;
    v_labels:=v_labels||jsonb_build_array(v_label||jsonb_build_object('value',v_document->(v_label->>'field'),'page_present',v_label->'value'='null'::jsonb OR EXISTS(SELECT 1 FROM jsonb_array_elements(COALESCE(v_document->'evidence','[]'::jsonb)) e WHERE e->>'field'=v_label->>'field' AND e->>'page'=v_label->>'page')));
    v_ids:=v_ids||jsonb_build_array(v_row.id);
   END LOOP;
   RETURN jsonb_build_object('candidate',CASE WHEN jsonb_array_length(v_labels)>0 THEN jsonb_build_object('labels',v_labels) END,'provenance',jsonb_build_object('model',settings->>'model','prompt_version',settings->>'prompt_version','run_ids',v_ids,'reason',CASE WHEN jsonb_array_length(v_labels)=0 THEN '尚无真人确认的字段标注' END));
  END IF;
  SELECT r.* INTO v_row FROM accounting.ai_runs r WHERE event_id=v_standard.evidence_json::jsonb->>'event_id' AND draft_id=v_standard.draft_id AND book_id=target_book AND input_version<=(SELECT reviewed_version FROM accounting.learning_samples WHERE id=v_standard.sample_id AND book_id=target_book) AND kind='proposal' AND status='succeeded' AND model=settings->>'model' AND prompt_version=settings->>'prompt_version' AND EXISTS(SELECT 1 FROM accounting.audit_events a WHERE a.firm_id=r.firm_id AND a.object_id=r.id AND a.action='intelligence.proposal.claim' AND a.details::jsonb->'fact_version'=v_snapshot->'fact_version' AND a.details::jsonb->'input_version'=to_jsonb(r.input_version)) ORDER BY created_at DESC,id DESC LIMIT 1;
  RETURN jsonb_build_object('candidate',CASE WHEN FOUND THEN v_row.result_json::jsonb END,'provenance',jsonb_build_object('model',settings->>'model','prompt_version',settings->>'prompt_version','run_id',CASE WHEN FOUND THEN v_row.id END,'input_version',CASE WHEN FOUND THEN v_row.input_version END,'input_fingerprint',CASE WHEN FOUND THEN v_row.input_fingerprint END,'replay_basis','审核前归档候选；不宣称候选在当前补件或规则上下文重新生成','reason',CASE WHEN NOT FOUND THEN '指定版本没有此事实版本的成功分录候选' END));
 END IF;
 IF NOT accounting.learning_sample_ordinary(target_book,v_standard.sample_id) THEN RETURN jsonb_build_object('candidate',NULL,'provenance',jsonb_build_object('reason','清偿分录须按实际核销关系评测，不属于普通分类模板'));END IF;
 IF v_mode='rules' THEN
  SELECT count(*) INTO v_count FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.book_id=target_book AND r.status='published' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(v.sample_ids_json::jsonb) selected_sample WHERE NOT accounting.learning_sample_ordinary(target_book,selected_sample)) AND accounting.learning_matches_scoped(target_book,v.definition_json::jsonb,v_snapshot);
  IF v_count=1 THEN SELECT r.id,r.current_version,v.definition_json::jsonb AS definition INTO v_row FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.version=r.current_version WHERE r.book_id=target_book AND r.status='published' AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(v.sample_ids_json::jsonb) selected_sample WHERE NOT accounting.learning_sample_ordinary(target_book,selected_sample)) AND accounting.learning_matches_scoped(target_book,v.definition_json::jsonb,v_snapshot);v_definition:=v_row.definition;END IF;
 ELSIF v_snapshot->>'source_kind'='business' THEN
  WITH matching AS (SELECT t.id,t.current_version,v.definition::jsonb AS definition,(CASE WHEN v.definition::jsonb->>'category' IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN v.definition::jsonb->>'payment_status' IS NOT NULL THEN 1 ELSE 0 END) AS score FROM accounting.journal_templates t JOIN accounting.journal_template_versions v ON v.template_id=t.id AND v.version=t.current_version WHERE t.book_id=target_book AND t.active=1 AND v.definition::jsonb->>'business_type'=v_snapshot->>'business_type' AND v.definition::jsonb->>'direction'=v_snapshot->'payload'->>'direction' AND (v.definition::jsonb->>'category' IS NULL OR v.definition::jsonb->>'category'=v_snapshot->'payload'->>'category') AND (v.definition::jsonb->>'payment_status' IS NULL OR v.definition::jsonb->>'payment_status'=v_snapshot->'payload'->>'payment_status')) SELECT count(*) INTO v_count FROM matching WHERE score=(SELECT max(score) FROM matching);
  IF v_count=1 THEN SELECT t.id,t.current_version,v.definition::jsonb AS definition INTO v_row FROM accounting.journal_templates t JOIN accounting.journal_template_versions v ON v.template_id=t.id AND v.version=t.current_version WHERE t.book_id=target_book AND t.active=1 AND v.definition::jsonb->>'business_type'=v_snapshot->>'business_type' AND v.definition::jsonb->>'direction'=v_snapshot->'payload'->>'direction' AND (v.definition::jsonb->>'category' IS NULL OR v.definition::jsonb->>'category'=v_snapshot->'payload'->>'category') AND (v.definition::jsonb->>'payment_status' IS NULL OR v.definition::jsonb->>'payment_status'=v_snapshot->'payload'->>'payment_status') ORDER BY ((CASE WHEN v.definition::jsonb->>'category' IS NOT NULL THEN 1 ELSE 0 END)+(CASE WHEN v.definition::jsonb->>'payment_status' IS NOT NULL THEN 1 ELSE 0 END)) DESC,t.id LIMIT 1;
   v_definition:=jsonb_build_object('source_kind','business','conditions',jsonb_build_object('business_type',v_snapshot->>'business_type','direction',v_snapshot->'payload'->>'direction','category',v_snapshot->'payload'->>'category','payment_status',v_snapshot->'payload'->>'payment_status','counterparty',v_snapshot->'payload'->>'counterparty'),'lines',v_row.definition->'lines');END IF;
 END IF;
 IF v_definition IS NULL THEN RETURN jsonb_build_object('candidate',NULL,'provenance',jsonb_build_object('reason','没有覆盖此输入的配置，或存在同优先级配置冲突'));END IF;
 v_candidate:=accounting.quality_render(target_book,v_definition,v_snapshot);v_provenance:=jsonb_build_object('mode',v_mode,'configuration_id',v_row.id,'version',v_row.current_version);
 IF v_candidate IS NULL THEN v_provenance:=v_provenance||jsonb_build_object('reason','当前科目、金额或辅助核算校验不通过');END IF;
 RETURN jsonb_build_object('candidate',v_candidate,'provenance',v_provenance);END $$;

CREATE FUNCTION accounting.quality_differences(v_standard accounting.quality_standards,settings JSONB,candidate JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_diffs JSONB:='[]'::jsonb;v_expected JSONB;v_actual JSONB;v_label JSONB;v_candidate_label JSONB;v_index INTEGER:=0;v_left TEXT;v_right TEXT;v_amount BIGINT;BEGIN
 IF settings->>'mode'='stored_ocr' THEN
  FOR v_label IN SELECT value FROM jsonb_array_elements(v_standard.labels_json::jsonb) LOOP
   v_candidate_label:=candidate->'labels'->v_index;v_index:=v_index+1;v_left:=v_label->>'value';v_right:=v_candidate_label->>'value';
   IF v_label->>'field' IN ('gross_amount','net_amount','tax_amount') AND accounting.learning_money_cents(v_right) IS NOT NULL THEN v_right:=accounting.delivery_cents(accounting.learning_money_cents(v_right));END IF;
   IF v_left IS DISTINCT FROM v_right THEN v_diffs:=v_diffs||jsonb_build_array(jsonb_build_object('field',v_label->>'field','reference',v_label-'value'-'field','expected',v_left,'actual',v_right));END IF;
   IF v_candidate_label->>'page_present' IS DISTINCT FROM 'true' THEN v_diffs:=v_diffs||jsonb_build_array(jsonb_build_object('field','reference','expected',v_label->'page','actual',NULL));END IF;
  END LOOP;RETURN v_diffs;
 END IF;
 v_expected:=accounting.learning_canonical_lines(v_standard.final_json::jsonb->'lines');v_actual:=accounting.learning_canonical_lines(candidate->'lines');
 IF v_actual IS NULL THEN RETURN jsonb_build_array(jsonb_build_object('field','lines','expected',v_standard.final_json::jsonb->'lines','actual',candidate->'lines'));END IF;
 IF (SELECT jsonb_agg(value-'aux' ORDER BY value->>'account_code',value->>'debit',value->>'credit') FROM jsonb_array_elements(v_expected)) IS DISTINCT FROM (SELECT jsonb_agg(value-'aux' ORDER BY value->>'account_code',value->>'debit',value->>'credit') FROM jsonb_array_elements(v_actual)) THEN
  v_diffs:=v_diffs||jsonb_build_array(jsonb_build_object('field','lines','expected',v_standard.final_json::jsonb->'lines','actual',candidate->'lines'));
 ELSIF v_expected IS DISTINCT FROM v_actual THEN v_diffs:=v_diffs||jsonb_build_array(jsonb_build_object('field','auxiliary','expected',v_standard.final_json::jsonb->'lines','actual',candidate->'lines'));END IF;
 SELECT COALESCE(sum((value->>'debit')::bigint),0) INTO v_amount FROM jsonb_array_elements(v_actual);
 IF v_amount<>(v_standard.snapshot_json::jsonb->'source'->>'amount_cents')::bigint THEN v_diffs:=v_diffs||jsonb_build_array(jsonb_build_object('field','amount','expected',v_standard.snapshot_json::jsonb->'payload'->>'amount','actual',accounting.delivery_cents(v_amount)));END IF;
 IF settings->>'mode'='stored_model' AND (jsonb_typeof(candidate->'evidence_refs') IS DISTINCT FROM 'array' OR jsonb_array_length(candidate->'evidence_refs')=0 OR EXISTS(
  SELECT 1 FROM jsonb_array_elements(candidate->'evidence_refs') ref WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(v_standard.evidence_json::jsonb->'ocr') o CROSS JOIN LATERAL jsonb_array_elements(o->'result'->'documents') d CROSS JOIN LATERAL jsonb_array_elements(d->'evidence') e WHERE o->>'attachment_id'=ref->>'attachment_id' AND o->>'id'=ref->>'extraction_id' AND e->>'field'=ref->>'field' AND e->>'page'=ref->>'page')))
 THEN v_diffs:=v_diffs||jsonb_build_array(jsonb_build_object('field','reference','expected','本标准冻结原件中的有效字段与页码引用','actual',candidate->'evidence_refs'));END IF;
 RETURN v_diffs;END $$;

CREATE FUNCTION accounting.quality_report(target_book TEXT,selected JSONB,settings JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE v_id TEXT;v_standard accounting.quality_standards;v_item JSONB;v_candidate JSONB;v_diffs JSONB;v_status TEXT;v_rows JSONB:='[]'::jsonb;v_counts JSONB;BEGIN
 IF NOT accounting.catalog_manage(target_book) THEN RAISE EXCEPTION 'Quality reviewer scope denied' USING ERRCODE='42501';END IF;
 IF jsonb_typeof(selected) IS DISTINCT FROM 'array' OR jsonb_array_length(selected) NOT BETWEEN 1 AND 100 OR EXISTS(SELECT 1 FROM jsonb_array_elements_text(selected) x GROUP BY x HAVING count(*)>1) OR jsonb_typeof(settings->'mode') IS DISTINCT FROM 'string' OR settings->>'mode' NOT IN ('rules','templates','stored_model','stored_ocr')
 OR (settings->>'mode' IN ('stored_model','stored_ocr') AND (COALESCE(length(settings->>'model'),0) NOT BETWEEN 1 AND 200 OR COALESCE(length(settings->>'prompt_version'),0) NOT BETWEEN 1 AND 100)) THEN RAISE EXCEPTION 'Invalid bounded replay request' USING ERRCODE='23514';END IF;
 FOR v_id IN SELECT jsonb_array_elements_text(selected) LOOP
  SELECT * INTO v_standard FROM accounting.quality_standards WHERE id=v_id AND book_id=target_book AND firm_id=accounting.actor_firm();
  IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM accounting.quality_confirmations WHERE standard_id=v_id AND book_id=target_book) THEN RAISE EXCEPTION 'Replay needs independently confirmed standards in this book' USING ERRCODE='23514';END IF;
  IF NOT accounting.quality_standard_current(v_id) THEN v_item:=jsonb_build_object('candidate',NULL,'provenance',jsonb_build_object('reason','事实或原件证据已失效'));v_status:='stale';v_diffs:='[]'::jsonb;
  ELSE v_item:=accounting.quality_candidate(target_book,v_standard,settings);v_candidate:=v_item->'candidate';
   IF v_candidate='null'::jsonb OR v_candidate IS NULL THEN v_status:='uncovered';v_diffs:='[]'::jsonb;
   ELSE v_diffs:=accounting.quality_differences(v_standard,settings,v_candidate);v_status:=CASE WHEN jsonb_array_length(v_diffs)>0 THEN 'different' ELSE 'passed' END;END IF;
  END IF;
  v_rows:=v_rows||jsonb_build_array(v_item||jsonb_build_object('standard_id',v_id,'draft_id',v_standard.draft_id,'business_type',v_standard.snapshot_json::jsonb->>'business_type','status',v_status,'differences',v_diffs));
 END LOOP;
 SELECT jsonb_build_object('passed',count(*) FILTER(WHERE value->>'status'='passed'),'different',count(*) FILTER(WHERE value->>'status'='different'),'uncovered',count(*) FILTER(WHERE value->>'status'='uncovered'),'stale',count(*) FILTER(WHERE value->>'status'='stale')) INTO v_counts FROM jsonb_array_elements(v_rows);
 RETURN jsonb_build_object('engine_version',1,'mode',settings->>'mode','counts',v_counts,'items',v_rows,'scope_note','规则/模板仅核对分录金额、科目及辅助项；分录模型增加证据引用核对；OCR仅核对真人独立确认的字段和页码。未覆盖项不计为通过，来源日期和交易对方仅在对应OCR标注存在时测量。');END $$;

CREATE FUNCTION accounting.quality_run_guard() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.catalog_manage(NEW.book_id) OR NEW.firm_id<>accounting.actor_firm() OR NEW.created_by<>accounting.actor_user() OR NOT EXISTS(SELECT 1 FROM accounting.books WHERE id=NEW.book_id AND firm_id=NEW.firm_id AND company_id=NEW.company_id AND period=NEW.period) THEN RAISE EXCEPTION 'Quality run scope denied' USING ERRCODE='42501';END IF;
 IF NEW.input_fingerprint IS DISTINCT FROM accounting.quality_context(NEW.book_id,NEW.standard_ids_json::jsonb,NEW.settings_json::jsonb) OR NEW.report_json::jsonb IS DISTINCT FROM accounting.quality_report(NEW.book_id,NEW.standard_ids_json::jsonb,NEW.settings_json::jsonb) THEN RAISE EXCEPTION 'Quality report must match native current replay' USING ERRCODE='23514';END IF;
 RETURN NEW;END $$;

CREATE VIEW accounting.quality_standard_types AS SELECT id,book_id,firm_id,snapshot_json::jsonb->>'business_type' AS business_type FROM accounting.quality_standards WHERE firm_id=accounting.actor_firm() AND accounting.catalog_manage(book_id);
CREATE TRIGGER quality_standard_truth BEFORE INSERT ON accounting.quality_standards FOR EACH ROW EXECUTE FUNCTION accounting.quality_standard_guard();
CREATE TRIGGER quality_independent_confirmation BEFORE INSERT ON accounting.quality_confirmations FOR EACH ROW EXECUTE FUNCTION accounting.quality_confirmation_guard();
CREATE TRIGGER quality_native_report BEFORE INSERT ON accounting.quality_runs FOR EACH ROW EXECUTE FUNCTION accounting.quality_run_guard();
CREATE TRIGGER quality_standards_immutable BEFORE UPDATE OR DELETE ON accounting.quality_standards FOR EACH ROW EXECUTE FUNCTION accounting.learning_history_immutable();
CREATE TRIGGER quality_confirmations_immutable BEFORE UPDATE OR DELETE ON accounting.quality_confirmations FOR EACH ROW EXECUTE FUNCTION accounting.learning_history_immutable();
CREATE TRIGGER quality_runs_immutable BEFORE UPDATE OR DELETE ON accounting.quality_runs FOR EACH ROW EXECUTE FUNCTION accounting.learning_history_immutable();
ALTER TABLE accounting.quality_standards ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.quality_standards FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.quality_confirmations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.quality_confirmations FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.quality_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.quality_runs FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.quality_standards,accounting.quality_confirmations,accounting.quality_runs,accounting.quality_standard_types FROM PUBLIC,accounting_worker;
GRANT SELECT,INSERT ON accounting.quality_standards,accounting.quality_confirmations,accounting.quality_runs TO accounting_app;
GRANT SELECT ON accounting.quality_standard_types TO accounting_app;
CREATE POLICY quality_standards_read ON accounting.quality_standards FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.catalog_manage(book_id));
CREATE POLICY quality_standards_insert ON accounting.quality_standards FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
CREATE POLICY quality_confirmations_read ON accounting.quality_confirmations FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.catalog_manage(book_id));
CREATE POLICY quality_confirmations_insert ON accounting.quality_confirmations FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND confirmed_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
CREATE POLICY quality_runs_read ON accounting.quality_runs FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.catalog_manage(book_id));
CREATE POLICY quality_runs_insert ON accounting.quality_runs FOR INSERT TO accounting_app WITH CHECK(firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND accounting.catalog_manage(book_id));
REVOKE ALL ON FUNCTION accounting.quality_evidence(TEXT,TEXT),accounting.quality_standard_current(TEXT),accounting.quality_standard_guard(),accounting.quality_confirmation_guard(),accounting.quality_context(TEXT,JSONB,JSONB),accounting.quality_render(TEXT,JSONB,JSONB),accounting.quality_candidate(TEXT,accounting.quality_standards,JSONB),accounting.quality_differences(accounting.quality_standards,JSONB,JSONB),accounting.quality_report(TEXT,JSONB,JSONB),accounting.quality_run_guard() FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.quality_evidence(TEXT,TEXT),accounting.quality_standard_current(TEXT),accounting.quality_context(TEXT,JSONB,JSONB),accounting.quality_report(TEXT,JSONB,JSONB) TO accounting_app;
