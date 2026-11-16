CREATE TABLE IF NOT EXISTS ai_route_decisions(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL REFERENCES firms(id),
 company_id TEXT NOT NULL REFERENCES companies(id),
 conversation_id TEXT,
 turn_id TEXT,
 question_sha256 TEXT NOT NULL,
 level TEXT NOT NULL CHECK(level IN('blocked','deterministic','memory','model')),
 matched_rule TEXT NOT NULL DEFAULT '',
 reason TEXT NOT NULL,
 budget_state TEXT NOT NULL DEFAULT 'unknown',
 input_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ai_route_decisions_company
 ON ai_route_decisions(company_id, created_at DESC);
CREATE TRIGGER IF NOT EXISTS ai_route_decisions_immutable
 BEFORE UPDATE ON ai_route_decisions
 BEGIN SELECT RAISE(ABORT, 'ai route decisions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ai_route_decisions_no_delete
 BEFORE DELETE ON ai_route_decisions
 BEGIN SELECT RAISE(ABORT, 'ai route decisions are immutable'); END;
