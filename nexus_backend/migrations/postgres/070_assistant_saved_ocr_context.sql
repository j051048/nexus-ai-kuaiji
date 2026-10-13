-- Bind current saved OCR, exception decisions and queue facts to dialogue evidence.
CREATE FUNCTION accounting.assistant_input_basis_core(target TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT CASE WHEN accounting.assistant_conversation_allowed(target) THEN encode(sha256(convert_to(jsonb_build_object(
  'scope',jsonb_build_array(c.id,c.scope_version,c.book_ids_json::jsonb,c.event_ids_json::jsonb,c.draft_id),
  'books',(SELECT coalesce(jsonb_agg(jsonb_build_array(b.id,b.name,b.period,p.status,(SELECT count(*) FROM accounting.sources s WHERE s.book_id=b.id),(SELECT coalesce(sum(s.amount_cents),0) FROM accounting.sources s WHERE s.book_id=b.id),(SELECT coalesce(jsonb_agg(jsonb_build_array(x.status,x.n) ORDER BY x.status),'[]'::jsonb) FROM (SELECT d.status,count(*) n FROM accounting.drafts d WHERE d.book_id=b.id GROUP BY d.status) x)) ORDER BY b.id),'[]'::jsonb) FROM accounting.books b JOIN accounting.periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id IN (SELECT value FROM jsonb_array_elements_text(c.book_ids_json::jsonb))),
  'events',(SELECT coalesce(jsonb_agg(jsonb_build_array(e.id,e.version,e.fact_version,e.status,e.payload::jsonb,(SELECT coalesce(jsonb_agg(jsonb_build_array(a.id,a.sha256) ORDER BY a.id),'[]'::jsonb) FROM accounting.business_attachments a WHERE a.event_id=e.id)) ORDER BY e.id),'[]'::jsonb) FROM accounting.business_events e WHERE e.id IN (SELECT value FROM jsonb_array_elements_text(c.event_ids_json::jsonb))),
  'draft',(SELECT jsonb_build_array(d.id,d.version,d.status,d.description,d.explanation,d.source_id,(SELECT coalesce(jsonb_agg(to_jsonb(l) ORDER BY l.ordinal),'[]'::jsonb) FROM accounting.draft_lines l WHERE l.draft_id=d.id)) FROM accounting.drafts d WHERE d.id=c.draft_id)
 )::text,'UTF8')),'hex') ELSE NULL END FROM accounting.assistant_conversations c WHERE c.id=target
$$;
CREATE OR REPLACE FUNCTION accounting.assistant_input_basis(target TEXT) RETURNS TEXT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT CASE WHEN accounting.assistant_conversation_allowed(c.id) THEN encode(sha256(convert_to(jsonb_build_object(
 'facts',accounting.assistant_input_basis_core(c.id),
 'evidence',(SELECT coalesce(jsonb_agg(jsonb_build_object('id',e.id,
   'runs',(SELECT coalesce(jsonb_agg(jsonb_build_array(r.id,r.status,r.result_json,r.error_code) ORDER BY r.id),'[]'::jsonb) FROM accounting.ai_runs r WHERE r.event_id=e.id),
   'findings',(SELECT coalesce(jsonb_agg(jsonb_build_array(f.id,f.code,f.severity,f.payload_json,f.fingerprint,(SELECT coalesce(jsonb_agg(jsonb_build_array(v.id,v.reason) ORDER BY v.id),'[]'::jsonb) FROM accounting.ai_finding_reviews v WHERE v.finding_id=f.id)) ORDER BY f.id),'[]'::jsonb) FROM accounting.ai_findings f WHERE f.event_id=e.id),
   'ocr',(SELECT coalesce(jsonb_agg(jsonb_build_array(q.id,q.status,q.expected_fact_version) ORDER BY q.id),'[]'::jsonb) FROM accounting.ocr_jobs q WHERE q.event_id=e.id),
   'proposals',(SELECT coalesce(jsonb_agg(jsonb_build_array(q.id,q.status,q.expected_fact_version,q.expected_draft_version) ORDER BY q.id),'[]'::jsonb) FROM accounting.proposal_jobs q WHERE q.event_id=e.id),
   'cases',(SELECT coalesce(jsonb_agg(jsonb_build_array(t.id,t.version,t.state,t.evidence_revision) ORDER BY t.id),'[]'::jsonb) FROM accounting.case_tasks t WHERE t.event_id=e.id),
   'supersessions',(SELECT coalesce(jsonb_agg(jsonb_build_array(s.old_attachment_id,s.response_id) ORDER BY s.old_attachment_id),'[]'::jsonb) FROM accounting.case_supersessions s WHERE s.event_id=e.id)
  ) ORDER BY e.id),'[]'::jsonb) FROM accounting.business_events e WHERE e.id IN (SELECT value FROM jsonb_array_elements_text(c.event_ids_json::jsonb)))
 )::text,'UTF8')),'hex') ELSE NULL END FROM accounting.assistant_conversations c WHERE c.id=target
$$;
REVOKE ALL ON FUNCTION accounting.assistant_input_basis_core(TEXT) FROM PUBLIC,accounting_app,accounting_worker;
