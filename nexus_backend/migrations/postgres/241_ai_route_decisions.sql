-- compatibility: breaking
CREATE TABLE IF NOT EXISTS accounting.ai_route_decisions(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 conversation_id TEXT,
 turn_id TEXT,
 question_sha256 TEXT NOT NULL,
 level TEXT NOT NULL CHECK(level IN('blocked','deterministic','memory','model')),
 matched_rule TEXT NOT NULL DEFAULT '',
 reason TEXT NOT NULL,
 budget_state TEXT NOT NULL DEFAULT 'unknown',
 input_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_route_decisions_company
 ON accounting.ai_route_decisions(company_id, created_at DESC);
ALTER TABLE accounting.ai_route_decisions ENABLE ROW LEVEL SECURITY;
CREATE POLICY ai_route_decisions_company ON accounting.ai_route_decisions
 USING (accounting.company_visible(company_id))
 WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT,INSERT ON accounting.ai_route_decisions TO accounting_app;
CREATE OR REPLACE FUNCTION accounting.ai_route_decisions_immutable() RETURNS trigger
 LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'ai route decisions are immutable' USING ERRCODE='check_violation';
END $$;
DROP TRIGGER IF EXISTS ai_route_decisions_no_update ON accounting.ai_route_decisions;
CREATE TRIGGER ai_route_decisions_no_update
 BEFORE UPDATE ON accounting.ai_route_decisions
 FOR EACH ROW EXECUTE FUNCTION accounting.ai_route_decisions_immutable();
DROP TRIGGER IF EXISTS ai_route_decisions_no_delete ON accounting.ai_route_decisions;
CREATE TRIGGER ai_route_decisions_no_delete
 BEFORE DELETE ON accounting.ai_route_decisions
 FOR EACH ROW EXECUTE FUNCTION accounting.ai_route_decisions_immutable();
