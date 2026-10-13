CREATE TABLE transaction_metric_buckets (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,stream_id TEXT NOT NULL CHECK(length(stream_id) BETWEEN 1 AND 80),
 bucket_minute INTEGER NOT NULL,tx_count INTEGER NOT NULL CHECK(tx_count BETWEEN 1 AND 1000000),
 write_count INTEGER NOT NULL CHECK(write_count BETWEEN 0 AND tx_count),
 rollback_count INTEGER NOT NULL CHECK(rollback_count BETWEEN 0 AND tx_count),
 connection_sum REAL NOT NULL CHECK(connection_sum BETWEEN 0 AND 86400000000000),
 lock_sum REAL NOT NULL CHECK(lock_sum BETWEEN 0 AND 86400000000000),
 transaction_sum REAL NOT NULL CHECK(transaction_sum BETWEEN 0 AND 86400000000000),
 connection_max REAL NOT NULL CHECK(connection_max BETWEEN 0 AND 86400000),
 lock_max REAL NOT NULL CHECK(lock_max BETWEEN 0 AND 86400000),
 transaction_max REAL NOT NULL CHECK(transaction_max BETWEEN 0 AND 86400000),
 PRIMARY KEY(firm_id,company_id,stream_id,bucket_minute),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id)
);
CREATE INDEX idx_transaction_metrics_expire ON transaction_metric_buckets(bucket_minute);
CREATE INDEX idx_transaction_metrics_company ON transaction_metric_buckets(firm_id,company_id,bucket_minute);
CREATE INDEX idx_telemetry_ocr_attempts ON ocr_attempts(firm_id,company_id,created_at DESC,id DESC);
CREATE INDEX idx_telemetry_proposal_attempts ON proposal_attempts(firm_id,company_id,created_at DESC,id DESC);
CREATE INDEX idx_telemetry_assistant_turns ON assistant_turns(firm_id,company_id,created_at DESC,id DESC);
CREATE INDEX idx_telemetry_migration_jobs ON migration_jobs(firm_id,company_id,created_at DESC,id DESC);
