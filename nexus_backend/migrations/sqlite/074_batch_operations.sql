-- compatibility: incremental
CREATE TABLE IF NOT EXISTS batch_operations(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('monthly_open')),
 status TEXT NOT NULL CHECK(status IN('queued','running','completed','attention','cancelled')),
 idempotency_key TEXT NOT NULL,
 request_json TEXT NOT NULL,
 total INTEGER NOT NULL CHECK(total>=0),
 succeeded INTEGER NOT NULL DEFAULT 0 CHECK(succeeded>=0),
 skipped INTEGER NOT NULL DEFAULT 0 CHECK(skipped>=0),
 failed INTEGER NOT NULL DEFAULT 0 CHECK(failed>=0),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT,
 UNIQUE(firm_id,created_by,idempotency_key)
);
CREATE TABLE IF NOT EXISTS batch_operation_items(
 id TEXT PRIMARY KEY,
 batch_id TEXT NOT NULL REFERENCES batch_operations(id) ON DELETE CASCADE,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 company_name TEXT NOT NULL DEFAULT '',
 sequence_no INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN('queued','running','succeeded','skipped','failed','retry_wait','cancelled')),
 attempt INTEGER NOT NULL DEFAULT 0 CHECK(attempt>=0),
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
 available_at TEXT NOT NULL,
 lease_token TEXT,
 lease_until TEXT,
 worker_id TEXT,
 result_json TEXT NOT NULL DEFAULT '{}',
 error_code TEXT NOT NULL DEFAULT '',
 error_detail TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 started_at TEXT,
 finished_at TEXT,
 UNIQUE(batch_id,company_id)
);
CREATE INDEX IF NOT EXISTS idx_batch_operations_firm
 ON batch_operations(firm_id,created_at DESC,id);
CREATE INDEX IF NOT EXISTS idx_batch_items_claim
 ON batch_operation_items(status,available_at,created_at,id);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch
 ON batch_operation_items(batch_id,sequence_no,id);
