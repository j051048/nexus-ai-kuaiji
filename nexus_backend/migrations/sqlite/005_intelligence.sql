-- SQLite local verification mirrors structural integrity; access is enforced
-- by the application, while PostgreSQL additionally enforces RLS.
CREATE TABLE ai_company_settings (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
 updated_by TEXT NOT NULL REFERENCES users(id),updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
CREATE TABLE ai_runs (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('ocr','proposal')),attachment_id TEXT,draft_id TEXT,book_id TEXT,
 input_version INTEGER NOT NULL CHECK(input_version>0),input_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','stale')),
 result_json TEXT CHECK(result_json IS NULL OR (json_valid(result_json) AND json_type(result_json)='object')),
 provider TEXT NOT NULL,model TEXT NOT NULL,prompt_version TEXT NOT NULL,
 usage_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(usage_json) AND json_type(usage_json)='object'),
 error_code TEXT NOT NULL DEFAULT '',error_detail TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(firm_id,event_id,kind,idempotency_key),UNIQUE(id,event_id,firm_id,company_id),
 UNIQUE(id,event_id,firm_id,company_id,book_id,draft_id),
 CHECK((kind='ocr' AND attachment_id IS NOT NULL AND book_id IS NULL AND draft_id IS NULL)
  OR (kind='proposal' AND attachment_id IS NULL AND book_id IS NOT NULL AND draft_id IS NOT NULL)),
 CHECK((status='running' AND result_json IS NULL AND finished_at IS NULL)
  OR (status='succeeded' AND result_json IS NOT NULL AND finished_at IS NOT NULL)
  OR (status IN ('failed','stale') AND result_json IS NULL AND finished_at IS NOT NULL)),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(attachment_id,event_id) REFERENCES business_attachments(id,event_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id)
);
CREATE TABLE ai_findings (
 id TEXT PRIMARY KEY,run_id TEXT,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 code TEXT NOT NULL,severity TEXT NOT NULL CHECK(severity IN ('warning','blocking')),
 payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(event_id,code,fingerprint),UNIQUE(id,event_id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(run_id,event_id,firm_id,company_id) REFERENCES ai_runs(id,event_id,firm_id,company_id)
);
CREATE TABLE ai_finding_reviews (
 id TEXT PRIMARY KEY,finding_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 reason TEXT NOT NULL CHECK(length(trim(reason))>0),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(finding_id,event_id,firm_id,company_id) REFERENCES ai_findings(id,event_id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id)
);
CREATE UNIQUE INDEX idx_business_mapping_ai_scope ON business_mappings(event_id,firm_id,company_id,book_id,draft_id);
CREATE UNIQUE INDEX idx_business_mapping_ai_book_scope ON business_mappings(event_id,firm_id,company_id,book_id);
CREATE TABLE ai_proposal_applications (
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 book_id TEXT NOT NULL,draft_id TEXT NOT NULL,applied_draft_version INTEGER NOT NULL CHECK(applied_draft_version>0),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(run_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES ai_runs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(event_id,firm_id,company_id,book_id,draft_id) REFERENCES business_mappings(event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id)
);
CREATE TABLE ai_bank_links (
 bank_source_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,book_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(event_id,firm_id,company_id,book_id) REFERENCES business_mappings(event_id,firm_id,company_id,book_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(bank_source_id,book_id) REFERENCES sources(id,book_id)
);
CREATE INDEX idx_ai_runs_event ON ai_runs(event_id,kind,created_at);
CREATE INDEX idx_ai_runs_company ON ai_runs(firm_id,company_id,status,created_at);
CREATE INDEX idx_ai_findings_event ON ai_findings(event_id,created_at);
CREATE INDEX idx_ai_reviews_finding ON ai_finding_reviews(finding_id,created_at);
CREATE INDEX idx_ai_bank_links_event ON ai_bank_links(event_id,book_id);
CREATE TRIGGER ai_run_identity BEFORE UPDATE ON ai_runs
 WHEN OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','stale')
 OR NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id OR NEW.event_id IS NOT OLD.event_id
 OR NEW.kind IS NOT OLD.kind OR NEW.attachment_id IS NOT OLD.attachment_id OR NEW.draft_id IS NOT OLD.draft_id OR NEW.book_id IS NOT OLD.book_id
 OR NEW.input_version IS NOT OLD.input_version OR NEW.input_fingerprint IS NOT OLD.input_fingerprint OR NEW.idempotency_key IS NOT OLD.idempotency_key
 OR NEW.provider IS NOT OLD.provider OR NEW.model IS NOT OLD.model OR NEW.prompt_version IS NOT OLD.prompt_version OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
 BEGIN SELECT RAISE(ABORT,'AI run input and terminal output are immutable'); END;
CREATE TRIGGER ai_runs_no_delete BEFORE DELETE ON ai_runs BEGIN SELECT RAISE(ABORT,'AI run is immutable'); END;
CREATE TRIGGER ai_findings_no_update BEFORE UPDATE ON ai_findings BEGIN SELECT RAISE(ABORT,'AI finding is immutable'); END;
CREATE TRIGGER ai_findings_no_delete BEFORE DELETE ON ai_findings BEGIN SELECT RAISE(ABORT,'AI finding is immutable'); END;
CREATE TRIGGER ai_reviews_no_update BEFORE UPDATE ON ai_finding_reviews BEGIN SELECT RAISE(ABORT,'AI review is immutable'); END;
CREATE TRIGGER ai_reviews_no_delete BEFORE DELETE ON ai_finding_reviews BEGIN SELECT RAISE(ABORT,'AI review is immutable'); END;
CREATE TRIGGER ai_applications_no_update BEFORE UPDATE ON ai_proposal_applications BEGIN SELECT RAISE(ABORT,'AI application is immutable'); END;
CREATE TRIGGER ai_applications_no_delete BEFORE DELETE ON ai_proposal_applications BEGIN SELECT RAISE(ABORT,'AI application is immutable'); END;
CREATE TRIGGER ai_bank_links_no_update BEFORE UPDATE ON ai_bank_links BEGIN SELECT RAISE(ABORT,'AI bank link is immutable'); END;
CREATE TRIGGER ai_bank_link_insert BEFORE INSERT ON ai_bank_links
 WHEN NOT EXISTS(SELECT 1 FROM business_mappings m JOIN drafts d ON d.id=m.draft_id AND d.book_id=m.book_id WHERE m.event_id=NEW.event_id AND m.book_id=NEW.book_id AND d.status='draft')
 OR NOT EXISTS(SELECT 1 FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=NEW.bank_source_id AND s.book_id=NEW.book_id AND s.source_kind='bank' AND d.status='draft')
 OR NOT EXISTS(SELECT 1 FROM business_events WHERE id=NEW.event_id AND json_extract(payload,'$.payment_status')='paid')
 BEGIN SELECT RAISE(ABORT,'Bank link requires a paid event and unapproved bank/business drafts'); END;
CREATE TRIGGER ai_bank_link_delete BEFORE DELETE ON ai_bank_links
 WHEN NOT EXISTS(SELECT 1 FROM business_mappings m JOIN drafts d ON d.id=m.draft_id AND d.book_id=m.book_id WHERE m.event_id=OLD.event_id AND m.book_id=OLD.book_id AND d.status='draft')
 BEGIN SELECT RAISE(ABORT,'Bank unlink requires an unapproved business draft'); END;
