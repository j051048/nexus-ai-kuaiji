-- Preserve all historical job scopes while replacing only the current mapping.

CREATE TABLE business_mapping_versions (
 event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,
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
, PRIMARY KEY(draft_id), UNIQUE(event_id,firm_id,company_id,book_id,draft_id));

INSERT INTO business_mapping_versions SELECT * FROM business_mappings;

CREATE TABLE revision_copy_ai_proposal_applications AS SELECT id,run_id,firm_id,company_id,event_id,book_id,draft_id,applied_draft_version,created_by,created_at FROM ai_proposal_applications;

DROP TABLE ai_proposal_applications;

CREATE TABLE ai_proposal_applications (
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 book_id TEXT NOT NULL,draft_id TEXT NOT NULL,applied_draft_version INTEGER NOT NULL CHECK(applied_draft_version>0),
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(run_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES ai_runs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(event_id,firm_id,company_id,book_id,draft_id) REFERENCES business_mapping_versions(event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id)
);

INSERT INTO ai_proposal_applications(id,run_id,firm_id,company_id,event_id,book_id,draft_id,applied_draft_version,created_by,created_at) SELECT id,run_id,firm_id,company_id,event_id,book_id,draft_id,applied_draft_version,created_by,created_at FROM revision_copy_ai_proposal_applications;

DROP TABLE revision_copy_ai_proposal_applications;

CREATE TRIGGER ai_applications_no_delete BEFORE DELETE ON ai_proposal_applications BEGIN SELECT RAISE(ABORT,'AI application is immutable'); END;

CREATE TRIGGER ai_applications_no_update BEFORE UPDATE ON ai_proposal_applications BEGIN SELECT RAISE(ABORT,'AI application is immutable'); END;

CREATE TABLE revision_copy_ai_bank_links AS SELECT bank_source_id,firm_id,company_id,event_id,book_id,created_by,created_at FROM ai_bank_links;

DROP TABLE ai_bank_links;

CREATE TABLE ai_bank_links (
 bank_source_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,book_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(bank_source_id,book_id) REFERENCES sources(id,book_id)
);

INSERT INTO ai_bank_links(bank_source_id,firm_id,company_id,event_id,book_id,created_by,created_at) SELECT bank_source_id,firm_id,company_id,event_id,book_id,created_by,created_at FROM revision_copy_ai_bank_links;

DROP TABLE revision_copy_ai_bank_links;

CREATE INDEX idx_ai_bank_links_event ON ai_bank_links(event_id,book_id);

CREATE TRIGGER ai_bank_link_delete BEFORE DELETE ON ai_bank_links
 WHEN NOT EXISTS(SELECT 1 FROM business_mappings m JOIN drafts d ON d.id=m.draft_id AND d.book_id=m.book_id WHERE m.event_id=OLD.event_id AND m.book_id=OLD.book_id AND d.status='draft')
 BEGIN SELECT RAISE(ABORT,'Bank unlink requires an unapproved business draft'); END;

CREATE TRIGGER ai_bank_link_insert BEFORE INSERT ON ai_bank_links
 WHEN NOT EXISTS(SELECT 1 FROM business_mappings m JOIN drafts d ON d.id=m.draft_id AND d.book_id=m.book_id WHERE m.event_id=NEW.event_id AND m.book_id=NEW.book_id AND d.status='draft')
 OR NOT EXISTS(SELECT 1 FROM sources s JOIN drafts d ON d.source_id=s.id AND d.book_id=s.book_id WHERE s.id=NEW.bank_source_id AND s.book_id=NEW.book_id AND s.source_kind='bank' AND d.status='draft')
 OR NOT EXISTS(SELECT 1 FROM business_events WHERE id=NEW.event_id AND json_extract(payload,'$.payment_status')='paid')
 BEGIN SELECT RAISE(ABORT,'Bank link requires a paid event and unapproved bank/business drafts'); END;

CREATE TRIGGER ai_bank_links_no_update BEFORE UPDATE ON ai_bank_links BEGIN SELECT RAISE(ABORT,'AI bank link is immutable'); END;

CREATE TABLE revision_copy_proposal_jobs AS SELECT id,firm_id,company_id,event_id,book_id,draft_id,expected_fact_version,expected_draft_version,configuration_fingerprint,policy_fingerprint,idempotency_key,execution_user_id,created_by,created_at,generation,status,attempt_count,max_attempts,available_at,dependency_deadline,lease_token,lease_until,worker_id,current_run_id,result_template_id,result_template_version,result_draft_version,error_code,error_detail,updated_at,finished_at,result_rule_id,result_rule_version FROM proposal_jobs;

DROP TABLE proposal_jobs;

CREATE TABLE proposal_jobs (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 book_id TEXT NOT NULL,draft_id TEXT NOT NULL,expected_fact_version INTEGER NOT NULL CHECK(expected_fact_version>0),
 expected_draft_version INTEGER NOT NULL CHECK(expected_draft_version>0),configuration_fingerprint TEXT NOT NULL,
 policy_fingerprint TEXT NOT NULL,idempotency_key TEXT NOT NULL,
 execution_user_id TEXT NOT NULL REFERENCES users(id),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>0),
 status TEXT NOT NULL CHECK(status IN ('waiting','queued','running','retry_wait','succeeded','needs_attention','failed','interrupted','stale','cancelled')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 30 AND attempt_count<=max_attempts),
 available_at TEXT NOT NULL,dependency_deadline TEXT NOT NULL,lease_token TEXT,lease_until TEXT,worker_id TEXT,current_run_id TEXT,
 result_template_id TEXT,result_template_version INTEGER,result_draft_version INTEGER,
 error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120),error_detail TEXT NOT NULL DEFAULT '' CHECK(length(error_detail)<=1000),
 updated_at TEXT NOT NULL,finished_at TEXT, result_rule_id TEXT, result_rule_version INTEGER,
 UNIQUE(firm_id,idempotency_key),UNIQUE(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(event_id,firm_id,company_id,book_id,draft_id) REFERENCES business_mapping_versions(event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(current_run_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES ai_runs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(result_template_id,result_template_version) REFERENCES journal_template_versions(template_id,version),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL)
  OR (status IN ('waiting','queued','retry_wait') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL)
  OR (status IN ('succeeded','needs_attention','failed','interrupted','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL))
);

INSERT INTO proposal_jobs(id,firm_id,company_id,event_id,book_id,draft_id,expected_fact_version,expected_draft_version,configuration_fingerprint,policy_fingerprint,idempotency_key,execution_user_id,created_by,created_at,generation,status,attempt_count,max_attempts,available_at,dependency_deadline,lease_token,lease_until,worker_id,current_run_id,result_template_id,result_template_version,result_draft_version,error_code,error_detail,updated_at,finished_at,result_rule_id,result_rule_version) SELECT id,firm_id,company_id,event_id,book_id,draft_id,expected_fact_version,expected_draft_version,configuration_fingerprint,policy_fingerprint,idempotency_key,execution_user_id,created_by,created_at,generation,status,attempt_count,max_attempts,available_at,dependency_deadline,lease_token,lease_until,worker_id,current_run_id,result_template_id,result_template_version,result_draft_version,error_code,error_detail,updated_at,finished_at,result_rule_id,result_rule_version FROM revision_copy_proposal_jobs;

DROP TABLE revision_copy_proposal_jobs;

CREATE UNIQUE INDEX idx_proposal_active_draft ON proposal_jobs(draft_id) WHERE status IN ('waiting','queued','running','retry_wait');

CREATE INDEX idx_proposal_book_page ON proposal_jobs(firm_id,book_id,created_at,id);

CREATE INDEX idx_proposal_dispatch ON proposal_jobs(status,available_at,firm_id,company_id,created_at,id);

CREATE INDEX idx_stage7_proposal_monitor ON proposal_jobs(firm_id,company_id,status,finished_at);

CREATE TRIGGER proposal_job_identity BEFORE UPDATE ON proposal_jobs
 WHEN NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id OR NEW.event_id IS NOT OLD.event_id
 OR NEW.book_id IS NOT OLD.book_id OR NEW.draft_id IS NOT OLD.draft_id OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at OR NEW.idempotency_key IS NOT OLD.idempotency_key
 BEGIN SELECT RAISE(ABORT,'Proposal job scope and provenance are immutable'); END;

CREATE TRIGGER proposal_jobs_no_delete BEFORE DELETE ON proposal_jobs BEGIN SELECT RAISE(ABORT,'Proposal job history is immutable'); END;

CREATE TRIGGER proposal_rule_receipt_guard_insert BEFORE INSERT ON proposal_jobs
 WHEN (NEW.result_rule_id IS NULL)<>(NEW.result_rule_version IS NULL)
 OR (NEW.result_rule_id IS NOT NULL AND (NEW.result_template_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM learning_rule_versions v JOIN learning_rules r ON r.id=v.rule_id AND r.book_id=v.book_id WHERE v.rule_id=NEW.result_rule_id AND v.version=NEW.result_rule_version AND v.book_id=NEW.book_id AND r.firm_id=NEW.firm_id AND r.company_id=NEW.company_id)))
 BEGIN SELECT RAISE(ABORT,'Company rule receipt must reference its scoped immutable version'); END;

CREATE TRIGGER proposal_rule_receipt_guard_update BEFORE UPDATE ON proposal_jobs
 WHEN (NEW.result_rule_id IS NULL)<>(NEW.result_rule_version IS NULL)
 OR (NEW.result_rule_id IS NOT NULL AND (NEW.result_template_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM learning_rule_versions v JOIN learning_rules r ON r.id=v.rule_id AND r.book_id=v.book_id WHERE v.rule_id=NEW.result_rule_id AND v.version=NEW.result_rule_version AND v.book_id=NEW.book_id AND r.firm_id=NEW.firm_id AND r.company_id=NEW.company_id)))
 BEGIN SELECT RAISE(ABORT,'Company rule receipt must reference its scoped immutable version'); END;

CREATE TRIGGER mapping_version_immutable_update BEFORE UPDATE ON business_mapping_versions BEGIN SELECT RAISE(ABORT,'mapping version is immutable'); END;

CREATE TRIGGER mapping_version_immutable_delete BEFORE DELETE ON business_mapping_versions BEGIN SELECT RAISE(ABORT,'mapping version is immutable'); END;

CREATE TRIGGER mapping_version_capture AFTER INSERT ON business_mappings BEGIN INSERT INTO business_mapping_versions VALUES(NEW.event_id,NEW.firm_id,NEW.company_id,NEW.book_id,NEW.source_id,NEW.draft_id,NEW.submitted_version,NEW.idempotency_key,NEW.request_fingerprint,NEW.created_by,NEW.created_at); END;

CREATE TABLE fact_revisions(
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 old_book_id TEXT NOT NULL,old_source_id TEXT NOT NULL,old_draft_id TEXT NOT NULL,target_book_id TEXT NOT NULL,
 expected_event_version INTEGER NOT NULL CHECK(expected_event_version>0),expected_fact_version INTEGER NOT NULL CHECK(expected_fact_version>0),
 expected_draft_version INTEGER NOT NULL CHECK(expected_draft_version>0),old_snapshot TEXT NOT NULL CHECK(json_type(old_snapshot)='object'),
 proposed_payload TEXT NOT NULL CHECK(json_type(proposed_payload)='object'),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),
 proposed_by TEXT NOT NULL REFERENCES users(id),idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),
 request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),state TEXT NOT NULL CHECK(state IN('pending','rejected','replaced','adjustment_proposed')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_at TEXT NOT NULL,
 UNIQUE(firm_id,proposed_by,idempotency_key),UNIQUE(id,firm_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(old_book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(target_book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id),
 FOREIGN KEY(old_draft_id,old_source_id,old_book_id) REFERENCES drafts(id,source_id,book_id)
);
CREATE UNIQUE INDEX idx_revision_pending ON fact_revisions(event_id) WHERE state='pending';
CREATE INDEX idx_revision_page ON fact_revisions(firm_id,old_book_id,target_book_id,created_at,id);
CREATE TABLE fact_revision_decisions(
 id TEXT PRIMARY KEY,revision_id TEXT NOT NULL,firm_id TEXT NOT NULL,decision TEXT NOT NULL CHECK(decision IN('accept','reject')),
 result_state TEXT NOT NULL CHECK(result_state IN('rejected','replaced','adjustment_proposed')),
 reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 3 AND 1000),reviewed_by TEXT NOT NULL REFERENCES users(id),
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 8 AND 120),request_fingerprint TEXT NOT NULL CHECK(length(request_fingerprint)=64),created_at TEXT NOT NULL,
 UNIQUE(revision_id),UNIQUE(firm_id,reviewed_by,idempotency_key),FOREIGN KEY(revision_id,firm_id) REFERENCES fact_revisions(id,firm_id)
);
CREATE TABLE fact_revision_lineage(
 revision_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 old_book_id TEXT NOT NULL,old_source_id TEXT NOT NULL UNIQUE,old_draft_id TEXT NOT NULL UNIQUE,
 new_book_id TEXT NOT NULL,new_source_id TEXT NOT NULL UNIQUE,new_draft_id TEXT NOT NULL UNIQUE,
 old_fact_version INTEGER NOT NULL,new_fact_version INTEGER NOT NULL CHECK(new_fact_version=old_fact_version+1),created_at TEXT NOT NULL,
 FOREIGN KEY(revision_id,firm_id) REFERENCES fact_revisions(id,firm_id),
 FOREIGN KEY(old_draft_id,old_source_id,old_book_id) REFERENCES drafts(id,source_id,book_id),
 FOREIGN KEY(new_draft_id,new_source_id,new_book_id) REFERENCES drafts(id,source_id,book_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id)
);


CREATE TRIGGER revisions_no_delete BEFORE DELETE ON fact_revisions BEGIN SELECT RAISE(ABORT,'fact revision history is immutable'); END;

CREATE TRIGGER fact_revision_decisions_immutable_update BEFORE UPDATE ON fact_revision_decisions BEGIN SELECT RAISE(ABORT,'fact revision evidence is immutable'); END;

CREATE TRIGGER fact_revision_decisions_immutable_delete BEFORE DELETE ON fact_revision_decisions BEGIN SELECT RAISE(ABORT,'fact revision evidence is immutable'); END;

CREATE TRIGGER fact_revision_lineage_immutable_update BEFORE UPDATE ON fact_revision_lineage BEGIN SELECT RAISE(ABORT,'fact revision evidence is immutable'); END;

CREATE TRIGGER fact_revision_lineage_immutable_delete BEFORE DELETE ON fact_revision_lineage BEGIN SELECT RAISE(ABORT,'fact revision evidence is immutable'); END;

DROP TRIGGER business_mappings_no_update;

CREATE TRIGGER revision_mapping_guard BEFORE UPDATE ON business_mappings WHEN NOT EXISTS(
 SELECT 1 FROM fact_revision_lineage l JOIN fact_revisions r ON r.id=l.revision_id JOIN fact_revision_decisions x ON x.revision_id=r.id
 WHERE l.event_id=OLD.event_id AND l.old_book_id=OLD.book_id AND l.old_source_id=OLD.source_id AND l.old_draft_id=OLD.draft_id
 AND l.new_book_id=NEW.book_id AND l.new_source_id=NEW.source_id AND l.new_draft_id=NEW.draft_id AND r.state='pending' AND x.result_state='replaced'
 AND NEW.event_id=OLD.event_id AND NEW.firm_id=OLD.firm_id AND NEW.company_id=OLD.company_id AND NEW.submitted_version=OLD.submitted_version)
 BEGIN SELECT RAISE(ABORT,'current mapping replacement requires independent fact revision'); END;

CREATE TRIGGER revision_old_draft_update BEFORE UPDATE ON drafts WHEN EXISTS(SELECT 1 FROM fact_revision_lineage WHERE old_draft_id=OLD.id AND old_book_id=OLD.book_id) BEGIN SELECT RAISE(ABORT,'superseded draft is frozen'); END;

CREATE TRIGGER revision_old_draft_delete BEFORE DELETE ON drafts WHEN EXISTS(SELECT 1 FROM fact_revision_lineage WHERE old_draft_id=OLD.id AND old_book_id=OLD.book_id) BEGIN SELECT RAISE(ABORT,'superseded draft is frozen'); END;

CREATE TRIGGER revision_pending_approval BEFORE UPDATE ON drafts WHEN NEW.status IN('approved','exported') AND EXISTS(SELECT 1 FROM fact_revisions WHERE old_draft_id=OLD.id AND old_book_id=OLD.book_id AND state='pending') BEGIN SELECT RAISE(ABORT,'pending fact revision requires independent review'); END;

CREATE TRIGGER revision_old_lines_insert BEFORE INSERT ON draft_lines WHEN EXISTS(SELECT 1 FROM fact_revision_lineage WHERE old_draft_id=NEW.draft_id AND old_book_id=NEW.book_id) BEGIN SELECT RAISE(ABORT,'superseded lines are frozen'); END;

CREATE TRIGGER revision_old_lines_update BEFORE UPDATE ON draft_lines WHEN EXISTS(SELECT 1 FROM fact_revision_lineage WHERE old_draft_id=NEW.draft_id AND old_book_id=NEW.book_id) BEGIN SELECT RAISE(ABORT,'superseded lines are frozen'); END;

CREATE TRIGGER revision_old_lines_delete BEFORE DELETE ON draft_lines WHEN EXISTS(SELECT 1 FROM fact_revision_lineage WHERE old_draft_id=OLD.draft_id AND old_book_id=OLD.book_id) BEGIN SELECT RAISE(ABORT,'superseded lines are frozen'); END;

CREATE TRIGGER revision_old_lines_cannot_move BEFORE UPDATE ON draft_lines WHEN EXISTS(SELECT 1 FROM fact_revision_lineage WHERE old_draft_id=OLD.draft_id AND old_book_id=OLD.book_id) BEGIN SELECT RAISE(ABORT,'superseded lines cannot move'); END;
CREATE TRIGGER revision_immutable_input_guard BEFORE UPDATE ON fact_revisions WHEN NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id OR NEW.event_id IS NOT OLD.event_id OR NEW.old_book_id IS NOT OLD.old_book_id OR NEW.old_source_id IS NOT OLD.old_source_id OR NEW.old_draft_id IS NOT OLD.old_draft_id OR NEW.target_book_id IS NOT OLD.target_book_id OR NEW.expected_event_version IS NOT OLD.expected_event_version OR NEW.expected_fact_version IS NOT OLD.expected_fact_version OR NEW.expected_draft_version IS NOT OLD.expected_draft_version OR NEW.old_snapshot IS NOT OLD.old_snapshot OR NEW.proposed_payload IS NOT OLD.proposed_payload OR NEW.reason IS NOT OLD.reason OR NEW.proposed_by IS NOT OLD.proposed_by OR NEW.idempotency_key IS NOT OLD.idempotency_key OR NEW.request_fingerprint IS NOT OLD.request_fingerprint OR NEW.created_at IS NOT OLD.created_at OR OLD.state<>'pending' OR NEW.version<>OLD.version+1 OR NOT EXISTS(SELECT 1 FROM fact_revision_decisions x WHERE x.revision_id=OLD.id AND x.result_state=NEW.state) BEGIN SELECT RAISE(ABORT,'revision inputs and review history are immutable'); END;

CREATE TRIGGER revision_handed_off_status_guard BEFORE UPDATE ON business_events WHEN OLD.status='handed_off' AND NEW.status<>'handed_off'
 BEGIN SELECT RAISE(ABORT,'handed-off status is frozen'); END;
CREATE TRIGGER revision_business_payload_guard BEFORE UPDATE ON business_events
 WHEN OLD.status NOT IN('draft','returned') AND NEW.payload IS NOT OLD.payload AND NOT EXISTS(
 SELECT 1 FROM fact_revisions r JOIN fact_revision_lineage l ON l.revision_id=r.id JOIN fact_revision_decisions x ON x.revision_id=r.id
 JOIN business_mappings m ON m.event_id=r.event_id WHERE r.event_id=OLD.id AND r.state='pending' AND x.result_state='replaced'
 AND r.proposed_payload=NEW.payload AND r.expected_event_version=OLD.version AND r.expected_fact_version=OLD.fact_version
 AND l.new_fact_version=NEW.fact_version AND m.book_id=l.new_book_id AND m.source_id=l.new_source_id AND m.draft_id=l.new_draft_id)
 BEGIN SELECT RAISE(ABORT,'approved business facts require independent revision'); END;
