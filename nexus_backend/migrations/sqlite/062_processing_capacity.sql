CREATE TABLE processing_runtime_limits(singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 processing_global_limit INTEGER NOT NULL DEFAULT 2 CHECK(processing_global_limit BETWEEN 1 AND 8),
 provider_global_limit INTEGER NOT NULL DEFAULT 2 CHECK(provider_global_limit BETWEEN 1 AND 8),
 provider_default_limit INTEGER NOT NULL DEFAULT 2 CHECK(provider_default_limit BETWEEN 1 AND 8));
INSERT INTO processing_runtime_limits(singleton) VALUES(1);
CREATE TABLE provider_runtime_limits(provider TEXT PRIMARY KEY,max_inflight INTEGER NOT NULL CHECK(max_inflight BETWEEN 1 AND 8));
CREATE TABLE provider_request_slots(id TEXT PRIMARY KEY CHECK(length(id)=32),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,user_id TEXT NOT NULL,
 provider TEXT NOT NULL,lease_until INTEGER NOT NULL,FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),FOREIGN KEY(user_id) REFERENCES users(id));
CREATE INDEX provider_slots_expiration ON provider_request_slots(lease_until);
CREATE UNIQUE INDEX provider_slots_one_company ON provider_request_slots(firm_id,company_id);
CREATE INDEX batch2_ocr_due ON ocr_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE INDEX batch2_proposal_due ON proposal_jobs(status,available_at,firm_id,company_id,created_at,id);
CREATE INDEX batch2_assistant_due ON assistant_turns(status,available_at,firm_id,company_id,created_at,id);
