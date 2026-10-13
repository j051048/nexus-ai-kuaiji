CREATE TABLE assistant_worker_heartbeats(
 worker_id TEXT PRIMARY KEY CHECK(length(trim(worker_id)) BETWEEN 1 AND 120),started_at TEXT NOT NULL,heartbeat_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','idle','stopped')),inflight_count INTEGER NOT NULL CHECK(inflight_count BETWEEN 0 AND 2),
 CHECK(state='active' OR inflight_count=0)
);
CREATE TRIGGER ops_dirty_assistant_turns_insert AFTER INSERT ON assistant_turns BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1
 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;
CREATE TRIGGER ops_dirty_assistant_turns_update AFTER UPDATE ON assistant_turns BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1
 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;
