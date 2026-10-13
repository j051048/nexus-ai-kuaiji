CREATE INDEX idx_workspace_business_own ON business_events(firm_id,applicant_id,status,updated_at DESC,id);
CREATE INDEX idx_workspace_ocr_latest ON ocr_jobs(event_id,attachment_id,created_at DESC,id DESC);
CREATE INDEX idx_workspace_proposal_latest ON proposal_jobs(draft_id,created_at DESC,id DESC);
