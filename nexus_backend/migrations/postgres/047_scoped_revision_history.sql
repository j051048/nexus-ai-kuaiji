-- A former period reader may inspect that period's frozen source evidence, but
-- must not acquire visibility of later facts or supplements on the same event.
CREATE OR REPLACE FUNCTION accounting.business_row_visible(target_event TEXT,target_firm TEXT,target_company TEXT,target_department TEXT,target_applicant TEXT,target_status TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT target_firm=accounting.actor_firm() AND accounting.actor_active() AND EXISTS(
  SELECT 1 FROM accounting.firm_clients WHERE firm_id=target_firm AND company_id=target_company AND status='active') AND (
  (accounting.business_member(target_firm,target_company) AND target_applicant=accounting.actor_user())
  OR accounting.business_finance(target_firm,target_company)
  OR accounting.business_department_approver(target_firm,target_company,target_department)
  OR (target_status='handed_off' AND EXISTS(SELECT 1 FROM accounting.business_mappings m
   WHERE m.event_id=target_event AND m.firm_id=target_firm AND m.company_id=target_company AND accounting.book_allowed(m.book_id))))
 $$;

CREATE FUNCTION accounting.revision_source_history(target_book TEXT,target_source TEXT) RETURNS TEXT
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE r accounting.fact_revisions;l accounting.fact_revision_lineage;v accounting.business_versions; BEGIN
 IF NOT accounting.book_allowed(target_book) OR NOT EXISTS(SELECT 1 FROM accounting.sources WHERE id=target_source AND book_id=target_book) THEN
  RAISE EXCEPTION 'Historical source scope denied' USING ERRCODE='42501'; END IF;
 SELECT * INTO l FROM accounting.fact_revision_lineage WHERE old_book_id=target_book AND old_source_id=target_source AND firm_id=accounting.actor_firm();
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO r FROM accounting.fact_revisions WHERE id=l.revision_id AND firm_id=l.firm_id AND state='replaced';
 SELECT * INTO v FROM accounting.business_versions WHERE event_id=l.event_id AND event_version=(r.old_snapshot::jsonb->'mapping'->>'submitted_version')::INTEGER;
 RETURN jsonb_build_object('revision_id',r.id,'event_id',l.event_id,'book_id',target_book,'source_id',target_source,
  'draft_id',l.old_draft_id,'fact_version',l.old_fact_version,'payload',r.old_snapshot::jsonb->'payload',
  'submitted_snapshot',jsonb_build_object('event_version',v.event_version,'payload',v.payload::jsonb,'template_snapshot',v.template_snapshot::jsonb,
   'attachment_snapshot',v.attachment_snapshot::jsonb,'created_at',v.created_at,'created_by',v.created_by),
  'attachments',COALESCE(r.old_snapshot::jsonb->'attachments','[]'::jsonb)||COALESCE(v.attachment_snapshot::jsonb,'[]'::jsonb),
  'reason',r.reason,'created_at',r.created_at,'state','superseded')::TEXT;
 END $$;
REVOKE ALL ON FUNCTION accounting.revision_source_history(TEXT,TEXT) FROM PUBLIC,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.revision_source_history(TEXT,TEXT) TO accounting_app;

CREATE FUNCTION accounting.revision_historical_attachment(target_book TEXT,target_source TEXT,target_attachment TEXT) RETURNS TEXT
 LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 DECLARE snapshot JSONB;entry JSONB;attachment accounting.business_attachments; BEGIN
 snapshot:=accounting.revision_source_history(target_book,target_source)::jsonb;
 IF snapshot IS NULL THEN RETURN NULL; END IF;
 SELECT value INTO entry FROM jsonb_array_elements(snapshot->'attachments') WHERE value->>'id'=target_attachment LIMIT 1;
 IF NOT FOUND THEN RETURN NULL; END IF;
 SELECT * INTO attachment FROM accounting.business_attachments WHERE id=target_attachment AND event_id=snapshot->>'event_id' AND firm_id=accounting.actor_firm();
 IF NOT FOUND OR attachment.sha256 IS DISTINCT FROM entry->>'sha256' OR attachment.size_bytes::TEXT IS DISTINCT FROM entry->>'size_bytes' THEN RETURN NULL; END IF;
 RETURN to_jsonb(attachment)::TEXT;
 END $$;
REVOKE ALL ON FUNCTION accounting.revision_historical_attachment(TEXT,TEXT,TEXT) FROM PUBLIC,accounting_worker;
GRANT EXECUTE ON FUNCTION accounting.revision_historical_attachment(TEXT,TEXT,TEXT) TO accounting_app;
