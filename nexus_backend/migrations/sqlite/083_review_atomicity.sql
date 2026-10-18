-- compatibility: incremental
-- R01/F02: one posted voucher can belong to at most one periodic draft.
CREATE UNIQUE INDEX IF NOT EXISTS periodic_draft_posted_voucher
 ON periodic_drafts(posted_voucher_id) WHERE posted_voucher_id IS NOT NULL;
