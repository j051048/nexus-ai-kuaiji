-- Business originals and approval snapshots are separate from the ledger.
ALTER TABLE sources ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'bank' CHECK(source_kind IN ('bank','business'));
CREATE UNIQUE INDEX idx_books_business_scope ON books(id,firm_id,company_id);
CREATE UNIQUE INDEX idx_drafts_business_source ON drafts(id,source_id,book_id);

CREATE TABLE department_approvers (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,department_id TEXT NOT NULL,user_id TEXT NOT NULL,
 PRIMARY KEY(firm_id,department_id,user_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 FOREIGN KEY(department_id,company_id) REFERENCES departments(id,company_id),
 FOREIGN KEY(company_id,user_id) REFERENCES company_memberships(company_id,user_id) ON DELETE CASCADE
);
CREATE TABLE business_events (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,department_id TEXT,
 applicant_id TEXT NOT NULL REFERENCES users(id),
 template_id TEXT NOT NULL CHECK(template_id IN ('reimbursement','procurement_expense','daily_cashflow')),
 template_version INTEGER NOT NULL DEFAULT 1 CHECK(template_version>0),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','submitted','department_approved','finance_approved','handed_off','returned')),
 payload TEXT NOT NULL CHECK(json_valid(payload) AND json_type(payload)='object'),
 submitted_version INTEGER CHECK(submitted_version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,firm_id,company_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 FOREIGN KEY(department_id,company_id) REFERENCES departments(id,company_id)
);
CREATE TABLE business_versions (
 event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 event_version INTEGER NOT NULL CHECK(event_version>0),
 payload TEXT NOT NULL CHECK(json_valid(payload) AND json_type(payload)='object'),
 template_snapshot TEXT NOT NULL CHECK(json_valid(template_snapshot) AND json_type(template_snapshot)='object'),
 attachment_snapshot TEXT NOT NULL CHECK(json_valid(attachment_snapshot) AND json_type(attachment_snapshot)='array'),
 created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),
 PRIMARY KEY(event_id,event_version),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id)
);
CREATE TABLE business_attachments (
 id TEXT PRIMARY KEY,event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,media_type TEXT NOT NULL,
 sha256 TEXT NOT NULL,size_bytes BIGINT NOT NULL CHECK(size_bytes>0),
 created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),UNIQUE(id,event_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id)
);
CREATE TABLE business_actions (
 id TEXT PRIMARY KEY,event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 submitted_version INTEGER NOT NULL CHECK(submitted_version>0),
 stage TEXT NOT NULL CHECK(stage IN ('department','finance','handoff','return')),
 decision TEXT NOT NULL CHECK(decision IN ('approve','return','handoff')),reason TEXT NOT NULL DEFAULT '',
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(event_id,submitted_version) REFERENCES business_versions(event_id,event_version)
);
CREATE TABLE business_mappings (
 event_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
 source_id TEXT NOT NULL,draft_id TEXT NOT NULL,submitted_version INTEGER NOT NULL CHECK(submitted_version>0),
 idempotency_key TEXT NOT NULL,request_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,UNIQUE(firm_id,idempotency_key),
 UNIQUE(source_id),UNIQUE(draft_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(event_id,submitted_version) REFERENCES business_versions(event_id,event_version),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id),
 FOREIGN KEY(draft_id,source_id,book_id) REFERENCES drafts(id,source_id,book_id)
);
CREATE INDEX idx_business_event_company ON business_events(firm_id,company_id,status,updated_at);
CREATE INDEX idx_business_event_applicant ON business_events(firm_id,company_id,applicant_id);
CREATE INDEX idx_business_event_department ON business_events(firm_id,company_id,department_id,status);
CREATE INDEX idx_business_attachment_event ON business_attachments(event_id,created_at);
CREATE INDEX idx_business_action_event ON business_actions(event_id,created_at);
CREATE INDEX idx_business_mapping_book ON business_mappings(book_id,event_id);
CREATE INDEX idx_department_approver_user ON department_approvers(firm_id,user_id,company_id);

INSERT INTO accounts(book_id,code,name) SELECT id,'2241','其他应付款' FROM books WHERE true ON CONFLICT(book_id,code) DO NOTHING;

CREATE TRIGGER business_events_identity BEFORE UPDATE OF id,firm_id,company_id,department_id,applicant_id,template_id,template_version,created_at ON business_events
 WHEN NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id
 OR NEW.department_id IS NOT OLD.department_id OR NEW.applicant_id<>OLD.applicant_id
 OR NEW.template_id<>OLD.template_id OR NEW.template_version<>OLD.template_version OR NEW.created_at<>OLD.created_at
 BEGIN SELECT RAISE(ABORT,'business event identity is immutable'); END;
CREATE TRIGGER business_versions_no_update BEFORE UPDATE ON business_versions BEGIN SELECT RAISE(ABORT,'business version is immutable'); END;
CREATE TRIGGER business_versions_no_delete BEFORE DELETE ON business_versions BEGIN SELECT RAISE(ABORT,'business version is immutable'); END;
CREATE TRIGGER business_attachments_no_update BEFORE UPDATE ON business_attachments BEGIN SELECT RAISE(ABORT,'business attachment is immutable'); END;
CREATE TRIGGER business_attachments_no_delete BEFORE DELETE ON business_attachments BEGIN SELECT RAISE(ABORT,'business attachment is immutable'); END;
CREATE TRIGGER business_actions_no_update BEFORE UPDATE ON business_actions BEGIN SELECT RAISE(ABORT,'business approval is immutable'); END;
CREATE TRIGGER business_actions_no_delete BEFORE DELETE ON business_actions BEGIN SELECT RAISE(ABORT,'business approval is immutable'); END;
CREATE TRIGGER business_mappings_no_update BEFORE UPDATE ON business_mappings BEGIN SELECT RAISE(ABORT,'business mapping is immutable'); END;
CREATE TRIGGER business_mappings_no_delete BEFORE DELETE ON business_mappings BEGIN SELECT RAISE(ABORT,'business mapping is immutable'); END;
