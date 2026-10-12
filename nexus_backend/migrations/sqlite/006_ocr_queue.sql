-- SQLite local queue: application enforces tenant access and worker capabilities.
CREATE UNIQUE INDEX idx_ai_run_queue_scope ON ai_runs(id,event_id,firm_id,company_id,attachment_id);

CREATE TABLE ocr_batches (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),idempotency_key TEXT NOT NULL,
 request_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,
 UNIQUE(firm_id,created_by,idempotency_key),UNIQUE(id,firm_id,company_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
CREATE TABLE ocr_jobs (
 id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 event_id TEXT NOT NULL,attachment_id TEXT NOT NULL,expected_version INTEGER NOT NULL CHECK(expected_version>0),
 attachment_sha256 TEXT NOT NULL,configuration_fingerprint TEXT NOT NULL,
 execution_user_id TEXT NOT NULL REFERENCES users(id),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','retry_wait','succeeded','failed','interrupted','stale','cancelled')),
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count>=0),max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 30 AND attempt_count<=max_attempts),
 available_at TEXT NOT NULL,lease_token TEXT,lease_until TEXT,worker_id TEXT,current_run_id TEXT,
 error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120),error_detail TEXT NOT NULL DEFAULT '' CHECK(length(error_detail)<=1000),
 updated_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(id,event_id,firm_id,company_id,attachment_id),
 FOREIGN KEY(batch_id,firm_id,company_id) REFERENCES ocr_batches(id,firm_id,company_id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id),
 FOREIGN KEY(attachment_id,event_id) REFERENCES business_attachments(id,event_id),
 FOREIGN KEY(current_run_id,event_id,firm_id,company_id,attachment_id) REFERENCES ai_runs(id,event_id,firm_id,company_id,attachment_id),
 CHECK((status='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND worker_id IS NOT NULL AND finished_at IS NULL)
  OR (status IN ('queued','retry_wait') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NULL)
  OR (status IN ('succeeded','failed','interrupted','stale','cancelled') AND lease_token IS NULL AND lease_until IS NULL AND worker_id IS NULL AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX idx_ocr_active_attachment ON ocr_jobs(event_id,attachment_id) WHERE status IN ('queued','running','retry_wait');
CREATE INDEX idx_ocr_jobs_dispatch ON ocr_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE INDEX idx_ocr_jobs_batch ON ocr_jobs(batch_id,created_at,id);
CREATE TABLE ocr_attempts (
 id TEXT PRIMARY KEY,job_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,attachment_id TEXT NOT NULL,
 attempt_no INTEGER NOT NULL CHECK(attempt_no>0),lease_token TEXT NOT NULL,
 execution_user_id TEXT NOT NULL REFERENCES users(id),input_version INTEGER NOT NULL CHECK(input_version>0),
 attachment_sha256 TEXT NOT NULL,configuration_fingerprint TEXT NOT NULL,
 phase TEXT NOT NULL DEFAULT 'claimed' CHECK(phase IN ('claimed','sending','finished')),
 status TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','succeeded','failed','interrupted','stale','cancelled')),
 ai_run_id TEXT,error_code TEXT NOT NULL DEFAULT '' CHECK(length(error_code)<=120),error_detail TEXT NOT NULL DEFAULT '' CHECK(length(error_detail)<=1000),
 created_at TEXT NOT NULL,finished_at TEXT,
 UNIQUE(job_id,attempt_no),UNIQUE(job_id,lease_token),
 FOREIGN KEY(job_id,event_id,firm_id,company_id,attachment_id) REFERENCES ocr_jobs(id,event_id,firm_id,company_id,attachment_id),
 FOREIGN KEY(ai_run_id,event_id,firm_id,company_id,attachment_id) REFERENCES ai_runs(id,event_id,firm_id,company_id,attachment_id),
 CHECK((status='running' AND phase IN ('claimed','sending') AND finished_at IS NULL)
  OR (status<>'running' AND phase='finished' AND finished_at IS NOT NULL))
);
CREATE TABLE ocr_retries (
 job_id TEXT NOT NULL,request_key TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 requested_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(job_id,request_key),
 FOREIGN KEY(job_id) REFERENCES ocr_jobs(id),
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id)
);
CREATE TABLE ocr_company_schedule (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,last_dispatch_seq INTEGER NOT NULL DEFAULT 0 CHECK(last_dispatch_seq>=0),
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);

CREATE TRIGGER ocr_jobs_no_delete BEFORE DELETE ON ocr_jobs BEGIN SELECT RAISE(ABORT,'Queue job is immutable'); END;
CREATE TRIGGER ocr_batches_no_update BEFORE UPDATE ON ocr_batches BEGIN SELECT RAISE(ABORT,'Queue batch is immutable'); END;
CREATE TRIGGER ocr_batches_no_delete BEFORE DELETE ON ocr_batches BEGIN SELECT RAISE(ABORT,'Queue batch is immutable'); END;
CREATE TRIGGER ocr_retries_no_update BEFORE UPDATE ON ocr_retries BEGIN SELECT RAISE(ABORT,'Queue retry history is immutable'); END;
CREATE TRIGGER ocr_retries_no_delete BEFORE DELETE ON ocr_retries BEGIN SELECT RAISE(ABORT,'Queue retry history is immutable'); END;
CREATE TRIGGER ocr_job_identity BEFORE UPDATE ON ocr_jobs
 WHEN NEW.id IS NOT OLD.id OR NEW.batch_id IS NOT OLD.batch_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.event_id IS NOT OLD.event_id OR NEW.attachment_id IS NOT OLD.attachment_id OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
 BEGIN SELECT RAISE(ABORT,'Queue job scope and provenance are immutable'); END;
CREATE TRIGGER ocr_attempts_no_delete BEFORE DELETE ON ocr_attempts BEGIN SELECT RAISE(ABORT,'Queue attempt is immutable'); END;
CREATE TRIGGER ocr_attempt_identity BEFORE UPDATE ON ocr_attempts
 WHEN OLD.status<>'running' OR NEW.id IS NOT OLD.id OR NEW.job_id IS NOT OLD.job_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.event_id IS NOT OLD.event_id OR NEW.attachment_id IS NOT OLD.attachment_id OR NEW.attempt_no IS NOT OLD.attempt_no OR NEW.lease_token IS NOT OLD.lease_token
 OR NEW.execution_user_id IS NOT OLD.execution_user_id OR NEW.input_version IS NOT OLD.input_version OR NEW.attachment_sha256 IS NOT OLD.attachment_sha256
 OR NEW.configuration_fingerprint IS NOT OLD.configuration_fingerprint OR NEW.created_at IS NOT OLD.created_at
 OR (OLD.ai_run_id IS NOT NULL AND NEW.ai_run_id IS NOT OLD.ai_run_id) OR (OLD.phase='sending' AND NEW.phase='claimed')
 BEGIN SELECT RAISE(ABORT,'Queue attempt history is immutable'); END;
