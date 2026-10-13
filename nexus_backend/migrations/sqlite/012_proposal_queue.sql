-- Independent durable proposal jobs; facts and workflow versions are distinct.
ALTER TABLE ocr_jobs ADD COLUMN expected_fact_version INTEGER CHECK(expected_fact_version>0);
CREATE UNIQUE INDEX idx_ai_run_proposal_scope ON ai_runs(id,event_id,firm_id,company_id,book_id,draft_id);
CREATE UNIQUE INDEX idx_business_mapping_proposal_scope ON business_mappings(event_id,firm_id,company_id,book_id,draft_id);

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
 updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(firm_id,idempotency_key),UNIQUE(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(event_id,firm_id,company_id,book_id,draft_id) REFERENCES business_mappings(event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(current_run_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES ai_runs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(result_template_id,result_template_version) REFERENCES journal_template_versions(template_id,version),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL)
  OR (status IN ('waiting','queued','retry_wait') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL)
  OR (status IN ('succeeded','needs_attention','failed','interrupted','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX idx_proposal_active_draft ON proposal_jobs(draft_id) WHERE status IN ('waiting','queued','running','retry_wait');
CREATE INDEX idx_proposal_dispatch ON proposal_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE INDEX idx_proposal_book_page ON proposal_jobs(firm_id,book_id,created_at,id);
CREATE TABLE proposal_dependencies (
 proposal_job_id TEXT NOT NULL,generation INTEGER NOT NULL,ocr_job_id TEXT NOT NULL,attachment_id TEXT NOT NULL,
 event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,draft_id TEXT NOT NULL,
 PRIMARY KEY(proposal_job_id,generation,ocr_job_id),
 FOREIGN KEY(proposal_job_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES proposal_jobs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(ocr_job_id,event_id,firm_id,company_id,attachment_id) REFERENCES ocr_jobs(id,event_id,firm_id,company_id,attachment_id)
);
CREATE TABLE proposal_attempts (
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL,event_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,book_id TEXT NOT NULL,draft_id TEXT NOT NULL,
 attempt_no INTEGER NOT NULL,lease_token TEXT NOT NULL,execution_user_id TEXT NOT NULL REFERENCES users(id),
 input_fact_version INTEGER NOT NULL,input_draft_version INTEGER NOT NULL,configuration_fingerprint TEXT NOT NULL,policy_fingerprint TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'claimed' CHECK(phase IN ('claimed','sending','finished')),
 status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','succeeded','needs_attention','failed','interrupted','stale','cancelled')),
 ai_run_id TEXT,error_code TEXT NOT NULL DEFAULT '',error_detail TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(job_id,attempt_no),UNIQUE(job_id,lease_token),
 FOREIGN KEY(job_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES proposal_jobs(id,event_id,firm_id,company_id,book_id,draft_id),
 FOREIGN KEY(ai_run_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES ai_runs(id,event_id,firm_id,company_id,book_id,draft_id),
 CHECK((status='running' AND phase IN ('claimed','sending') AND finished_at IS NULL) OR (status<>'running' AND phase='finished' AND finished_at IS NOT NULL))
);
CREATE TABLE proposal_retries (
 job_id TEXT NOT NULL,request_key TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,book_id TEXT NOT NULL,draft_id TEXT NOT NULL,
 requested_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,reason TEXT NOT NULL CHECK(length(reason) BETWEEN 3 AND 1000),PRIMARY KEY(job_id,request_key),
 FOREIGN KEY(job_id,event_id,firm_id,company_id,book_id,draft_id) REFERENCES proposal_jobs(id,event_id,firm_id,company_id,book_id,draft_id)
);
CREATE TRIGGER proposal_jobs_no_delete BEFORE DELETE ON proposal_jobs BEGIN SELECT RAISE(ABORT,'Proposal job history is immutable'); END;
CREATE TRIGGER proposal_job_identity BEFORE UPDATE ON proposal_jobs
 WHEN NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id OR NEW.event_id IS NOT OLD.event_id
 OR NEW.book_id IS NOT OLD.book_id OR NEW.draft_id IS NOT OLD.draft_id OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at OR NEW.idempotency_key IS NOT OLD.idempotency_key
 BEGIN SELECT RAISE(ABORT,'Proposal job scope and provenance are immutable'); END;
CREATE TRIGGER proposal_attempts_no_delete BEFORE DELETE ON proposal_attempts BEGIN SELECT RAISE(ABORT,'Proposal attempt is immutable'); END;
CREATE TRIGGER proposal_attempt_identity BEFORE UPDATE ON proposal_attempts
 WHEN OLD.status<>'running' OR NEW.id IS NOT OLD.id OR NEW.job_id IS NOT OLD.job_id OR NEW.event_id IS NOT OLD.event_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.book_id IS NOT OLD.book_id OR NEW.draft_id IS NOT OLD.draft_id OR NEW.attempt_no IS NOT OLD.attempt_no OR NEW.lease_token IS NOT OLD.lease_token OR NEW.execution_user_id IS NOT OLD.execution_user_id
 OR NEW.input_fact_version IS NOT OLD.input_fact_version OR NEW.input_draft_version IS NOT OLD.input_draft_version OR NEW.configuration_fingerprint IS NOT OLD.configuration_fingerprint OR NEW.policy_fingerprint IS NOT OLD.policy_fingerprint
 OR NEW.created_at IS NOT OLD.created_at OR (OLD.ai_run_id IS NOT NULL AND NEW.ai_run_id IS NOT OLD.ai_run_id) OR (OLD.phase='sending' AND NEW.phase='claimed')
 BEGIN SELECT RAISE(ABORT,'Proposal attempt history is immutable'); END;
CREATE TRIGGER proposal_dependencies_no_update BEFORE UPDATE ON proposal_dependencies BEGIN SELECT RAISE(ABORT,'Proposal dependencies are immutable'); END;
CREATE TRIGGER proposal_dependencies_no_delete BEFORE DELETE ON proposal_dependencies BEGIN SELECT RAISE(ABORT,'Proposal dependencies are immutable'); END;
CREATE TRIGGER proposal_retries_no_update BEFORE UPDATE ON proposal_retries BEGIN SELECT RAISE(ABORT,'Proposal retry receipt is immutable'); END;
CREATE TRIGGER proposal_retries_no_delete BEFORE DELETE ON proposal_retries BEGIN SELECT RAISE(ABORT,'Proposal retry receipt is immutable'); END;
