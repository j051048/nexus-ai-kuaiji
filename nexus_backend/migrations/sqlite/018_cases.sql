CREATE TABLE case_tasks(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT,book_id TEXT,source_id TEXT,draft_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('exception','supplement')),title TEXT NOT NULL,request_reason TEXT NOT NULL,
 required_fields TEXT NOT NULL DEFAULT '[]',attachment_required INTEGER NOT NULL DEFAULT 0 CHECK(attachment_required IN (0,1)),
 recipient_id TEXT,assignee_id TEXT,priority TEXT NOT NULL CHECK(priority IN ('normal','high','urgent')),due_on TEXT,
 state TEXT NOT NULL CHECK(state IN ('open','awaiting_info','response_submitted','in_review','closed')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),evidence_revision INTEGER NOT NULL DEFAULT 0 CHECK(evidence_revision>=0),
 accepted_fact_revision INTEGER NOT NULL DEFAULT 0 CHECK(accepted_fact_revision>=0),created_by TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(id,firm_id,company_id),CHECK(event_id IS NOT NULL OR (book_id IS NOT NULL AND source_id IS NOT NULL AND draft_id IS NOT NULL)),
 CHECK(kind<>'supplement' OR event_id IS NOT NULL),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(source_id,book_id) REFERENCES sources(id,book_id),FOREIGN KEY(draft_id,source_id,book_id) REFERENCES drafts(id,source_id,book_id),
 FOREIGN KEY(recipient_id) REFERENCES users(id),FOREIGN KEY(assignee_id) REFERENCES users(id),FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE TABLE case_responses(
 id TEXT PRIMARY KEY,case_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('draft','submitted','accepted','returned')),message TEXT NOT NULL DEFAULT '',fields TEXT NOT NULL DEFAULT '{}',
 created_by TEXT NOT NULL,created_at TEXT NOT NULL,submitted_at TEXT,reviewed_by TEXT,reviewed_at TEXT,review_reason TEXT NOT NULL DEFAULT '',supersedes_attachment_ids TEXT NOT NULL DEFAULT '[]',
 UNIQUE(id,case_id),FOREIGN KEY(case_id,firm_id,company_id) REFERENCES case_tasks(id,firm_id,company_id),
 FOREIGN KEY(created_by) REFERENCES users(id),FOREIGN KEY(reviewed_by) REFERENCES users(id)
);
CREATE UNIQUE INDEX idx_case_response_current ON case_responses(case_id) WHERE state IN ('draft','submitted');
CREATE TABLE case_attachments(
 id TEXT PRIMARY KEY,response_id TEXT NOT NULL,case_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,media_type TEXT NOT NULL,sha256 TEXT NOT NULL,size_bytes INTEGER NOT NULL CHECK(size_bytes>0 AND size_bytes<=10485760),
 created_by TEXT NOT NULL,created_at TEXT NOT NULL,FOREIGN KEY(response_id,case_id) REFERENCES case_responses(id,case_id),
 FOREIGN KEY(case_id,firm_id,company_id) REFERENCES case_tasks(id,firm_id,company_id),FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE TABLE case_history(
 id TEXT PRIMARY KEY,case_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,case_version INTEGER NOT NULL,
 action TEXT NOT NULL,audience TEXT NOT NULL CHECK(audience IN ('internal','requester')),reason TEXT NOT NULL,created_by TEXT NOT NULL,created_at TEXT NOT NULL,
 FOREIGN KEY(case_id,firm_id,company_id) REFERENCES case_tasks(id,firm_id,company_id),FOREIGN KEY(created_by) REFERENCES users(id)
);
CREATE TABLE case_commands(
 firm_id TEXT NOT NULL,user_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,case_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,
 result_version INTEGER NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(firm_id,user_id,idempotency_key),
 FOREIGN KEY(case_id) REFERENCES case_tasks(id),FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX idx_cases_page ON case_tasks(firm_id,state,priority,due_on,updated_at,id);
CREATE INDEX idx_cases_event ON case_tasks(event_id,state);
CREATE INDEX idx_cases_draft ON case_tasks(draft_id,state);
CREATE INDEX idx_cases_company ON case_tasks(firm_id,company_id,state,updated_at);
CREATE INDEX idx_case_history_scope ON case_history(case_id,audience,created_at,id);
CREATE TABLE case_supersessions(
 old_attachment_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,case_id TEXT NOT NULL,response_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 accepted_by TEXT NOT NULL,accepted_at TEXT NOT NULL,FOREIGN KEY(old_attachment_id,event_id) REFERENCES business_attachments(id,event_id),
 FOREIGN KEY(response_id,case_id) REFERENCES case_responses(id,case_id),FOREIGN KEY(case_id,firm_id,company_id) REFERENCES case_tasks(id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),FOREIGN KEY(accepted_by) REFERENCES users(id)
);
CREATE TRIGGER case_supersession_update BEFORE UPDATE ON case_supersessions BEGIN SELECT RAISE(ABORT,'supplement supersession is immutable'); END;
CREATE TRIGGER case_supersession_delete BEFORE DELETE ON case_supersessions BEGIN SELECT RAISE(ABORT,'supplement supersession is immutable'); END;
CREATE TRIGGER case_identity BEFORE UPDATE ON case_tasks WHEN
 (NEW.id,NEW.firm_id,NEW.company_id,NEW.event_id,NEW.book_id,NEW.source_id,NEW.draft_id,NEW.kind,NEW.recipient_id,NEW.created_by,NEW.created_at,NEW.title,NEW.request_reason,NEW.required_fields,NEW.attachment_required)
 IS NOT (OLD.id,OLD.firm_id,OLD.company_id,OLD.event_id,OLD.book_id,OLD.source_id,OLD.draft_id,OLD.kind,OLD.recipient_id,OLD.created_by,OLD.created_at,OLD.title,OLD.request_reason,OLD.required_fields,OLD.attachment_required)
 BEGIN SELECT RAISE(ABORT,'case identity is immutable'); END;
CREATE TRIGGER case_attachment_update BEFORE UPDATE ON case_attachments BEGIN SELECT RAISE(ABORT,'supplement attachment is immutable'); END;
CREATE TRIGGER case_attachment_delete BEFORE DELETE ON case_attachments BEGIN SELECT RAISE(ABORT,'supplement attachment is immutable'); END;
CREATE TRIGGER case_history_update BEFORE UPDATE ON case_history BEGIN SELECT RAISE(ABORT,'case history is immutable'); END;
CREATE TRIGGER case_history_delete BEFORE DELETE ON case_history BEGIN SELECT RAISE(ABORT,'case history is immutable'); END;
CREATE TRIGGER case_commands_update BEFORE UPDATE ON case_commands BEGIN SELECT RAISE(ABORT,'case receipt is immutable'); END;
CREATE TRIGGER case_commands_delete BEFORE DELETE ON case_commands BEGIN SELECT RAISE(ABORT,'case receipt is immutable'); END;
CREATE TRIGGER case_response_identity BEFORE UPDATE ON case_responses WHEN
 (NEW.id,NEW.case_id,NEW.firm_id,NEW.company_id,NEW.created_by,NEW.created_at) IS NOT (OLD.id,OLD.case_id,OLD.firm_id,OLD.company_id,OLD.created_by,OLD.created_at)
 OR OLD.state IN ('accepted','returned') OR (OLD.state='submitted' AND (NEW.message IS NOT OLD.message OR NEW.fields IS NOT OLD.fields OR NEW.submitted_at IS NOT OLD.submitted_at))
 BEGIN SELECT RAISE(ABORT,'submitted supplement facts are immutable'); END;
