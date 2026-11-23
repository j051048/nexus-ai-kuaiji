-- compatibility: incremental
-- PLT05-C 受控子集补导：只追加的补导批次。
-- 刻意不改 bridge_outbox 的唯一约束、也不改 bridge_scope_guard()：旧守卫继续对
-- 「一次导出 = 一份交付意图 = 全量冻结清单」生效，补导走这张独立表，绝不削弱它。
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.bridge_reissue_batches(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
  company_id TEXT NOT NULL REFERENCES accounting.companies(id),
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  ledger_id TEXT NOT NULL REFERENCES accounting.ledgers(id),
  period TEXT NOT NULL,
  parent_outbox_id TEXT NOT NULL REFERENCES accounting.bridge_outbox(id),
  parent_artifact_id TEXT NOT NULL REFERENCES accounting.bridge_export_artifacts(id),
  batch_code TEXT NOT NULL,
  failed_voucher_codes_json JSONB NOT NULL,
  source_configuration_version INTEGER NOT NULL,
  configuration_version INTEGER NOT NULL,
  content TEXT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
  row_count INTEGER NOT NULL CHECK(row_count>=0),
  voucher_count INTEGER NOT NULL CHECK(voucher_count>0),
  idempotency_key TEXT NOT NULL,
  reason TEXT NOT NULL CHECK(length(btrim(reason))>=3),
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TEXT NOT NULL,
  UNIQUE(firm_id,idempotency_key),
  UNIQUE(parent_artifact_id,content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_bridge_reissue_book
  ON accounting.bridge_reissue_batches(firm_id,book_id,created_at,id);

ALTER TABLE accounting.bridge_reissue_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.bridge_reissue_batches FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS bridge_reissue_read ON accounting.bridge_reissue_batches;
CREATE POLICY bridge_reissue_read ON accounting.bridge_reissue_batches
  FOR SELECT TO accounting_app
  USING (firm_id = accounting.actor_firm() AND accounting.book_allowed(book_id));
DROP POLICY IF EXISTS bridge_reissue_insert ON accounting.bridge_reissue_batches;
CREATE POLICY bridge_reissue_insert ON accounting.bridge_reissue_batches
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.ai_book_reviewer(book_id)
    AND created_by = accounting.actor_user()
  );

REVOKE ALL ON accounting.bridge_reissue_batches
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT ON accounting.bridge_reissue_batches TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.bridge_reissue_append_only() RETURNS trigger
  LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'bridge reissue batches are append-only' USING ERRCODE = '55000';
END;
$$;
REVOKE ALL ON FUNCTION accounting.bridge_reissue_append_only()
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
DROP TRIGGER IF EXISTS bridge_reissue_batches_no_change ON accounting.bridge_reissue_batches;
CREATE TRIGGER bridge_reissue_batches_no_change
  BEFORE UPDATE OR DELETE ON accounting.bridge_reissue_batches
  FOR EACH ROW EXECUTE FUNCTION accounting.bridge_reissue_append_only();

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.bridge_reissue_batches;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_reissue_batches
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
