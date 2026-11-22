-- compatibility: incremental
-- 三家行业处理器的可复核草稿投影：物业与食材集采、贸易存货、内部医院汇总。
-- 一张表 + processor_code/kind 判别，保证「同一信封 + 同一契约 + 同一产物」只有一份草稿；
-- 草稿只是投影，不写凭证、不生成应收，任何输入变化都要重新生成。
CREATE TABLE IF NOT EXISTS fsc_industry_drafts(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES firms(id),
  company_id TEXT NOT NULL REFERENCES companies(id),
  book_id TEXT NOT NULL REFERENCES books(id),
  envelope_id TEXT NOT NULL REFERENCES fsc_import_envelopes(id),
  contract_id TEXT NOT NULL REFERENCES fsc_industry_contracts(id),
  processor_code TEXT NOT NULL CHECK(processor_code IN('property_service','trade_inventory','hospital_summary')),
  kind TEXT NOT NULL CHECK(kind IN(
    'property_amortization','grocery_movement',
    'trade_purchase_match','trade_cost_carry',
    'hospital_department_summary')),
  period TEXT NOT NULL,
  source_version INTEGER NOT NULL CHECK(source_version>0),
  total_cents INTEGER NOT NULL,
  lines_json TEXT NOT NULL CHECK(json_valid(lines_json)),
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  UNIQUE(envelope_id,contract_id,processor_code,kind)
);
CREATE INDEX IF NOT EXISTS idx_fsc_industry_drafts_book
  ON fsc_industry_drafts(firm_id,book_id,period,created_at);

-- 草稿只能追加，不能改也不能删：复核过的投影不能被悄悄改写。
CREATE TRIGGER IF NOT EXISTS fsc_industry_drafts_no_update
 BEFORE UPDATE ON fsc_industry_drafts
BEGIN SELECT RAISE(ABORT,'fsc industry drafts are append-only'); END;
CREATE TRIGGER IF NOT EXISTS fsc_industry_drafts_no_delete
 BEFORE DELETE ON fsc_industry_drafts
BEGIN SELECT RAISE(ABORT,'fsc industry drafts are append-only'); END;
