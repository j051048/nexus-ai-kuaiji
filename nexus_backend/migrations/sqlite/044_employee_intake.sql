-- Incomplete private employee drafts never enter accounting facts.
CREATE TABLE intake_drafts(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,department_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),
 template_id TEXT NOT NULL CHECK(template_id IN ('reimbursement','procurement_expense','daily_cashflow')),
 fields_json TEXT NOT NULL CHECK(json_valid(fields_json) AND json_type(fields_json)='object'),
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','completed')),version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 create_key TEXT NOT NULL,create_fingerprint TEXT NOT NULL CHECK(length(create_fingerprint)=64),
 event_id TEXT,completion_key TEXT,completion_fingerprint TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,create_key),UNIQUE(id,firm_id,company_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),FOREIGN KEY(department_id,company_id) REFERENCES departments(id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 CHECK((status='draft' AND event_id IS NULL AND completion_key IS NULL AND completion_fingerprint IS NULL) OR (status='completed' AND event_id IS NOT NULL AND completion_key IS NOT NULL AND completion_fingerprint IS NOT NULL AND length(completion_fingerprint)=64))
);
CREATE INDEX idx_intake_own ON intake_drafts(firm_id,created_by,status,updated_at DESC,id);
CREATE TABLE intake_attachments(
 id TEXT PRIMARY KEY,intake_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL,filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL CHECK(media_type IN ('application/pdf','image/png','image/jpeg','image/webp')),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),
 UNIQUE(intake_id,sha256),UNIQUE(id,intake_id),FOREIGN KEY(intake_id,firm_id,company_id) REFERENCES intake_drafts(id,firm_id,company_id)
);
CREATE TABLE intake_runs(
 id TEXT PRIMARY KEY,intake_id TEXT NOT NULL,attachment_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),
 input_fingerprint TEXT NOT NULL CHECK(length(input_fingerprint)=64),idempotency_key TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed','stale')),result_json TEXT CHECK(result_json IS NULL OR (json_valid(result_json) AND json_type(result_json)='object')),
 provider TEXT NOT NULL,model TEXT NOT NULL,prompt_version TEXT NOT NULL,usage_json TEXT NOT NULL DEFAULT '{}',
 error_code TEXT NOT NULL DEFAULT '',error_detail TEXT NOT NULL DEFAULT '',checks_json TEXT NOT NULL DEFAULT '[]',created_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(intake_id,idempotency_key),UNIQUE(id,firm_id,company_id,created_by),
 FOREIGN KEY(intake_id,firm_id,company_id) REFERENCES intake_drafts(id,firm_id,company_id),FOREIGN KEY(attachment_id,intake_id) REFERENCES intake_attachments(id,intake_id),
 CHECK((status='running' AND result_json IS NULL AND finished_at IS NULL) OR (status='succeeded' AND result_json IS NOT NULL AND finished_at IS NOT NULL) OR (status IN ('failed','stale') AND result_json IS NULL AND finished_at IS NOT NULL))
);
CREATE INDEX idx_intake_runs_attachment ON intake_runs(intake_id,attachment_id,created_at DESC,id DESC);
CREATE TRIGGER intake_draft_identity BEFORE UPDATE ON intake_drafts
 WHEN OLD.status<>'draft' OR (NEW.id,NEW.firm_id,NEW.company_id,NEW.department_id,NEW.created_by,NEW.create_key,NEW.create_fingerprint,NEW.created_at) IS NOT (OLD.id,OLD.firm_id,OLD.company_id,OLD.department_id,OLD.created_by,OLD.create_key,OLD.create_fingerprint,OLD.created_at) OR NEW.version<>OLD.version+1
 BEGIN SELECT RAISE(ABORT,'Intake identity and completed evidence are immutable'); END;
CREATE TRIGGER intake_complete_scope BEFORE UPDATE ON intake_drafts WHEN NEW.status='completed' AND (NOT EXISTS(
 SELECT 1 FROM business_events e WHERE e.id=NEW.event_id AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.department_id=NEW.department_id AND e.applicant_id=NEW.created_by AND e.template_id=NEW.template_id AND e.payload=NEW.fields_json AND e.status IN ('draft','submitted')) OR EXISTS(SELECT 1 FROM intake_attachments a WHERE a.intake_id=NEW.id AND NOT EXISTS(SELECT 1 FROM business_attachments b WHERE b.id=a.id AND b.event_id=NEW.event_id AND b.storage_key=a.storage_key AND b.sha256=a.sha256 AND b.size_bytes=a.size_bytes)))
 BEGIN SELECT RAISE(ABORT,'Intake completion must reference matching confirmed business facts'); END;
CREATE TRIGGER intake_attachments_no_update BEFORE UPDATE ON intake_attachments BEGIN SELECT RAISE(ABORT,'Intake originals are immutable'); END;
CREATE TRIGGER intake_attachments_no_delete BEFORE DELETE ON intake_attachments BEGIN SELECT RAISE(ABORT,'Intake originals are immutable'); END;
CREATE TRIGGER intake_attachments_open BEFORE INSERT ON intake_attachments WHEN NOT EXISTS(SELECT 1 FROM intake_drafts d WHERE d.id=NEW.intake_id AND d.status='draft' AND d.created_by=NEW.created_by) OR (SELECT count(*) FROM intake_attachments WHERE intake_id=NEW.intake_id)>=10 OR (SELECT coalesce(sum(size_bytes),0) FROM intake_attachments WHERE intake_id=NEW.intake_id)+NEW.size_bytes>52428800
 BEGIN SELECT RAISE(ABORT,'Intake attachment scope or limits invalid'); END;
CREATE TRIGGER intake_runs_identity BEFORE UPDATE ON intake_runs WHEN OLD.status<>'running' OR NEW.status NOT IN ('succeeded','failed','stale') OR (NEW.id,NEW.intake_id,NEW.attachment_id,NEW.firm_id,NEW.company_id,NEW.created_by,NEW.input_fingerprint,NEW.idempotency_key,NEW.provider,NEW.model,NEW.prompt_version,NEW.created_at) IS NOT (OLD.id,OLD.intake_id,OLD.attachment_id,OLD.firm_id,OLD.company_id,OLD.created_by,OLD.input_fingerprint,OLD.idempotency_key,OLD.provider,OLD.model,OLD.prompt_version,OLD.created_at)
 BEGIN SELECT RAISE(ABORT,'Intake model input and terminal output are immutable'); END;
CREATE TRIGGER intake_runs_no_delete BEFORE DELETE ON intake_runs BEGIN SELECT RAISE(ABORT,'Intake model history is immutable'); END;
CREATE TABLE model_costs_intake_upgrade (
 id TEXT PRIMARY KEY,run_id TEXT UNIQUE CHECK(id=run_id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('ocr','proposal')),created_by TEXT NOT NULL REFERENCES users(id),
 period TEXT NOT NULL CHECK(length(period)=7 AND substr(period,5,1)='-' AND substr(period,1,4) GLOB '[0-9][0-9][0-9][0-9]' AND CAST(substr(period,6,2) AS INTEGER) BETWEEN 1 AND 12),provider TEXT NOT NULL,model TEXT NOT NULL,charge_token TEXT NOT NULL,
 pricing_json TEXT NOT NULL CHECK(json_valid(pricing_json) AND json_type(pricing_json)='object'),quota_enabled INTEGER NOT NULL CHECK(quota_enabled IN (0,1)),
 reserved_micro INTEGER NOT NULL CHECK(reserved_micro BETWEEN 0 AND 999999999999990000),
 estimated_micro INTEGER CHECK(estimated_micro BETWEEN 0 AND 999999999999990000),confirmed_micro INTEGER CHECK(confirmed_micro BETWEEN 0 AND 999999999999990000),
 input_tokens INTEGER CHECK(input_tokens BETWEEN 0 AND 10000000),output_tokens INTEGER CHECK(output_tokens BETWEEN 0 AND 10000000),
 state TEXT NOT NULL CHECK(state IN ('reserved','sent','estimated','uncertain','released','reconciled')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_at TEXT NOT NULL,sent_at TEXT,finished_at TEXT,
 overrun INTEGER NOT NULL DEFAULT 0 CHECK(overrun IN (0,1)),manual_reason TEXT NOT NULL DEFAULT '',manual_by TEXT REFERENCES users(id),
 intake_run_id TEXT UNIQUE,
 FOREIGN KEY(run_id,event_id,firm_id,company_id,kind,created_by) REFERENCES ai_runs(id,event_id,firm_id,company_id,kind,created_by),
 UNIQUE(id,event_id,firm_id,company_id),
 CHECK((state IN ('reserved','sent') AND finished_at IS NULL) OR (state NOT IN ('reserved','sent') AND finished_at IS NOT NULL)),
 CHECK(state<>'estimated' OR estimated_micro IS NOT NULL),
 CHECK(state<>'reconciled' OR (confirmed_micro IS NOT NULL AND manual_by IS NOT NULL AND length(trim(manual_reason))>0))
 ,
 FOREIGN KEY(intake_run_id,firm_id,company_id,created_by) REFERENCES intake_runs(id,firm_id,company_id,created_by),
 CHECK((intake_run_id IS NULL AND run_id IS NOT NULL AND event_id IS NOT NULL) OR (intake_run_id IS NOT NULL AND intake_run_id=id AND run_id IS NULL AND event_id IS NULL AND kind='ocr'))
);
INSERT INTO model_costs_intake_upgrade(id,run_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,estimated_micro,confirmed_micro,input_tokens,output_tokens,state,version,created_at,sent_at,finished_at,overrun,manual_reason,manual_by) SELECT id,run_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,estimated_micro,confirmed_micro,input_tokens,output_tokens,state,version,created_at,sent_at,finished_at,overrun,manual_reason,manual_by FROM model_costs;
PRAGMA legacy_alter_table=ON;
DROP TABLE model_costs;
ALTER TABLE model_costs_intake_upgrade RENAME TO model_costs;
PRAGMA legacy_alter_table=OFF;
CREATE INDEX idx_model_costs_month ON model_costs(firm_id,company_id,period,state);
CREATE TRIGGER model_cost_identity BEFORE UPDATE ON model_costs
 WHEN NEW.intake_run_id IS NOT OLD.intake_run_id OR NEW.id IS NOT OLD.id OR NEW.run_id IS NOT OLD.run_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.event_id IS NOT OLD.event_id OR NEW.kind IS NOT OLD.kind OR NEW.created_by IS NOT OLD.created_by OR NEW.period IS NOT OLD.period
 OR NEW.provider IS NOT OLD.provider OR NEW.model IS NOT OLD.model OR NEW.charge_token IS NOT OLD.charge_token OR NEW.pricing_json IS NOT OLD.pricing_json
 OR NEW.quota_enabled IS NOT OLD.quota_enabled OR NEW.reserved_micro IS NOT OLD.reserved_micro OR NEW.created_at IS NOT OLD.created_at
 OR NEW.version<>OLD.version+1 OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS NOT OLD.sent_at)
 OR (OLD.state IN ('estimated','released','reconciled') AND NEW.state<>'reconciled')
 OR (OLD.state='uncertain' AND NOT (NEW.state='reconciled' OR (OLD.estimated_micro IS NULL AND OLD.input_tokens IS NULL AND OLD.output_tokens IS NULL AND NEW.state IN ('estimated','released','uncertain'))))
 OR (OLD.state IN ('estimated','released','reconciled') AND (NEW.estimated_micro IS NOT OLD.estimated_micro OR NEW.input_tokens IS NOT OLD.input_tokens OR NEW.output_tokens IS NOT OLD.output_tokens))
 OR (OLD.state='uncertain' AND (OLD.estimated_micro IS NOT NULL OR OLD.input_tokens IS NOT NULL OR OLD.output_tokens IS NOT NULL) AND (NEW.estimated_micro IS NOT OLD.estimated_micro OR NEW.input_tokens IS NOT OLD.input_tokens OR NEW.output_tokens IS NOT OLD.output_tokens))
 BEGIN SELECT RAISE(ABORT,'Model cost evidence and provenance are immutable'); END;
CREATE TRIGGER model_costs_no_delete BEFORE DELETE ON model_costs BEGIN SELECT RAISE(ABORT,'Model cost evidence is immutable'); END;
CREATE TRIGGER ops_dirty_model_costs_insert AFTER INSERT ON model_costs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;
CREATE TRIGGER ops_dirty_model_costs_update AFTER UPDATE ON model_costs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;
