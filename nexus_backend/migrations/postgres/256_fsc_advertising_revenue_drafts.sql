-- FSC04 second domain: advertising project revenue draft from a confirmed envelope.
CREATE TABLE IF NOT EXISTS accounting.fsc_advertising_revenue_drafts (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 envelope_id TEXT NOT NULL,
 contract_id TEXT NOT NULL,
 period TEXT NOT NULL,
 source_version INTEGER NOT NULL CHECK(source_version>0),
 total_cents BIGINT NOT NULL CHECK(total_cents>=0),
 lines_json JSONB NOT NULL,
 fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL,
 UNIQUE(envelope_id,contract_id)
);
CREATE INDEX IF NOT EXISTS idx_fsc_adv_drafts_book
 ON accounting.fsc_advertising_revenue_drafts(firm_id,book_id,period,created_at);

ALTER TABLE accounting.fsc_advertising_revenue_drafts ENABLE ROW LEVEL SECURITY;
CREATE POLICY fsc_adv_drafts_company ON accounting.fsc_advertising_revenue_drafts
 USING (accounting.company_visible(company_id))
 WITH CHECK (accounting.company_visible(company_id));

GRANT SELECT, INSERT ON accounting.fsc_advertising_revenue_drafts TO accounting_app;
