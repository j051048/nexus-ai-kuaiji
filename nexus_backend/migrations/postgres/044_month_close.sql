CREATE FUNCTION accounting.closing_fingerprint(target_book TEXT) RETURNS TEXT LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE basis JSONB; BEGIN
 IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'current scoped book reviewer required' USING ERRCODE='42501'; END IF;
 SELECT jsonb_build_object('books',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM books t WHERE t.id=b.id),'periods',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM periods t WHERE t.book_id=b.id),'sources',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM sources t WHERE t.book_id=b.id),'drafts',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM drafts t WHERE t.book_id=b.id),'draft_lines',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM draft_lines t WHERE t.book_id=b.id),'draft_line_auxiliaries',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM draft_line_auxiliaries t WHERE t.book_id=b.id),'accounts',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM accounts t WHERE t.book_id=b.id),'source_evidence',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM source_evidence t WHERE t.book_id=b.id),'business_mappings',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM business_mappings t WHERE t.book_id=b.id),'business_mapping_versions',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM business_mapping_versions t WHERE t.book_id=b.id),'business_events',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM business_events t WHERE t.firm_id=b.firm_id AND t.company_id=b.company_id AND (substr(t.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions m WHERE m.event_id=t.id AND m.book_id=b.id))),'business_attachments',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM business_attachments t WHERE t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id)))),'case_tasks',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM case_tasks t WHERE t.book_id=b.id OR t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id)))),'case_responses',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM case_responses t WHERE t.case_id IN(SELECT c.id FROM case_tasks c WHERE c.book_id=b.id OR c.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id))))),'case_supersessions',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM case_supersessions t WHERE t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id)))),'ocr_jobs',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM ocr_jobs t WHERE t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id)))),'proposal_jobs',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM proposal_jobs t WHERE t.book_id=b.id),'ai_runs',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM ai_runs t WHERE t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id)))),'model_costs',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM model_costs t WHERE t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id)))),'ai_findings',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM ai_findings t WHERE t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id)))),'ai_finding_reviews',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM ai_finding_reviews t WHERE t.event_id IN(SELECT e.id FROM business_events e WHERE e.firm_id=b.firm_id AND e.company_id=b.company_id AND (substr(e.payload::jsonb->>'occurred_on',1,7)=b.period OR EXISTS(SELECT 1 FROM business_mapping_versions mv WHERE mv.event_id=e.id AND mv.book_id=b.id)))),'ai_bank_links',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM ai_bank_links t WHERE t.book_id=b.id),'bank_import_keys',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM bank_import_keys t WHERE t.book_id=b.id),'bank_duplicate_decisions',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM bank_duplicate_decisions t WHERE t.book_id=b.id),'bank_source_dispositions',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM bank_source_dispositions t WHERE t.book_id=b.id),'exports',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM exports t WHERE t.book_id=b.id),'delivery_manifests',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM delivery_manifests t WHERE t.book_id=b.id),'delivery_receipts',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM delivery_receipts t WHERE t.book_id=b.id),'delivery_receipt_items',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM delivery_receipt_items t WHERE t.book_id=b.id),'fact_revisions',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM fact_revisions t WHERE t.old_book_id=b.id OR t.target_book_id=b.id),'fact_revision_lineage',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM fact_revision_lineage t WHERE t.old_book_id=b.id OR t.new_book_id=b.id),'company_master_entities',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM company_master_entities t WHERE t.firm_id=b.firm_id AND t.company_id=b.company_id),'company_master_aliases',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM company_master_aliases t WHERE t.firm_id=b.firm_id AND t.company_id=b.company_id),'journal_templates',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM journal_templates t WHERE t.book_id=b.id),'journal_template_versions',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM journal_template_versions t WHERE t.book_id=b.id),'learning_rules',(SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text),'[]'::jsonb) FROM learning_rules t WHERE t.book_id=b.id)) INTO basis FROM accounting.books b WHERE b.id=target_book;
 RETURN encode(sha256(convert_to(basis::text,'UTF8')),'hex'); END $$;
CREATE FUNCTION accounting.closing_issues(target_book TEXT) RETURNS TABLE(category TEXT,object_type TEXT,object_id TEXT,detail TEXT) LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF NOT accounting.ai_book_reviewer(target_book) THEN RAISE EXCEPTION 'current scoped book reviewer required' USING ERRCODE='42501'; END IF;
 RETURN QUERY
WITH book AS (SELECT * FROM books WHERE id=target_book),
      active_sources AS (SELECT s.* FROM sources s JOIN book b ON b.id=s.book_id WHERE NOT EXISTS(SELECT 1 FROM bank_source_dispositions z WHERE z.book_id=s.book_id AND z.source_id=s.id) AND NOT EXISTS(SELECT 1 FROM fact_revision_lineage z WHERE z.old_book_id=s.book_id AND z.old_source_id=s.id)),
      active_drafts AS (SELECT d.* FROM drafts d JOIN active_sources s ON s.id=d.source_id AND s.book_id=d.book_id
        WHERE NOT EXISTS(SELECT 1 FROM ai_bank_links link WHERE link.bank_source_id=s.id AND link.book_id=s.book_id)),
      event_scope AS (SELECT e.*,b.id AS scope_book_id FROM business_events e JOIN book b ON b.firm_id=e.firm_id AND b.company_id=e.company_id
        WHERE substr(jsonb_extract_path_text((e.payload)::jsonb,'occurred_on'),1,7)=b.period OR EXISTS(SELECT 1 FROM business_mappings m WHERE m.event_id=e.id AND m.book_id=b.id)),
      issues AS (
      SELECT 'unreviewed' AS category,'draft' AS object_type,d.id AS object_id,'尚有未审核或未导出的有效凭证' AS detail FROM active_drafts d WHERE d.status<>'exported'
      UNION ALL SELECT 'unprocessed','source',s.id,'来源尚未形成有效凭证草稿' FROM active_sources s WHERE NOT EXISTS(SELECT 1 FROM drafts d WHERE d.book_id=s.book_id AND d.source_id=s.id)
      UNION ALL SELECT 'missing_evidence','source',s.id,'当前有效来源缺少原件或证据引用' FROM active_sources s WHERE trim(COALESCE((SELECT se.evidence_ref FROM source_evidence se WHERE se.book_id=s.book_id AND se.source_id=s.id ORDER BY se.id DESC LIMIT 1),s.evidence_ref))=''
      UNION ALL SELECT 'business_pending','book',b.id,'本企业本期仍有未完成审批或移交的业务事项，请企业财务核对' FROM book b WHERE EXISTS(SELECT 1 FROM event_scope e WHERE e.scope_book_id=b.id AND e.status<>'handed_off')
      UNION ALL SELECT 'missing_evidence','event',m.event_id,'已移交事项缺少当前有效附件' FROM business_mappings m JOIN book b ON b.id=m.book_id
        WHERE NOT EXISTS(SELECT 1 FROM business_attachments a WHERE a.event_id=m.event_id AND NOT EXISTS(SELECT 1 FROM case_supersessions z WHERE z.event_id=a.event_id AND z.old_attachment_id=a.id))
      UNION ALL SELECT 'open_cases','case',c.id,'本账套仍有未关闭的异常或补件待办' FROM case_tasks c JOIN book b ON c.book_id=b.id OR c.event_id IN(SELECT m.event_id FROM business_mappings m WHERE m.book_id=b.id) WHERE c.state<>'closed'
      UNION ALL SELECT 'open_cases','book',b.id,'本企业本期未移交事项仍有待处理补件，请企业财务核对' FROM book b WHERE EXISTS(SELECT 1 FROM case_tasks c JOIN event_scope e ON e.id=c.event_id AND e.scope_book_id=b.id WHERE c.book_id IS NULL AND c.state<>'closed' AND NOT EXISTS(SELECT 1 FROM business_mappings m WHERE m.event_id=e.id AND m.book_id=b.id))
      UNION ALL SELECT 'background_pending','ocr_job',q.id,'票据识别仍在运行或存在发送结果未知的任务' FROM ocr_jobs q JOIN event_scope e ON e.id=q.event_id WHERE q.status IN ('queued','running','retry_wait') AND NOT EXISTS(SELECT 1 FROM case_supersessions z WHERE z.event_id=q.event_id AND z.old_attachment_id=q.attachment_id) AND EXISTS(SELECT 1 FROM business_mappings m JOIN book b ON b.id=m.book_id WHERE m.event_id=e.id)
      UNION ALL SELECT 'background_pending','proposal_job',q.id,'分录任务仍在运行或等待人工处理' FROM proposal_jobs q JOIN book b ON b.id=q.book_id WHERE q.status IN ('waiting','queued','running','retry_wait') OR q.status IN ('needs_attention','interrupted','failed') AND EXISTS(SELECT 1 FROM drafts d WHERE d.id=q.draft_id AND d.book_id=q.book_id AND d.status='draft')
      UNION ALL SELECT 'background_pending','event',r.event_id,'仍有未完成的模型执行记录' FROM ai_runs r JOIN event_scope e ON e.id=r.event_id WHERE r.status='running' AND EXISTS(SELECT 1 FROM business_mappings m JOIN book b ON b.id=m.book_id WHERE m.event_id=e.id)
      UNION ALL SELECT 'model_uncertain','event',c.event_id,'仍有模型外发结果或费用未知的记录' FROM model_costs c JOIN event_scope e ON e.id=c.event_id WHERE c.state='uncertain' AND EXISTS(SELECT 1 FROM business_mappings m JOIN book b ON b.id=m.book_id WHERE m.event_id=e.id)
      UNION ALL SELECT 'unmatched_bank','source',s.id,'银行来源未匹配业务事项，也未形成独立已导出的凭证' FROM active_sources s WHERE s.source_kind='bank' AND NOT EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.bank_source_id=s.id AND l.book_id=s.book_id) AND NOT EXISTS(SELECT 1 FROM drafts d WHERE d.source_id=s.id AND d.book_id=s.book_id AND d.status='exported')
      UNION ALL SELECT 'unmatched_business','event',m.event_id,'已支付业务事项尚未匹配银行来源，请先核对防止重复记账' FROM business_mappings m JOIN event_scope e ON e.id=m.event_id AND e.scope_book_id=m.book_id JOIN book b ON b.id=m.book_id
        WHERE jsonb_extract_path_text((e.payload)::jsonb,'payment_status')='paid' AND NOT EXISTS(SELECT 1 FROM ai_bank_links l WHERE l.event_id=m.event_id AND l.book_id=m.book_id)
        AND NOT EXISTS(SELECT 1 FROM fact_revision_lineage z WHERE z.old_book_id=m.book_id AND z.old_source_id=m.source_id)
      UNION ALL SELECT 'delivery_pending','export',x.id,'导出后缺少内容一致的完整导入回执，或回执仍有未知及差异' FROM exports x JOIN book b ON b.id=x.book_id WHERE NOT EXISTS(SELECT 1 FROM delivery_manifests m JOIN delivery_receipts r ON r.export_id=m.export_id AND r.book_id=m.book_id
      WHERE m.export_id=x.id AND m.book_id=x.book_id AND jsonb_extract_path_text((r.body_json)::jsonb,'book_id')=jsonb_extract_path_text((m.payload_json)::jsonb,'book_id') AND jsonb_extract_path_text((r.body_json)::jsonb,'company_id')=jsonb_extract_path_text((m.payload_json)::jsonb,'company_id') AND jsonb_extract_path_text((r.body_json)::jsonb,'period')=jsonb_extract_path_text((m.payload_json)::jsonb,'period') AND jsonb_extract_path_text((r.body_json)::jsonb,'currency')=jsonb_extract_path_text((m.payload_json)::jsonb,'currency')
      AND NOT EXISTS(SELECT 1 FROM delivery_receipts newer WHERE newer.export_id=r.export_id AND newer.book_id=r.book_id AND (newer.created_at>r.created_at OR newer.created_at=r.created_at AND newer.id>r.id))
      AND (SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((r.body_json)::jsonb,'items')) i(value))=(SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((m.payload_json)::jsonb,'items')) expected(value))
      AND (SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((r.body_json)::jsonb,'items')) i(value))=x.count
      AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(jsonb_extract_path((r.body_json)::jsonb,'items')) i(value)
        WHERE jsonb_extract_path_text((i.value)::jsonb,'status') IS NULL OR jsonb_extract_path_text((i.value)::jsonb,'status')<>'accepted' OR trim(COALESCE(jsonb_extract_path_text((i.value)::jsonb,'external_voucher_id'),''))=''
        OR (SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((r.body_json)::jsonb,'items')) duplicate(value) WHERE jsonb_extract_path_text((duplicate.value)::jsonb,'draft_id')=jsonb_extract_path_text((i.value)::jsonb,'draft_id'))<>1
        OR (SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((r.body_json)::jsonb,'items')) duplicate(value) WHERE jsonb_extract_path_text((duplicate.value)::jsonb,'external_voucher_id')=jsonb_extract_path_text((i.value)::jsonb,'external_voucher_id'))<>1
        OR EXISTS(SELECT 1 FROM delivery_receipt_items h WHERE h.book_id=r.book_id AND h.receipt_id<>r.id AND
          (h.claimed_draft_id<>jsonb_extract_path_text((i.value)::jsonb,'draft_id') AND h.external_voucher_id=jsonb_extract_path_text((i.value)::jsonb,'external_voucher_id')
           OR h.claimed_draft_id=jsonb_extract_path_text((i.value)::jsonb,'draft_id') AND trim(COALESCE(h.external_voucher_id,''))<>'' AND h.external_voucher_id<>jsonb_extract_path_text((i.value)::jsonb,'external_voucher_id')))
        OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(jsonb_extract_path((m.payload_json)::jsonb,'items')) expected(value) WHERE jsonb_extract_path_text((expected.value)::jsonb,'draft_id')=jsonb_extract_path_text((i.value)::jsonb,'draft_id') AND (jsonb_extract_path_text((i.value)::jsonb,'date')) IS NOT DISTINCT FROM (jsonb_extract_path_text((expected.value)::jsonb,'date')) AND (jsonb_extract_path_text((i.value)::jsonb,'amount')) IS NOT DISTINCT FROM (jsonb_extract_path_text((expected.value)::jsonb,'amount')) AND (jsonb_extract_path_text((i.value)::jsonb,'description')) IS NOT DISTINCT FROM (jsonb_extract_path_text((expected.value)::jsonb,'description'))
          AND (SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((i.value)::jsonb,'lines')) line(value))=(SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((expected.value)::jsonb,'lines')) wanted(value))
          AND (SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((i.value)::jsonb,'lines')) line(value))>=2
          AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements(jsonb_extract_path((i.value)::jsonb,'lines')) line(value) WHERE NOT EXISTS(SELECT 1 FROM jsonb_array_elements(jsonb_extract_path((expected.value)::jsonb,'lines')) wanted(value)
            WHERE (jsonb_extract_path_text((line.value)::jsonb,'ordinal')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'ordinal')) AND (jsonb_extract_path_text((line.value)::jsonb,'account_code')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'account_code')) AND (jsonb_extract_path_text((line.value)::jsonb,'debit')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'debit')) AND (jsonb_extract_path_text((line.value)::jsonb,'credit')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'credit')) AND jsonb_typeof(line.value->'auxiliary_values')='object' AND (jsonb_extract_path_text((line.value)::jsonb,'auxiliary_values','department')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'auxiliary_values','department')) AND (jsonb_extract_path_text((line.value)::jsonb,'auxiliary_values','employee')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'auxiliary_values','employee')) AND (jsonb_extract_path_text((line.value)::jsonb,'auxiliary_values','counterparty')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'auxiliary_values','counterparty')) AND (jsonb_extract_path_text((line.value)::jsonb,'auxiliary_values','project')) IS NOT DISTINCT FROM (jsonb_extract_path_text((wanted.value)::jsonb,'auxiliary_values','project')) AND line.value->'auxiliary_values'=wanted.value->'auxiliary_values')
            OR (SELECT count(*) FROM jsonb_array_elements(jsonb_extract_path((i.value)::jsonb,'lines')) duplicate_line(value) WHERE jsonb_extract_path_text((duplicate_line.value)::jsonb,'ordinal')=jsonb_extract_path_text((line.value)::jsonb,'ordinal'))<>1)))
    )
      UNION ALL SELECT 'fact_revision','revision',r.id,'业务事实修订或已导出调整建议尚未完成独立处置' FROM fact_revisions r JOIN book b ON r.old_book_id=b.id OR r.target_book_id=b.id WHERE r.state IN ('pending','adjustment_proposed')
      ) SELECT issues.category,issues.object_type,issues.object_id,issues.detail FROM issues ORDER BY issues.category,issues.object_type,issues.object_id; END $$;

CREATE FUNCTION accounting.closing_report(target_book TEXT) RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 WITH issues AS (SELECT * FROM accounting.closing_issues(target_book)), counts AS (SELECT category,count(*) AS count FROM issues GROUP BY category)
 SELECT jsonb_build_object('engine_version',1,'passed',NOT EXISTS(SELECT 1 FROM issues),'issue_count',(SELECT count(*) FROM issues),
 'truncated',(SELECT count(*) FROM issues)>500,
 'counts',COALESCE((SELECT jsonb_object_agg(category,count) FROM counts),'{}'::jsonb),
 'issues',COALESCE((SELECT jsonb_agg(to_jsonb(selected) ORDER BY selected.category,selected.object_type,selected.object_id) FROM (SELECT * FROM issues ORDER BY category,object_type,object_id LIMIT 500) selected),'[]'::jsonb))
$$;
CREATE TABLE accounting.closing_snapshots(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,period TEXT NOT NULL,
 input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),report_json TEXT NOT NULL CHECK(jsonb_typeof(report_json::jsonb)='object'),
 request_key TEXT NOT NULL CHECK(length(request_key) BETWEEN 8 AND 120),created_by TEXT NOT NULL REFERENCES accounting.users(id),created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,request_key),UNIQUE(id,book_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES accounting.books(id,firm_id,company_id)
);
CREATE TABLE accounting.closing_reviews(
 snapshot_id TEXT NOT NULL,book_id TEXT NOT NULL,reviewed_by TEXT NOT NULL REFERENCES accounting.users(id),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),materials_confirmed BOOLEAN NOT NULL CHECK(materials_confirmed),created_at TEXT NOT NULL,
 PRIMARY KEY(snapshot_id,reviewed_by),FOREIGN KEY(snapshot_id,book_id) REFERENCES accounting.closing_snapshots(id,book_id)
);
CREATE TABLE accounting.closing_receipts(
 book_id TEXT PRIMARY KEY,period TEXT NOT NULL,snapshot_id TEXT NOT NULL,closed_by TEXT NOT NULL REFERENCES accounting.users(id),
 reason TEXT NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 1000),created_at TEXT NOT NULL,
 FOREIGN KEY(snapshot_id,book_id) REFERENCES accounting.closing_snapshots(id,book_id),FOREIGN KEY(book_id,period) REFERENCES accounting.periods(book_id,period)
);
CREATE INDEX idx_closing_history ON accounting.closing_snapshots(book_id,created_at DESC,id);
CREATE FUNCTION accounting.create_closing_snapshot(target_book TEXT,target_key TEXT) RETURNS TEXT LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE book RECORD; prior RECORD; new_id TEXT; stamp TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 IF NOT accounting.ai_book_reviewer(target_book) OR target_key IS NULL OR length(target_key) NOT BETWEEN 8 AND 120 THEN
 RAISE EXCEPTION 'current book reviewer and bounded request key required' USING ERRCODE='42501'; END IF;
 SELECT * INTO book FROM accounting.books WHERE id=target_book AND firm_id=accounting.actor_firm();
 IF NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=book.id AND period=book.period AND status='open') THEN
 RAISE EXCEPTION 'closed period snapshots are history only' USING ERRCODE='23514'; END IF;
 SELECT * INTO prior FROM accounting.closing_snapshots WHERE firm_id=accounting.actor_firm() AND created_by=accounting.actor_user() AND request_key=target_key;
 IF FOUND THEN IF prior.book_id<>target_book THEN RAISE EXCEPTION 'snapshot key belongs to another book'; END IF; RETURN prior.id; END IF;
 new_id:=md5(random()::text||clock_timestamp()::text||target_book); stamp:=accounting.ocr_utc_now();
 INSERT INTO accounting.closing_snapshots(id,firm_id,company_id,book_id,period,input_fingerprint,report_json,request_key,created_by,created_at)
 VALUES(new_id,book.firm_id,book.company_id,book.id,book.period,accounting.closing_fingerprint(book.id),accounting.closing_report(book.id)::text,target_key,accounting.actor_user(),stamp);
 RETURN new_id; END $$;
CREATE FUNCTION accounting.review_closing_snapshot(target_book TEXT,target_snapshot TEXT,target_reason TEXT,confirmed BOOLEAN) RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE item RECORD; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 IF NOT accounting.ai_book_reviewer(target_book) OR confirmed IS DISTINCT FROM true OR target_reason IS NULL OR length(btrim(target_reason)) NOT BETWEEN 3 AND 1000
 THEN RAISE EXCEPTION 'current reviewer and explicit material coverage confirmation required' USING ERRCODE='42501'; END IF;
 SELECT * INTO item FROM accounting.closing_snapshots WHERE id=target_snapshot AND book_id=target_book AND firm_id=accounting.actor_firm();
 IF NOT FOUND OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=item.book_id AND period=item.period AND status='open')
 OR item.input_fingerprint IS DISTINCT FROM accounting.closing_fingerprint(target_book)
 OR item.report_json::jsonb IS DISTINCT FROM accounting.closing_report(target_book) OR (item.report_json::jsonb->>'passed')::BOOLEAN IS DISTINCT FROM true
 THEN RAISE EXCEPTION 'preflight is blocked or stale; recompute before review' USING ERRCODE='23514'; END IF;
 INSERT INTO accounting.closing_reviews(snapshot_id,book_id,reviewed_by,reason,materials_confirmed,created_at)
 VALUES(target_snapshot,target_book,accounting.actor_user(),target_reason,true,accounting.ocr_utc_now()) ON CONFLICT DO NOTHING;
 END $$;
CREATE FUNCTION accounting.close_with_snapshot(target_book TEXT,target_snapshot TEXT,target_reason TEXT) RETURNS VOID LANGUAGE plpgsql VOLATILE SECURITY DEFINER
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE item RECORD; prior RECORD; stamp TEXT; BEGIN
 PERFORM pg_advisory_xact_lock(6420031786281903101);
 IF NOT accounting.ai_book_reviewer(target_book) OR target_reason IS NULL OR length(btrim(target_reason)) NOT BETWEEN 3 AND 1000 THEN
 RAISE EXCEPTION 'current reviewer and close reason required' USING ERRCODE='42501'; END IF;
 SELECT * INTO prior FROM accounting.closing_receipts WHERE book_id=target_book;
 IF FOUND THEN IF prior.snapshot_id<>target_snapshot OR prior.closed_by<>accounting.actor_user() OR prior.reason<>target_reason
 THEN RAISE EXCEPTION 'period already closed with a different reviewed snapshot'; END IF; RETURN; END IF;
 SELECT * INTO item FROM accounting.closing_snapshots WHERE id=target_snapshot AND book_id=target_book AND firm_id=accounting.actor_firm();
 IF NOT FOUND OR item.input_fingerprint IS DISTINCT FROM accounting.closing_fingerprint(target_book)
 OR item.report_json::jsonb IS DISTINCT FROM accounting.closing_report(target_book) OR (item.report_json::jsonb->>'passed')::BOOLEAN IS DISTINCT FROM true
 OR NOT EXISTS(SELECT 1 FROM accounting.closing_reviews WHERE snapshot_id=item.id AND book_id=target_book AND reviewed_by=accounting.actor_user() AND materials_confirmed)
 OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=item.book_id AND period=item.period AND status='open')
 THEN RAISE EXCEPTION 'current passed and explicitly reviewed snapshot required' USING ERRCODE='23514'; END IF;
 stamp:=accounting.ocr_utc_now();
 INSERT INTO accounting.closing_receipts(book_id,period,snapshot_id,closed_by,reason,created_at) VALUES(target_book,item.period,item.id,accounting.actor_user(),target_reason,stamp);
 UPDATE accounting.periods SET status='closed' WHERE book_id=target_book AND period=item.period AND status='open';
 INSERT INTO accounting.audit_events(firm_id,book_id,user_id,action,object_id,details,created_at)
 VALUES(item.firm_id,target_book,accounting.actor_user(),'period.close',item.period,jsonb_build_object('snapshot_id',item.id,'input_fingerprint',item.input_fingerprint,'reason',target_reason)::text,stamp);
 END $$;
CREATE FUNCTION accounting.period_close_guard() RETURNS TRIGGER LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
 -- Trusted restoration/test fixtures use administrator connections. Runtime
 -- roles and their login accounts are never granted BYPASSRLS.
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=current_user AND (rolsuper OR rolbypassrls)) THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END; END IF;
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'period history cannot be deleted' USING ERRCODE='42501'; END IF;
 IF TG_OP='INSERT' THEN
 IF NEW.status<>'open' THEN RAISE EXCEPTION 'new period must start open; reviewed close is required' USING ERRCODE='42501'; END IF;
 RETURN NEW; END IF;
 IF (NEW.book_id,NEW.period) IS DISTINCT FROM (OLD.book_id,OLD.period) OR NEW.status IS DISTINCT FROM OLD.status
 THEN RAISE EXCEPTION 'period transition requires the reviewed close capability; reopen is unavailable' USING ERRCODE='42501'; END IF;
 RETURN NEW; END $$;
CREATE TRIGGER period_reviewed_close BEFORE INSERT OR UPDATE OR DELETE ON accounting.periods FOR EACH ROW EXECUTE FUNCTION accounting.period_close_guard();
REVOKE ALL ON FUNCTION accounting.closing_fingerprint(TEXT),accounting.closing_issues(TEXT),accounting.closing_report(TEXT),accounting.create_closing_snapshot(TEXT,TEXT),
 accounting.review_closing_snapshot(TEXT,TEXT,TEXT,BOOLEAN),accounting.close_with_snapshot(TEXT,TEXT,TEXT),accounting.period_close_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.closing_fingerprint(TEXT),accounting.closing_issues(TEXT),accounting.closing_report(TEXT),accounting.create_closing_snapshot(TEXT,TEXT),
 accounting.review_closing_snapshot(TEXT,TEXT,TEXT,BOOLEAN),accounting.close_with_snapshot(TEXT,TEXT,TEXT) TO accounting_app;
DO $$ DECLARE tab TEXT; BEGIN
 FOREACH tab IN ARRAY ARRAY['closing_snapshots','closing_reviews','closing_receipts'] LOOP
 EXECUTE format('ALTER TABLE accounting.%I ENABLE ROW LEVEL SECURITY',tab);
 EXECUTE format('ALTER TABLE accounting.%I FORCE ROW LEVEL SECURITY',tab);
 EXECUTE format('REVOKE ALL ON accounting.%I FROM PUBLIC,accounting_app,accounting_worker',tab);
 EXECUTE format('GRANT SELECT ON accounting.%I TO accounting_app',tab);
 EXECUTE format('CREATE TRIGGER close_history_immutable BEFORE UPDATE OR DELETE ON accounting.%I FOR EACH ROW EXECUTE FUNCTION accounting.ai_immutable_guard()',tab);
 EXECUTE format('CREATE POLICY close_history_read ON accounting.%I FOR SELECT TO accounting_app USING(accounting.ai_book_reviewer(book_id))',tab);
 END LOOP;
END $$;
