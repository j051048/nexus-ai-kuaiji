-- Current company identities govern matching; original evidence remains immutable.
CREATE FUNCTION accounting.learning_counterparty_identity(target_book TEXT,target_original TEXT) RETURNS JSONB
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 WITH scope AS (SELECT b.firm_id,b.company_id FROM accounting.books b WHERE b.id=target_book AND accounting.book_allowed(target_book)),
 candidates AS (
  SELECT e.id,e.name,e.version,a.id AS alias_id,a.version AS alias_version FROM scope s JOIN accounting.company_master_entities e ON e.firm_id=s.firm_id AND e.company_id=s.company_id
  LEFT JOIN accounting.company_master_aliases a ON a.entity_id=e.id AND a.firm_id=e.firm_id AND a.company_id=e.company_id AND a.kind='counterparty' AND a.original=target_original
   AND a.status='approved' AND a.reviewed_by IS NOT NULL AND a.reviewed_by<>a.created_by
  WHERE e.kind='counterparty' AND e.active=1 AND (e.name=target_original OR a.id IS NOT NULL)),
 summary AS (SELECT count(DISTINCT id) AS total,min(id) AS entity_id,min(name) AS name,min(version) AS version,
  COALESCE(jsonb_agg(DISTINCT to_jsonb(alias_id) ORDER BY to_jsonb(alias_id)) FILTER(WHERE alias_id IS NOT NULL),'[]'::jsonb) AS aliases,
  COALESCE(jsonb_agg(DISTINCT jsonb_build_array(alias_id,alias_version) ORDER BY jsonb_build_array(alias_id,alias_version)) FILTER(WHERE alias_id IS NOT NULL),'[]'::jsonb) AS versions FROM candidates)
 SELECT jsonb_build_object('status',CASE WHEN total=1 THEN 'resolved' WHEN total>1 THEN 'ambiguous' ELSE 'unresolved' END,
  'original',target_original,'entity_id',CASE WHEN total=1 THEN entity_id END,'canonical_value',CASE WHEN total=1 THEN name END,
  'entity_version',CASE WHEN total=1 THEN version END,'alias_ids',aliases,'alias_versions',versions)
 FROM summary WHERE EXISTS(SELECT 1 FROM scope)
$$;
CREATE FUNCTION accounting.learning_master_context(target_book TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT md5(jsonb_build_object('entities',COALESCE((SELECT jsonb_agg(jsonb_build_array(e.id,e.name,e.active,e.version) ORDER BY e.id)
  FROM accounting.company_master_entities e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND e.kind='counterparty'),'[]'::jsonb),
  'aliases',COALESCE((SELECT jsonb_agg(jsonb_build_array(a.id,a.entity_id,a.original,a.status,a.version,a.created_by,a.reviewed_by) ORDER BY a.id)
  FROM accounting.company_master_aliases a WHERE a.firm_id=b.firm_id AND a.company_id=b.company_id AND a.kind='counterparty'),'[]'::jsonb))::TEXT)
 FROM accounting.books b WHERE b.id=target_book AND accounting.book_allowed(target_book)
$$;
CREATE FUNCTION accounting.learning_identity_valid(target_book TEXT,definition JSONB) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book) AND CASE WHEN definition->'conditions'->>'counterparty_id' IS NULL THEN true ELSE
  jsonb_typeof(definition->'conditions'->'counterparty_id')='string' AND length(definition->'conditions'->>'counterparty_id') BETWEEN 1 AND 100
  AND accounting.learning_counterparty_identity(target_book,definition->'conditions'->>'counterparty')->>'status'='resolved'
  AND accounting.learning_counterparty_identity(target_book,definition->'conditions'->>'counterparty')->>'entity_id'=definition->'conditions'->>'counterparty_id' END
$$;
CREATE FUNCTION accounting.learning_matches_scoped(target_book TEXT,definition JSONB,snapshot JSONB) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT COALESCE(accounting.learning_identity_valid(target_book,definition) AND CASE WHEN definition->'conditions'->>'counterparty_id' IS NULL
  THEN accounting.learning_matches(definition,snapshot) ELSE
  accounting.learning_matches(jsonb_set(definition,'{conditions,counterparty}',COALESCE(snapshot->'payload'->'counterparty','null'::jsonb)),snapshot)
  AND accounting.learning_counterparty_identity(target_book,snapshot->'payload'->>'counterparty')->>'status'='resolved'
  AND accounting.learning_counterparty_identity(target_book,snapshot->'payload'->>'counterparty')->>'entity_id'=definition->'conditions'->>'counterparty_id' END,false)
$$;
CREATE FUNCTION accounting.learning_semantics(definition JSONB) RETURNS JSONB LANGUAGE sql IMMUTABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT jsonb_build_object('source_kind',definition->'source_kind','conditions',CASE WHEN definition->'conditions'->>'counterparty_id' IS NULL
  THEN (definition->'conditions')-'counterparty_id' ELSE (definition->'conditions')-'counterparty' END,
  'lines',COALESCE((SELECT jsonb_agg(l.value ORDER BY l.value->>'account_code',l.value->>'side',l.value->>'amount_field')
   FROM jsonb_array_elements(CASE WHEN jsonb_typeof(definition->'lines')='array' THEN definition->'lines' ELSE '[]'::jsonb END) l),'[]'::jsonb))
$$;
CREATE FUNCTION accounting.learning_overlaps(target_book TEXT,left_definition JSONB,right_definition JSONB) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT COALESCE(accounting.learning_identity_valid(target_book,left_definition) AND accounting.learning_identity_valid(target_book,right_definition)
  AND left_definition->>'source_kind'=right_definition->>'source_kind'
  AND left_definition->'conditions'->>'business_type'=right_definition->'conditions'->>'business_type'
  AND left_definition->'conditions'->>'direction'=right_definition->'conditions'->>'direction'
  AND left_definition->'conditions'->>'payment_status'=right_definition->'conditions'->>'payment_status'
  AND (left_definition->'conditions'->>'category' IS NULL OR right_definition->'conditions'->>'category' IS NULL OR left_definition->'conditions'->>'category'=right_definition->'conditions'->>'category')
  AND CASE WHEN left_definition->'conditions'->>'counterparty_id' IS NULL AND right_definition->'conditions'->>'counterparty_id' IS NULL
   THEN left_definition->'conditions'->>'counterparty'=right_definition->'conditions'->>'counterparty'
   ELSE COALESCE(left_definition->'conditions'->>'counterparty_id',accounting.learning_counterparty_identity(target_book,left_definition->'conditions'->>'counterparty')->>'entity_id')
    =COALESCE(right_definition->'conditions'->>'counterparty_id',accounting.learning_counterparty_identity(target_book,right_definition->'conditions'->>'counterparty')->>'entity_id') END,false)
$$;
CREATE FUNCTION accounting.learning_conflicts(target_book TEXT,target_rule TEXT,definition JSONB) RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target_book) AND EXISTS(SELECT 1 FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version
  WHERE r.book_id=target_book AND r.id<>target_rule AND r.status='published' AND accounting.learning_overlaps(target_book,definition,v.definition_json::jsonb))
$$;
CREATE FUNCTION accounting.learning_identity_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$ DECLARE definition JSONB; BEGIN
 IF TG_TABLE_NAME='learning_samples' THEN
  IF NEW.snapshot_json::jsonb ? 'counterparty_identity' AND NEW.snapshot_json::jsonb->'counterparty_identity' IS DISTINCT FROM
   accounting.learning_counterparty_identity(NEW.book_id,NEW.snapshot_json::jsonb->'payload'->>'counterparty') THEN
   RAISE EXCEPTION 'Learning identity projection must match current scoped master data' USING ERRCODE='23514'; END IF;
 ELSIF TG_TABLE_NAME='learning_rule_versions' THEN
  definition:=NEW.definition_json::jsonb;
  IF NOT accounting.learning_identity_valid(NEW.book_id,definition) THEN RAISE EXCEPTION 'Learning identity must resolve uniquely to own active company master' USING ERRCODE='23514'; END IF;
  IF EXISTS(SELECT 1 FROM accounting.learning_rules r JOIN accounting.learning_rule_versions v ON v.rule_id=r.id AND v.book_id=r.book_id AND v.version=r.current_version
   WHERE r.book_id=NEW.book_id AND r.id<>NEW.rule_id AND r.status<>'retired' AND accounting.learning_semantics(v.definition_json::jsonb)=accounting.learning_semantics(definition)) THEN
   RAISE EXCEPTION 'Duplicate learning semantics require explicit revision; samples are not merged' USING ERRCODE='23514'; END IF;
 ELSE
  IF NEW.status='published' THEN
   SELECT v.definition_json::jsonb INTO definition FROM accounting.learning_rule_versions v WHERE v.rule_id=NEW.id AND v.book_id=NEW.book_id AND v.version=NEW.current_version;
   IF NOT accounting.learning_identity_valid(NEW.book_id,definition) OR accounting.learning_conflicts(NEW.book_id,NEW.id,definition) THEN
    RAISE EXCEPTION 'Learning publication identity or overlapping rule conflict requires review' USING ERRCODE='23514'; END IF;
  END IF;
 END IF;
 RETURN NEW; END $$;
CREATE TRIGGER learning_identity_capture BEFORE INSERT ON accounting.learning_samples FOR EACH ROW EXECUTE FUNCTION accounting.learning_identity_guard();
CREATE TRIGGER learning_identity_version BEFORE INSERT ON accounting.learning_rule_versions FOR EACH ROW EXECUTE FUNCTION accounting.learning_identity_guard();
CREATE TRIGGER learning_identity_publish BEFORE UPDATE OF status ON accounting.learning_rules FOR EACH ROW EXECUTE FUNCTION accounting.learning_identity_guard();
REVOKE ALL ON FUNCTION accounting.learning_counterparty_identity(TEXT,TEXT),accounting.learning_master_context(TEXT),accounting.learning_identity_valid(TEXT,JSONB),
 accounting.learning_matches_scoped(TEXT,JSONB,JSONB),accounting.learning_semantics(JSONB),accounting.learning_overlaps(TEXT,JSONB,JSONB),accounting.learning_conflicts(TEXT,TEXT,JSONB),accounting.learning_identity_guard() FROM PUBLIC,accounting_app,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.learning_counterparty_identity(TEXT,TEXT),accounting.learning_master_context(TEXT),accounting.learning_identity_valid(TEXT,JSONB),
 accounting.learning_matches_scoped(TEXT,JSONB,JSONB),accounting.learning_semantics(JSONB),accounting.learning_overlaps(TEXT,JSONB,JSONB),accounting.learning_conflicts(TEXT,TEXT,JSONB) TO accounting_app;

-- Native evaluation and lease-fenced application share the same scoped matcher.
CREATE OR REPLACE FUNCTION accounting.learning_db_context(target_rule TEXT,target_book TEXT,target_version INTEGER) RETURNS TEXT LANGUAGE sql STABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT md5(jsonb_build_object('definition',v.definition_json::jsonb,'version',v.version,'positive',v.sample_ids_json::jsonb,'master',accounting.learning_master_context(target_book),'published',COALESCE((SELECT jsonb_agg(jsonb_build_array(r.id,r.current_version,r.state_version) ORDER BY r.id) FROM accounting.learning_rules r WHERE r.book_id=target_book AND r.status='published'),'[]'::jsonb),
  'catalog',COALESCE((SELECT jsonb_agg(jsonb_build_array(a.code,a.name,a.active,a.version,a.auxiliary_requirements::jsonb) ORDER BY a.code) FROM accounting.accounts a WHERE a.book_id=target_book),'[]'::jsonb),
  'samples',COALESCE((SELECT jsonb_agg(jsonb_build_array(s.id,s.sample_fingerprint,s.snapshot_json::jsonb,s.final_json::jsonb,d.status IN ('approved','exported'),d.approved_by) ORDER BY s.id)
   FROM accounting.learning_samples s JOIN accounting.drafts d ON d.id=s.draft_id AND d.book_id=s.book_id WHERE s.book_id=target_book),'[]'::jsonb))::text)
 FROM accounting.learning_rule_versions v WHERE v.rule_id=target_rule AND v.book_id=target_book AND v.version=target_version AND accounting.book_allowed(target_book)
 $$;
CREATE OR REPLACE FUNCTION accounting.learning_db_evaluation(target_rule TEXT,target_book TEXT,target_version INTEGER,negative_ids JSONB) RETURNS JSONB LANGUAGE plpgsql STABLE
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE definition JSONB; positive_ids JSONB; sample RECORD; positives INTEGER; negatives INTEGER; scanned INTEGER:=0; matched INTEGER:=0;
  passed BOOLEAN:=true;
 BEGIN
 SELECT definition_json::jsonb,sample_ids_json::jsonb INTO definition,positive_ids FROM accounting.learning_rule_versions
  WHERE rule_id=target_rule AND book_id=target_book AND version=target_version;
 IF NOT FOUND OR NOT accounting.book_allowed(target_book) OR jsonb_typeof(negative_ids) IS DISTINCT FROM 'array'
  OR jsonb_array_length(negative_ids) NOT BETWEEN 1 AND 100 THEN RAISE EXCEPTION 'learning requires real positive and negative examples'; END IF;
 positives:=jsonb_array_length(positive_ids);negatives:=jsonb_array_length(negative_ids);
 IF NOT accounting.learning_identity_valid(target_book,definition) OR accounting.learning_conflicts(target_book,target_rule,definition) THEN passed:=false; END IF;
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
  IF positive_ids @> jsonb_build_array(sample.id) AND NOT accounting.learning_matches_scoped(target_book,definition,sample.snapshot_json::jsonb) THEN passed:=false; END IF;
  IF NOT accounting.learning_matches_scoped(target_book,definition,sample.snapshot_json::jsonb) THEN CONTINUE; END IF;
  matched:=matched+1;
  IF negative_ids @> jsonb_build_array(sample.id) OR sample.draft_status NOT IN ('approved','exported') OR sample.approved_by IS DISTINCT FROM sample.reviewed_by
   OR NOT accounting.learning_render_matches(target_book,definition,sample.snapshot_json::jsonb,sample.final_json::jsonb) THEN passed:=false; END IF;
 END LOOP;
 RETURN jsonb_build_object('passed',passed,'positive_count',positives,'negative_count',negatives,'scanned_count',scanned,'matched_count',matched);
 END $$;

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
   AND accounting.learning_matches_scoped(j.book_id,v.definition_json::jsonb,jsonb_build_object('source_kind','business','business_type',e.template_id,'payload',e.payload::jsonb))
   AND accounting.learning_render_matches(j.book_id,v.definition_json::jsonb,
    jsonb_build_object('source_kind','business','business_type',e.template_id,'payload',e.payload::jsonb,
     'context',jsonb_build_object('department_id',e.department_id,'applicant_id',e.applicant_id),
     'recognized',accounting.learning_recognized(e.id,e.fact_version,(SELECT amount_cents FROM accounting.sources WHERE id=d.source_id AND book_id=j.book_id))),
    jsonb_build_object('lines',(SELECT jsonb_agg(jsonb_build_object('account_code',l.account_code,'debit',(l.debit_cents/100)::text||'.'||lpad((l.debit_cents%100)::text,2,'0'),'credit',(l.credit_cents/100)::text||'.'||lpad((l.credit_cents%100)::text,2,'0'),
     'auxiliary_values',COALESCE(a.values_json::jsonb,'{}'::jsonb)) ORDER BY l.ordinal)
     FROM accounting.draft_lines l LEFT JOIN accounting.draft_line_auxiliaries a ON a.draft_id=l.draft_id AND a.book_id=l.book_id AND a.ordinal=l.ordinal
     WHERE l.draft_id=j.draft_id AND l.book_id=j.book_id)))
   AND NOT accounting.learning_conflicts(j.book_id,r.id,v.definition_json::jsonb)
   AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(v.sample_ids_json::jsonb) refs(sample_id) WHERE NOT accounting.learning_sample_ordinary(j.book_id,refs.sample_id))
   AND NOT accounting.source_workflow_blocked(j.book_id,d.source_id)) THEN RETURN false; END IF;
  stamp:=accounting.ocr_utc_now();
  UPDATE accounting.proposal_attempts SET phase='finished',status='succeeded',error_code='',error_detail='',finished_at=stamp WHERE job_id=j.id AND lease_token=target_token AND status='running';
  UPDATE accounting.proposal_jobs SET status='succeeded',lease_token=NULL,lease_until=NULL,worker_id=NULL,updated_at=stamp,finished_at=stamp,error_code='',error_detail='',
   result_rule_id=target_rule,result_rule_version=target_version,result_draft_version=target_draft_version WHERE id=j.id;
  RETURN true;
 END
$$;
