-- compatibility: incremental
-- Per-firm quotas: one row per firm. Absent row means "use the documented
-- defaults", so a new firm needs no bootstrap write and the defaults live in
-- nexus_backend/app/security/quotas.py (single source of truth, no drift).
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.firm_quotas(
  firm_id TEXT PRIMARY KEY,
  import_rows_per_batch INTEGER NOT NULL DEFAULT 5000
    CHECK(import_rows_per_batch BETWEEN 1 AND 200000),
  async_jobs_per_day INTEGER NOT NULL DEFAULT 500
    CHECK(async_jobs_per_day BETWEEN 1 AND 100000),
  storage_bytes BIGINT NOT NULL DEFAULT 2147483648
    CHECK(storage_bytes BETWEEN 1048576 AND 1099511627776),
  concurrent_requests INTEGER NOT NULL DEFAULT 32
    CHECK(concurrent_requests BETWEEN 1 AND 4096),
  updated_by TEXT REFERENCES accounting.users(id),
  updated_at TEXT NOT NULL
);

ALTER TABLE accounting.firm_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.firm_quotas FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS firm_quotas_read ON accounting.firm_quotas;
CREATE POLICY firm_quotas_read ON accounting.firm_quotas
  FOR SELECT TO accounting_app
  USING (firm_id = accounting.actor_firm());

DROP POLICY IF EXISTS firm_quotas_write ON accounting.firm_quotas;
CREATE POLICY firm_quotas_write ON accounting.firm_quotas
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.firm_manager(firm_id)
    AND updated_by = accounting.actor_user()
  );

DROP POLICY IF EXISTS firm_quotas_update ON accounting.firm_quotas;
CREATE POLICY firm_quotas_update ON accounting.firm_quotas
  FOR UPDATE TO accounting_app
  USING (firm_id = accounting.actor_firm() AND accounting.firm_manager(firm_id))
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.firm_manager(firm_id)
    AND updated_by = accounting.actor_user()
  );

REVOKE ALL ON accounting.firm_quotas
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT, UPDATE ON accounting.firm_quotas TO accounting_app;
