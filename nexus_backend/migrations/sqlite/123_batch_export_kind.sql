-- compatibility: incremental
-- DCP06-B：批量受控导出复用同一张批量表，不另起队列。SQLite 无法修改 CHECK，
-- 因此按“建新表→拷数据→换名”的标准做法放开 kind。
PRAGMA defer_foreign_keys=ON;
CREATE TABLE batch_operations_new(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN('monthly_open','review_export')),
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
 cancelled INTEGER NOT NULL DEFAULT 0,
 UNIQUE(firm_id,created_by,idempotency_key)
);
INSERT INTO batch_operations_new(
 id,firm_id,kind,status,idempotency_key,request_json,total,succeeded,skipped,failed,
 created_by,created_at,updated_at,started_at,finished_at,cancelled)
SELECT id,firm_id,kind,status,idempotency_key,request_json,total,succeeded,skipped,failed,
 created_by,created_at,updated_at,started_at,finished_at,cancelled FROM batch_operations;
DROP TABLE batch_operations;
ALTER TABLE batch_operations_new RENAME TO batch_operations;
CREATE INDEX IF NOT EXISTS idx_batch_operations_firm
 ON batch_operations(firm_id,created_at DESC,id);
