-- compatibility: incremental
-- PLT05-C 受控子集补导：只追加的补导批次。
-- 刻意不改 bridge_outbox 的唯一约束、也不改桥接冻结清单守卫：旧守卫继续对
-- 「一次导出 = 一份交付意图 = 全量冻结清单」生效，补导走这张独立表，绝不削弱它。
CREATE TABLE IF NOT EXISTS bridge_reissue_batches(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  book_id TEXT NOT NULL REFERENCES books(id),
  ledger_id TEXT NOT NULL REFERENCES ledgers(id),
  period TEXT NOT NULL,
  parent_outbox_id TEXT NOT NULL REFERENCES bridge_outbox(id),
  parent_artifact_id TEXT NOT NULL REFERENCES bridge_export_artifacts(id),
  batch_code TEXT NOT NULL,
  failed_voucher_codes_json TEXT NOT NULL CHECK(json_valid(failed_voucher_codes_json)),
  source_configuration_version INTEGER NOT NULL,
  configuration_version INTEGER NOT NULL,
  content TEXT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK(length(content_sha256)=64),
  row_count INTEGER NOT NULL CHECK(row_count>=0),
  voucher_count INTEGER NOT NULL CHECK(voucher_count>0),
  idempotency_key TEXT NOT NULL,
  reason TEXT NOT NULL CHECK(length(trim(reason))>=3),
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE(firm_id,idempotency_key),
  UNIQUE(parent_artifact_id,content_sha256)
);
CREATE INDEX IF NOT EXISTS idx_bridge_reissue_book
  ON bridge_reissue_batches(firm_id,book_id,created_at,id);

CREATE TRIGGER IF NOT EXISTS bridge_reissue_batches_no_update
 BEFORE UPDATE ON bridge_reissue_batches
BEGIN SELECT RAISE(ABORT,'bridge reissue batches are append-only'); END;
CREATE TRIGGER IF NOT EXISTS bridge_reissue_batches_no_delete
 BEFORE DELETE ON bridge_reissue_batches
BEGIN SELECT RAISE(ABORT,'bridge reissue batches are append-only'); END;
