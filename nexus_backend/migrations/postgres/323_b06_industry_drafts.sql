-- compatibility: incremental
-- 三家行业处理器的可复核草稿投影：物业与食材集采、贸易存货、内部医院汇总。
-- 一张表 + processor_code/kind 判别，保证「同一信封 + 同一契约 + 同一产物」只有一份草稿；
-- 草稿只是投影，不写凭证、不生成应收，任何输入变化都要重新生成。
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.fsc_industry_drafts(
  id TEXT PRIMARY KEY,
  firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
  company_id TEXT NOT NULL REFERENCES accounting.companies(id),
  book_id TEXT NOT NULL REFERENCES accounting.books(id),
  envelope_id TEXT NOT NULL REFERENCES accounting.fsc_import_envelopes(id),
  contract_id TEXT NOT NULL REFERENCES accounting.fsc_industry_contracts(id),
  processor_code TEXT NOT NULL CHECK(processor_code IN('property_service','trade_inventory','hospital_summary')),
  kind TEXT NOT NULL CHECK(kind IN(
    'property_amortization','grocery_movement',
    'trade_purchase_match','trade_cost_carry',
    'hospital_department_summary')),
  period TEXT NOT NULL,
  source_version INTEGER NOT NULL CHECK(source_version>0),
  total_cents BIGINT NOT NULL,
  lines_json JSONB NOT NULL,
  fingerprint TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES accounting.users(id),
  created_at TEXT NOT NULL,
  UNIQUE(envelope_id,contract_id,processor_code,kind)
);
CREATE INDEX IF NOT EXISTS idx_fsc_industry_drafts_book
  ON accounting.fsc_industry_drafts(firm_id,book_id,period,created_at);

ALTER TABLE accounting.fsc_industry_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_industry_drafts FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS fsc_industry_drafts_access ON accounting.fsc_industry_drafts;
CREATE POLICY fsc_industry_drafts_access ON accounting.fsc_industry_drafts
  FOR SELECT TO accounting_app
  USING (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND accounting.book_allowed(book_id)
  );
DROP POLICY IF EXISTS fsc_industry_drafts_insert ON accounting.fsc_industry_drafts;
CREATE POLICY fsc_industry_drafts_insert ON accounting.fsc_industry_drafts
  FOR INSERT TO accounting_app
  WITH CHECK (
    firm_id = accounting.actor_firm()
    AND accounting.company_visible(company_id)
    AND accounting.book_review_allowed(book_id)
    AND created_by = accounting.actor_user()
  );

REVOKE ALL ON accounting.fsc_industry_drafts
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT ON accounting.fsc_industry_drafts TO accounting_app;

CREATE OR REPLACE FUNCTION accounting.fsc_industry_drafts_append_only() RETURNS TRIGGER
  LANGUAGE plpgsql SET search_path=accounting,pg_catalog AS $$
BEGIN
  RAISE EXCEPTION 'fsc industry drafts are append-only' USING ERRCODE = '55000';
END;
$$;
REVOKE ALL ON FUNCTION accounting.fsc_industry_drafts_append_only()
  FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;

DROP TRIGGER IF EXISTS fsc_industry_drafts_append_only ON accounting.fsc_industry_drafts;
CREATE TRIGGER fsc_industry_drafts_append_only
 BEFORE UPDATE OR DELETE ON accounting.fsc_industry_drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.fsc_industry_drafts_append_only();

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fsc_industry_drafts;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_industry_drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
