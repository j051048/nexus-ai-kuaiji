-- FSC04 second domain: advertising project revenue draft from a confirmed envelope.
CREATE TABLE IF NOT EXISTS fsc_advertising_revenue_drafts (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 book_id TEXT NOT NULL,
 envelope_id TEXT NOT NULL,
 contract_id TEXT NOT NULL,
 period TEXT NOT NULL,
 source_version INTEGER NOT NULL CHECK(source_version>0),
 total_cents INTEGER NOT NULL CHECK(total_cents>=0),
 lines_json TEXT NOT NULL CHECK(json_valid(lines_json)),
 fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(envelope_id,contract_id)
);
CREATE INDEX IF NOT EXISTS idx_fsc_adv_drafts_book
 ON fsc_advertising_revenue_drafts(firm_id,book_id,period,created_at);
