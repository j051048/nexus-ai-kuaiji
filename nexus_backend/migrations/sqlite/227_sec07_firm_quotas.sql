-- compatibility: incremental
-- SQLite mirror of 320_sec07_firm_quotas.sql. No RLS here: SQLite is the local
-- and test engine, and its isolation story is the single-file boundary, not
-- row policies. The CHECKs and the "no delete" trigger still apply so a local
-- dataset cannot drift from the production contract.
CREATE TABLE IF NOT EXISTS firm_quotas(
  firm_id TEXT PRIMARY KEY,
  import_rows_per_batch INTEGER NOT NULL DEFAULT 5000
    CHECK(import_rows_per_batch BETWEEN 1 AND 200000),
  async_jobs_per_day INTEGER NOT NULL DEFAULT 500
    CHECK(async_jobs_per_day BETWEEN 1 AND 100000),
  storage_bytes INTEGER NOT NULL DEFAULT 2147483648
    CHECK(storage_bytes BETWEEN 1048576 AND 1099511627776),
  concurrent_requests INTEGER NOT NULL DEFAULT 32
    CHECK(concurrent_requests BETWEEN 1 AND 4096),
  updated_by TEXT REFERENCES users(id),
  updated_at TEXT NOT NULL
);

DROP TRIGGER IF EXISTS firm_quotas_no_delete;
CREATE TRIGGER firm_quotas_no_delete
BEFORE DELETE ON firm_quotas
BEGIN
  SELECT RAISE(ABORT, '租户配额不能被删除：请调整数值而不是移除记录');
END;
