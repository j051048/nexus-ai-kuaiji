CREATE TABLE business_submission_policies(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,mode TEXT NOT NULL CHECK(mode IN('department','direct')),
 version INTEGER NOT NULL CHECK(version>0),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),
 updated_by TEXT NOT NULL REFERENCES users(id),updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
CREATE TRIGGER submission_policy_initial_version BEFORE INSERT ON business_submission_policies
 WHEN NEW.version<>coalesce((SELECT version FROM business_submission_policies WHERE firm_id=NEW.firm_id AND company_id=NEW.company_id),0)+1
 BEGIN SELECT RAISE(ABORT,'Submission policy starts at one and advances one version'); END;
CREATE TRIGGER submission_policy_scope BEFORE UPDATE ON business_submission_policies
 WHEN (NEW.firm_id,NEW.company_id) IS NOT (OLD.firm_id,OLD.company_id) OR NEW.version<>OLD.version+1
 BEGIN SELECT RAISE(ABORT,'Submission policy scope and consecutive version are protected'); END;
ALTER TABLE business_events ADD COLUMN submission_mode TEXT NOT NULL DEFAULT 'department' CHECK(submission_mode IN('department','direct'));
CREATE TRIGGER submission_channel_immutable BEFORE UPDATE ON business_events WHEN NEW.submission_mode IS NOT OLD.submission_mode
 BEGIN SELECT RAISE(ABORT,'Submission channel is frozen per event'); END;
CREATE TRIGGER direct_submission_snapshot_pointer BEFORE UPDATE ON business_events
 WHEN OLD.submission_mode='direct' AND OLD.status<>'handed_off' AND NEW.submitted_version IS NOT OLD.submitted_version
 AND NOT (OLD.status IN('draft','returned') AND NEW.status='submitted' AND NEW.submitted_version=NEW.version
  AND EXISTS(SELECT 1 FROM business_versions v WHERE v.event_id=NEW.id AND v.event_version=NEW.submitted_version AND v.payload=NEW.payload))
 BEGIN SELECT RAISE(ABORT,'Only resubmission changes the frozen snapshot pointer'); END;
CREATE TABLE intake_drafts_upgrade(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,department_id TEXT,created_by TEXT NOT NULL REFERENCES users(id),
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
INSERT INTO intake_drafts_upgrade SELECT * FROM intake_drafts;
PRAGMA legacy_alter_table=ON;
DROP TABLE intake_drafts;
ALTER TABLE intake_drafts_upgrade RENAME TO intake_drafts;
PRAGMA legacy_alter_table=OFF;
CREATE INDEX idx_intake_own ON intake_drafts(firm_id,created_by,status,updated_at DESC,id);
CREATE TRIGGER intake_draft_identity BEFORE UPDATE ON intake_drafts
 WHEN OLD.status<>'draft' OR (NEW.id,NEW.firm_id,NEW.company_id,NEW.department_id,NEW.created_by,NEW.create_key,NEW.create_fingerprint,NEW.created_at) IS NOT (OLD.id,OLD.firm_id,OLD.company_id,OLD.department_id,OLD.created_by,OLD.create_key,OLD.create_fingerprint,OLD.created_at) OR NEW.version<>OLD.version+1
 BEGIN SELECT RAISE(ABORT,'Intake identity and completed evidence are immutable'); END;
CREATE TRIGGER intake_complete_scope BEFORE UPDATE ON intake_drafts WHEN NEW.status='completed' AND (NOT EXISTS(
 SELECT 1 FROM business_events e WHERE e.id=NEW.event_id AND e.firm_id=NEW.firm_id AND e.company_id=NEW.company_id AND e.department_id IS NEW.department_id AND e.applicant_id=NEW.created_by AND e.template_id=NEW.template_id AND e.payload=NEW.fields_json AND e.status IN('draft','submitted')) OR EXISTS(SELECT 1 FROM intake_attachments a WHERE a.intake_id=NEW.id AND NOT EXISTS(SELECT 1 FROM business_attachments b WHERE b.id=a.id AND b.event_id=NEW.event_id AND b.storage_key=a.storage_key AND b.sha256=a.sha256 AND b.size_bytes=a.size_bytes)))
 BEGIN SELECT RAISE(ABORT,'Intake completion must reference matching confirmed business facts'); END;
CREATE TRIGGER intake_channel_scope BEFORE INSERT ON intake_drafts WHEN NEW.department_id IS NULL AND NOT EXISTS(
 SELECT 1 FROM business_submission_policies p JOIN company_memberships cm ON cm.company_id=p.company_id
 WHERE p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND p.mode='direct' AND cm.user_id=NEW.created_by AND cm.role IN('owner','admin','finance'))
 BEGIN SELECT RAISE(ABORT,'A department is required for the configured intake channel'); END;
