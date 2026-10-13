-- Facts evolve separately from approval transitions. Existing documents begin at
-- their current event version so outstanding pre-migration jobs fail closed.
ALTER TABLE business_events ADD COLUMN fact_version INTEGER NOT NULL DEFAULT 1 CHECK(fact_version>0);
UPDATE business_events SET fact_version=version;
CREATE TRIGGER business_fact_version_guard BEFORE UPDATE ON business_events
 WHEN NEW.fact_version<OLD.fact_version OR NEW.fact_version>OLD.fact_version+1
 BEGIN SELECT RAISE(ABORT,'Business fact versions must track changed facts'); END;
CREATE TRIGGER business_payload_fact_version AFTER UPDATE OF payload ON business_events
 WHEN NEW.payload IS NOT OLD.payload AND NEW.fact_version=OLD.fact_version
 BEGIN UPDATE business_events SET fact_version=fact_version+1 WHERE id=NEW.id; END;
ALTER TABLE drafts ADD COLUMN last_manual_edit_version INTEGER NOT NULL DEFAULT 0 CHECK(last_manual_edit_version>=0);
-- Existing edited drafts are conservatively treated as manually edited.
UPDATE drafts SET last_manual_edit_version=version WHERE version>1;
ALTER TABLE ai_runs ADD COLUMN cache_hit INTEGER NOT NULL DEFAULT 0 CHECK(cache_hit IN (0,1));
CREATE TABLE document_extractions(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,attachment_sha256 TEXT NOT NULL CHECK(length(attachment_sha256)=64),
 config_key TEXT NOT NULL,result_json TEXT NOT NULL CHECK(json_valid(result_json) AND json_type(result_json)='object'),created_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id,attachment_sha256,config_key),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
-- This is a derived cache: explicit, validated re-extraction may refresh its
-- content. Immutable ai_runs retain every original result and application.
CREATE TRIGGER document_extraction_scope_guard BEFORE UPDATE ON document_extractions
 WHEN NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.attachment_sha256 IS NOT OLD.attachment_sha256 OR NEW.config_key IS NOT OLD.config_key
 BEGIN SELECT RAISE(ABORT,'OCR cache scope is immutable'); END;
CREATE INDEX idx_ai_runs_attachment_latest ON ai_runs(event_id,attachment_id,created_at DESC,id DESC) WHERE kind='ocr';
