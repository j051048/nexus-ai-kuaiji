CREATE INDEX idx_stage7_draft_page ON drafts(book_id,status,id);
CREATE INDEX idx_stage7_source_page ON sources(book_id,date,id);
CREATE INDEX idx_stage7_proposal_monitor ON proposal_jobs(firm_id,company_id,status,finished_at);
