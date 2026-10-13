-- Scoped evidence projections rely on latest persisted runs, never raw text scans.
CREATE INDEX IF NOT EXISTS idx_assistant_event_findings ON ai_findings(event_id,run_id,id);
CREATE INDEX IF NOT EXISTS idx_assistant_finding_reviews ON ai_finding_reviews(finding_id,created_at,id);
