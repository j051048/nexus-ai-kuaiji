ALTER TABLE accounting.business_events ADD COLUMN fact_version INTEGER NOT NULL DEFAULT 1 CHECK(fact_version>0);
UPDATE accounting.business_events SET fact_version=version;
CREATE FUNCTION accounting.business_fact_version_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NEW.fact_version<OLD.fact_version OR NEW.fact_version>OLD.fact_version+1 THEN
   RAISE EXCEPTION 'Business fact versions must track changed facts' USING ERRCODE='23514';
  END IF;
  IF NEW.payload IS DISTINCT FROM OLD.payload AND NEW.fact_version=OLD.fact_version THEN
   NEW.fact_version:=OLD.fact_version+1;
  END IF;
  RETURN NEW;
 END
$$;
REVOKE ALL ON FUNCTION accounting.business_fact_version_guard() FROM PUBLIC;
CREATE TRIGGER business_fact_version_guard BEFORE UPDATE ON accounting.business_events FOR EACH ROW EXECUTE FUNCTION accounting.business_fact_version_guard();
ALTER TABLE accounting.drafts ADD COLUMN last_manual_edit_version INTEGER NOT NULL DEFAULT 0 CHECK(last_manual_edit_version>=0);
UPDATE accounting.drafts SET last_manual_edit_version=version WHERE version>1;
ALTER TABLE accounting.ai_runs ADD COLUMN cache_hit INTEGER NOT NULL DEFAULT 0 CHECK(cache_hit IN (0,1));
CREATE TABLE accounting.document_extractions(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,attachment_sha256 TEXT NOT NULL CHECK(length(attachment_sha256)=64),
 config_key TEXT NOT NULL,result_json TEXT NOT NULL CHECK(jsonb_typeof(result_json::jsonb)='object'),created_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id,attachment_sha256,config_key),
 FOREIGN KEY(firm_id,company_id) REFERENCES accounting.firm_clients(firm_id,company_id)
);
ALTER TABLE accounting.document_extractions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.document_extractions FORCE ROW LEVEL SECURITY;
-- No application/worker SELECT or INSERT grants: even same-company employees
-- cannot enumerate another department's evidence. These capabilities accept an
-- authorized current attachment/run, returning only matching OCR content.
REVOKE ALL ON accounting.document_extractions FROM PUBLIC,accounting_app,accounting_worker;
CREATE FUNCTION accounting.read_document_extraction(target_attachment TEXT,target_config TEXT) RETURNS TEXT
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT c.result_json FROM accounting.business_attachments a JOIN accounting.document_extractions c
 ON c.firm_id=a.firm_id AND c.company_id=a.company_id AND c.attachment_sha256=a.sha256 AND c.config_key=target_config
 JOIN accounting.ai_company_settings s ON s.firm_id=a.firm_id AND s.company_id=a.company_id AND s.enabled=1
 WHERE a.id=target_attachment AND a.firm_id=accounting.actor_firm()
 AND accounting.ai_event_processor(a.event_id,a.firm_id,a.company_id)
 AND accounting.ai_event_open(a.event_id,a.firm_id,a.company_id)
$$;
CREATE FUNCTION accounting.store_document_extraction(target_run TEXT,target_config TEXT) RETURNS VOID
 LANGUAGE sql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 INSERT INTO accounting.document_extractions(firm_id,company_id,attachment_sha256,config_key,result_json,created_at)
 SELECT r.firm_id,r.company_id,a.sha256,target_config,r.result_json,r.finished_at FROM accounting.ai_runs r
 JOIN accounting.business_attachments a ON a.id=r.attachment_id AND a.event_id=r.event_id
 JOIN accounting.ai_company_settings s ON s.firm_id=r.firm_id AND s.company_id=r.company_id AND s.enabled=1
 WHERE r.id=target_run AND r.kind='ocr' AND r.status='succeeded' AND r.created_by=accounting.actor_user()
 AND r.firm_id=accounting.actor_firm() AND accounting.ai_event_processor(r.event_id,r.firm_id,r.company_id)
 AND accounting.ai_event_open(r.event_id,r.firm_id,r.company_id)
 AND NOT EXISTS(SELECT 1 FROM accounting.ai_findings f WHERE f.run_id=r.id AND f.severity='blocking')
 ON CONFLICT(firm_id,company_id,attachment_sha256,config_key)
 DO UPDATE SET result_json=excluded.result_json,created_at=excluded.created_at
$$;
REVOKE ALL ON FUNCTION accounting.read_document_extraction(TEXT,TEXT),accounting.store_document_extraction(TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.read_document_extraction(TEXT,TEXT),accounting.store_document_extraction(TEXT,TEXT) TO accounting_app;
-- Only the restricted capability may refresh derived content. The immutable
-- ai_runs retain the actual evidence and every source-specific result.
CREATE INDEX idx_ai_runs_attachment_latest ON accounting.ai_runs(event_id,attachment_id,created_at DESC,id DESC) WHERE kind='ocr';
