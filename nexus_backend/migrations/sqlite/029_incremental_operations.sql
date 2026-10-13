CREATE TABLE ops_company_dirty(firm_id TEXT NOT NULL,company_id TEXT NOT NULL,dirty_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 1,PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id));
CREATE TABLE ops_company_refresh(firm_id TEXT NOT NULL,company_id TEXT NOT NULL,last_refreshed_at TEXT,next_due_at TEXT NOT NULL,PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id));
CREATE INDEX idx_ops_dirty_fair ON ops_company_dirty(dirty_at,firm_id,company_id);
CREATE INDEX idx_ops_refresh_due ON ops_company_refresh(next_due_at,firm_id,company_id);
INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM firm_clients WHERE status='active';
INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM firm_clients WHERE status='active';
CREATE TABLE human_work_sessions(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,user_id TEXT NOT NULL REFERENCES users(id),book_id TEXT,event_id TEXT,
 activity TEXT NOT NULL CHECK(activity IN ('review','correction','supplement','month_close','business_entry')),state TEXT NOT NULL CHECK(state IN ('active','paused','completed')),
 credited_seconds INTEGER NOT NULL CHECK(credited_seconds BETWEEN 0 AND 28800),started_at TEXT NOT NULL,last_active_at TEXT NOT NULL,updated_at TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),start_key TEXT NOT NULL,start_fingerprint TEXT NOT NULL CHECK(length(start_fingerprint)=64),
 UNIQUE(firm_id,user_id,start_key),UNIQUE(id,firm_id,user_id),CHECK((book_id IS NULL)<>(event_id IS NULL)),CHECK((event_id IS NOT NULL AND activity='business_entry') OR (book_id IS NOT NULL AND activity<>'business_entry')),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id)
);
CREATE UNIQUE INDEX idx_human_one_active ON human_work_sessions(firm_id,user_id) WHERE state='active';
CREATE INDEX idx_human_work_scope ON human_work_sessions(firm_id,book_id,user_id,started_at);
CREATE TABLE human_work_commands(firm_id TEXT NOT NULL,user_id TEXT NOT NULL,idempotency_key TEXT NOT NULL,session_id TEXT NOT NULL,request_fingerprint TEXT NOT NULL,response_json TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(firm_id,user_id,idempotency_key),FOREIGN KEY(session_id,firm_id,user_id) REFERENCES human_work_sessions(id,firm_id,user_id));
CREATE TRIGGER human_session_no_delete BEFORE DELETE ON human_work_sessions BEGIN SELECT RAISE(ABORT,'Work sessions are historical records'); END;
CREATE TRIGGER human_session_identity BEFORE UPDATE ON human_work_sessions WHEN (NEW.id,NEW.firm_id,NEW.company_id,NEW.user_id,NEW.book_id,NEW.event_id,NEW.activity,NEW.started_at,NEW.start_key,NEW.start_fingerprint) IS NOT (OLD.id,OLD.firm_id,OLD.company_id,OLD.user_id,OLD.book_id,OLD.event_id,OLD.activity,OLD.started_at,OLD.start_key,OLD.start_fingerprint) OR NEW.version<>OLD.version+1 OR NEW.credited_seconds<OLD.credited_seconds OR NEW.credited_seconds>OLD.credited_seconds+60 OR OLD.state='completed' BEGIN SELECT RAISE(ABORT,'Work session identity and bounded increments are protected'); END;
CREATE TRIGGER human_command_no_update BEFORE UPDATE ON human_work_commands BEGIN SELECT RAISE(ABORT,'Work commands are immutable'); END;
CREATE TRIGGER human_command_no_delete BEFORE DELETE ON human_work_commands BEGIN SELECT RAISE(ABORT,'Work commands are immutable'); END;
CREATE TRIGGER ops_dirty_firm_clients_insert AFTER INSERT ON firm_clients BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_firm_clients_update AFTER UPDATE ON firm_clients BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_model_company_policies_insert AFTER INSERT ON model_company_policies BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_model_company_policies_update AFTER UPDATE ON model_company_policies BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_model_costs_insert AFTER INSERT ON model_costs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_model_costs_update AFTER UPDATE ON model_costs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_ocr_jobs_insert AFTER INSERT ON ocr_jobs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_ocr_jobs_update AFTER UPDATE ON ocr_jobs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_proposal_jobs_insert AFTER INSERT ON proposal_jobs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_proposal_jobs_update AFTER UPDATE ON proposal_jobs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_ops_exception_snapshots_insert AFTER INSERT ON ops_exception_snapshots BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_ops_exception_snapshots_update AFTER UPDATE ON ops_exception_snapshots BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_case_tasks_insert AFTER INSERT ON case_tasks BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_case_tasks_update AFTER UPDATE ON case_tasks BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_delivery_receipts_insert AFTER INSERT ON delivery_receipts BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_delivery_receipts_update AFTER UPDATE ON delivery_receipts BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_drafts_insert AFTER INSERT ON drafts BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM books WHERE id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM books WHERE id=NEW.book_id ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_drafts_update AFTER UPDATE ON drafts BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM books WHERE id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM books WHERE id=NEW.book_id ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_periods_insert AFTER INSERT ON periods BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM books WHERE id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM books WHERE id=NEW.book_id ON CONFLICT(firm_id,company_id) DO NOTHING;
END;

CREATE TRIGGER ops_dirty_periods_update AFTER UPDATE ON periods BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM books WHERE id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT firm_id,company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') FROM books WHERE id=NEW.book_id ON CONFLICT(firm_id,company_id) DO NOTHING;
END;
