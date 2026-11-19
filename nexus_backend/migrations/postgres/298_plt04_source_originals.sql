-- compatibility: incremental
-- PLT04-D: immutable source originals through the existing two-phase upload binding.
-- compatibility: breaking
-- NEXT P0: bring PG tax receipt upload into the existing immutable binding contract.
-- Preserve every previous purpose and historical migration checksum.
ALTER TABLE accounting.upload_intents DROP CONSTRAINT IF EXISTS upload_intents_purpose_check;
ALTER TABLE accounting.upload_intents ADD CONSTRAINT upload_intents_purpose_check
 CHECK(purpose IN('intake_attachment','event_attachment','case_attachment','material_attachment','bank_import','invoice_import','tax_filing_attachment','source_original'));


CREATE TABLE IF NOT EXISTS accounting.source_originals(
 id TEXT PRIMARY KEY,
 source_id TEXT NOT NULL,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 storage_key TEXT NOT NULL UNIQUE,
 filename TEXT NOT NULL CHECK(char_length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(char_length(sha256)=64),
 size_bytes BIGINT NOT NULL CHECK(size_bytes>0),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL,
 FOREIGN KEY(source_id,book_id) REFERENCES accounting.sources(id,book_id)
);
CREATE INDEX IF NOT EXISTS idx_source_originals_source
 ON accounting.source_originals(source_id,created_at);
ALTER TABLE accounting.source_originals ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.source_originals FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.source_originals FROM PUBLIC, accounting_app, accounting_worker;
GRANT SELECT,INSERT ON accounting.source_originals TO accounting_app;
DROP POLICY IF EXISTS plt04_source_original_read ON accounting.source_originals;
CREATE POLICY plt04_source_original_read ON accounting.source_originals FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
DROP POLICY IF EXISTS plt04_source_original_insert ON accounting.source_originals;
CREATE POLICY plt04_source_original_insert ON accounting.source_originals FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id)
 AND created_by=accounting.actor_user());

CREATE OR REPLACE FUNCTION accounting.upload_binding_expected(purpose TEXT, object_key TEXT, uploader TEXT, firm TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER
SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 CASE purpose
 WHEN 'intake_attachment' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','intake_attachment','target_kind','intake_draft','target_id',a.intake_id,'attachment_table','intake_attachments','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',NULL,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.intake_attachments a JOIN accounting.intake_drafts t ON t.id=a.intake_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'event_attachment' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','event_attachment','target_kind','business_event','target_id',a.event_id,'attachment_table','business_attachments','attachment_id',a.id,'event_id',t.id,'source_id',NULL,'book_id',NULL,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.business_attachments a JOIN accounting.business_events t ON t.id=a.event_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'case_attachment' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','case_attachment','target_kind','case_task','target_id',a.case_id,'attachment_table','case_attachments','attachment_id',a.id,'event_id',t.event_id,'source_id',t.source_id,'book_id',t.book_id,'response_id',a.response_id,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.case_attachments a JOIN accounting.case_tasks t ON t.id=a.case_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'material_attachment' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','material_attachment','target_kind','material_request','target_id',a.request_id,'attachment_table','material_attachments','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.book_id,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.material_attachments a JOIN accounting.material_requests t ON t.id=a.request_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'bank_import' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','bank_import','target_kind','book','target_id',a.book_id,'attachment_table','bank_import_files','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.id,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.bank_import_files a JOIN accounting.books t ON t.id=a.book_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'invoice_import' THEN RETURN (SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','invoice_import','target_kind','book','target_id',a.book_id,'attachment_table','invoice_import_batches','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.id,'response_id',NULL,'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM accounting.invoice_import_batches a JOIN accounting.books t ON t.id=a.book_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'tax_filing_attachment' THEN RETURN (
  SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,
   'purpose','tax_filing_attachment','target_kind','tax_filing','target_id',a.filing_id,
   'attachment_table','tax_filing_attachments','attachment_id',a.id,
   'event_id',NULL,'source_id',NULL,'book_id',NULL,'response_id',NULL,
   'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes))
  FROM accounting.tax_filing_attachments a JOIN accounting.tax_filings t
   ON t.id=a.filing_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id AND t.book_id=a.book_id
  WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 WHEN 'source_original' THEN RETURN (
  SELECT jsonb_build_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,
   'purpose','source_original','target_kind','source','target_id',a.source_id,
   'attachment_table','source_originals','attachment_id',a.id,
   'event_id',NULL,'source_id',a.source_id,'book_id',a.book_id,'response_id',NULL,
   'storage',jsonb_build_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes))
  FROM accounting.source_originals a
  WHERE a.storage_key=object_key AND a.created_by=uploader AND a.firm_id=firm);
 ELSE RETURN NULL;
 END CASE;
END $$;
REVOKE ALL ON FUNCTION accounting.upload_binding_expected(TEXT,TEXT,TEXT,TEXT) FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
