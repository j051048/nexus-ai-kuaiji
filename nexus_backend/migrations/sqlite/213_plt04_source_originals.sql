-- compatibility: incremental
-- PLT04-D：费用/租赁/规费等来源的原始文件必须有稳定绑定；不把 evidence_ref 字符串当文件。
CREATE TABLE IF NOT EXISTS source_originals(
 id TEXT PRIMARY KEY,
 source_id TEXT NOT NULL,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 book_id TEXT NOT NULL REFERENCES books(id),
 storage_key TEXT NOT NULL UNIQUE,
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 size_bytes INTEGER NOT NULL CHECK(size_bytes>0),
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL,
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id)
);
CREATE INDEX IF NOT EXISTS idx_source_originals_source
 ON source_originals(source_id,created_at);
CREATE TRIGGER IF NOT EXISTS source_originals_append_only
 BEFORE UPDATE ON source_originals
 BEGIN SELECT RAISE(ABORT,'source_originals is append-only'); END;
CREATE TRIGGER IF NOT EXISTS source_originals_no_delete
 BEFORE DELETE ON source_originals
 BEGIN SELECT RAISE(ABORT,'source_originals is append-only'); END;

-- SQLite cannot alter a CHECK constraint: rebuild upload_intents to admit the
-- source-original purpose while preserving rows, binding view and guards.
DROP VIEW IF EXISTS upload_binding_candidates;
CREATE TABLE upload_intents_next(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL REFERENCES firms(id),user_id TEXT NOT NULL REFERENCES users(id),
 purpose TEXT NOT NULL CHECK(purpose IN('intake_attachment','event_attachment','case_attachment','material_attachment','bank_import','invoice_import','tax_filing_attachment','source_original')),
 filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL CHECK(length(media_type) BETWEEN 1 AND 120),
 size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),
 storage_key TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN('pending','stored','consuming','consumed','discarded')),
 created_at TEXT NOT NULL,expires_at TEXT NOT NULL,consumed_ref TEXT,binding_json TEXT
);
INSERT INTO upload_intents_next(id,firm_id,user_id,purpose,filename,media_type,size_bytes,sha256,storage_key,state,created_at,expires_at,consumed_ref,binding_json)
 SELECT id,firm_id,user_id,purpose,filename,media_type,size_bytes,sha256,storage_key,state,created_at,expires_at,consumed_ref,binding_json FROM upload_intents;
DROP TABLE upload_intents;
ALTER TABLE upload_intents_next RENAME TO upload_intents;
CREATE INDEX idx_upload_intents_owner ON upload_intents(firm_id,user_id,state,created_at);

CREATE VIEW upload_binding_candidates AS
SELECT 'intake_attachment' AS purpose,a.storage_key,a.created_by,json_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','intake_attachment','target_kind','intake_draft','target_id',a.intake_id,'attachment_table','intake_attachments','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',NULL,'response_id',NULL,'storage',json_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) AS binding_json FROM intake_attachments a JOIN intake_drafts t ON t.id=a.intake_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id
UNION ALL
SELECT 'event_attachment',a.storage_key,a.created_by,json_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','event_attachment','target_kind','business_event','target_id',a.event_id,'attachment_table','business_attachments','attachment_id',a.id,'event_id',t.id,'source_id',NULL,'book_id',NULL,'response_id',NULL,'storage',json_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM business_attachments a JOIN business_events t ON t.id=a.event_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id
UNION ALL
SELECT 'case_attachment',a.storage_key,a.created_by,json_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','case_attachment','target_kind','case_task','target_id',a.case_id,'attachment_table','case_attachments','attachment_id',a.id,'event_id',t.event_id,'source_id',t.source_id,'book_id',t.book_id,'response_id',a.response_id,'storage',json_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM case_attachments a JOIN case_tasks t ON t.id=a.case_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id
UNION ALL
SELECT 'material_attachment',a.storage_key,a.created_by,json_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','material_attachment','target_kind','material_request','target_id',a.request_id,'attachment_table','material_attachments','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.book_id,'response_id',NULL,'storage',json_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM material_attachments a JOIN material_requests t ON t.id=a.request_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id
UNION ALL
SELECT 'bank_import',a.storage_key,a.created_by,json_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','bank_import','target_kind','book','target_id',a.book_id,'attachment_table','bank_import_files','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.id,'response_id',NULL,'storage',json_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM bank_import_files a JOIN books t ON t.id=a.book_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id
UNION ALL
SELECT 'invoice_import',a.storage_key,a.created_by,json_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','invoice_import','target_kind','book','target_id',a.book_id,'attachment_table','invoice_import_batches','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',t.id,'response_id',NULL,'storage',json_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM invoice_import_batches a JOIN books t ON t.id=a.book_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id
UNION ALL
SELECT 'tax_filing_attachment',a.storage_key,a.created_by,json_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','tax_filing_attachment','target_kind','tax_filing','target_id',a.filing_id,'attachment_table','tax_filing_attachments','attachment_id',a.id,'event_id',NULL,'source_id',NULL,'book_id',NULL,'response_id',NULL,'storage',json_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM tax_filing_attachments a JOIN tax_filings t ON t.id=a.filing_id AND t.firm_id=a.firm_id AND t.company_id=a.company_id
UNION ALL
SELECT 'source_original',a.storage_key,a.created_by,json_object('version',1,'firm_id',a.firm_id,'company_id',a.company_id,'purpose','source_original','target_kind','source','target_id',a.source_id,'attachment_table','source_originals','attachment_id',a.id,'event_id',NULL,'source_id',a.source_id,'book_id',t.book_id,'response_id',NULL,'storage',json_object('storage_key',a.storage_key,'sha256',a.sha256,'size_bytes',a.size_bytes)) FROM source_originals a JOIN sources t ON t.id=a.source_id AND t.book_id=a.book_id;

CREATE TRIGGER upload_binding_insert BEFORE INSERT ON upload_intents
WHEN NEW.binding_json IS NOT NULL OR NEW.consumed_ref IS NOT NULL OR NEW.state<>'pending'
BEGIN SELECT RAISE(ABORT,'Upload binding cannot be supplied on creation'); END;
CREATE TRIGGER upload_binding_immutable BEFORE UPDATE ON upload_intents
WHEN OLD.state='consumed' AND (NEW.state IS NOT OLD.state OR NEW.binding_json IS NOT OLD.binding_json OR NEW.consumed_ref IS NOT OLD.consumed_ref)
BEGIN SELECT RAISE(ABORT,'Consumed upload binding is immutable, including legacy rows'); END;
CREATE TRIGGER upload_binding_unconsumed BEFORE UPDATE ON upload_intents
WHEN NEW.state<>'consumed' AND (NEW.binding_json IS NOT NULL OR NEW.consumed_ref IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'Unconsumed upload cannot have a binding'); END;
CREATE TRIGGER upload_binding_validate BEFORE UPDATE ON upload_intents
WHEN NEW.state='consumed' AND OLD.state<>'consumed'
BEGIN
 SELECT CASE WHEN OLD.state<>'consuming' OR NEW.binding_json IS NULL OR NOT json_valid(NEW.binding_json)
  THEN RAISE(ABORT,'Consume original atomically with a valid binding') END;
 SELECT CASE WHEN NOT EXISTS(
  SELECT 1 FROM upload_binding_candidates c
  WHERE c.purpose=NEW.purpose AND c.storage_key=NEW.storage_key AND c.created_by=NEW.user_id
   AND json_extract(c.binding_json,'$.firm_id')=NEW.firm_id
   AND json_extract(c.binding_json,'$.storage.sha256')=NEW.sha256
   AND json_extract(c.binding_json,'$.storage.size_bytes')=NEW.size_bytes
   AND NEW.consumed_ref=json_extract(c.binding_json,'$.attachment_table')||':'||json_extract(c.binding_json,'$.attachment_id')
   AND NOT EXISTS(SELECT fullkey,CASE WHEN type IN ('object','array') THEN NULL ELSE value END,type FROM json_tree(c.binding_json) EXCEPT SELECT fullkey,CASE WHEN type IN ('object','array') THEN NULL ELSE value END,type FROM json_tree(NEW.binding_json))
   AND NOT EXISTS(SELECT fullkey,CASE WHEN type IN ('object','array') THEN NULL ELSE value END,type FROM json_tree(NEW.binding_json) EXCEPT SELECT fullkey,CASE WHEN type IN ('object','array') THEN NULL ELSE value END,type FROM json_tree(c.binding_json))
 ) THEN RAISE(ABORT,'Upload binding must match the actual company, parent and original bytes') END;
END;
CREATE TRIGGER upload_identity_immutable BEFORE UPDATE ON upload_intents
WHEN NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.user_id IS NOT OLD.user_id
 OR NEW.purpose IS NOT OLD.purpose OR NEW.storage_key IS NOT OLD.storage_key OR NEW.sha256 IS NOT OLD.sha256
 OR NEW.size_bytes IS NOT OLD.size_bytes OR NEW.filename IS NOT OLD.filename OR NEW.media_type IS NOT OLD.media_type
BEGIN SELECT RAISE(ABORT,'Upload intent identity is immutable'); END;
