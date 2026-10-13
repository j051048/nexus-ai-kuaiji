-- New costs preserve both existing run types and their source identities.
ALTER TABLE model_company_policies ADD COLUMN chat_reserve_micro INTEGER NOT NULL DEFAULT 0 CHECK(chat_reserve_micro BETWEEN 0 AND 999999999999990000);
CREATE TABLE model_costs_chat_upgrade (
 id TEXT PRIMARY KEY,run_id TEXT UNIQUE CHECK(id=run_id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN ('ocr','proposal','chat')),created_by TEXT NOT NULL REFERENCES users(id),
 period TEXT NOT NULL CHECK(length(period)=7 AND substr(period,5,1)='-' AND substr(period,1,4) GLOB '[0-9][0-9][0-9][0-9]' AND CAST(substr(period,6,2) AS INTEGER) BETWEEN 1 AND 12),provider TEXT NOT NULL,model TEXT NOT NULL,charge_token TEXT NOT NULL,
 pricing_json TEXT NOT NULL CHECK(json_valid(pricing_json) AND json_type(pricing_json)='object'),quota_enabled INTEGER NOT NULL CHECK(quota_enabled IN (0,1)),
 reserved_micro INTEGER NOT NULL CHECK(reserved_micro BETWEEN 0 AND 999999999999990000),
 estimated_micro INTEGER CHECK(estimated_micro BETWEEN 0 AND 999999999999990000),confirmed_micro INTEGER CHECK(confirmed_micro BETWEEN 0 AND 999999999999990000),
 input_tokens INTEGER CHECK(input_tokens BETWEEN 0 AND 10000000),output_tokens INTEGER CHECK(output_tokens BETWEEN 0 AND 10000000),
 state TEXT NOT NULL CHECK(state IN ('reserved','sent','estimated','uncertain','released','reconciled')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_at TEXT NOT NULL,sent_at TEXT,finished_at TEXT,
 overrun INTEGER NOT NULL DEFAULT 0 CHECK(overrun IN (0,1)),manual_reason TEXT NOT NULL DEFAULT '',manual_by TEXT REFERENCES users(id),
 intake_run_id TEXT UNIQUE,chat_turn_id TEXT UNIQUE,
 FOREIGN KEY(run_id,event_id,firm_id,company_id,kind,created_by) REFERENCES ai_runs(id,event_id,firm_id,company_id,kind,created_by),
 UNIQUE(id,event_id,firm_id,company_id),
 CHECK((state IN ('reserved','sent') AND finished_at IS NULL) OR (state NOT IN ('reserved','sent') AND finished_at IS NOT NULL)),
 CHECK(state<>'estimated' OR estimated_micro IS NOT NULL),
 CHECK(state<>'reconciled' OR (confirmed_micro IS NOT NULL AND manual_by IS NOT NULL AND length(trim(manual_reason))>0))
 ,
 FOREIGN KEY(intake_run_id,firm_id,company_id,created_by) REFERENCES intake_runs(id,firm_id,company_id,created_by),
 FOREIGN KEY(chat_turn_id,firm_id,company_id,created_by) REFERENCES assistant_turns(id,firm_id,company_id,created_by),
 CHECK((chat_turn_id IS NULL AND intake_run_id IS NULL AND run_id IS NOT NULL AND event_id IS NOT NULL AND kind IN('ocr','proposal')) OR (chat_turn_id IS NULL AND intake_run_id IS NOT NULL AND intake_run_id=id AND run_id IS NULL AND event_id IS NULL AND kind='ocr') OR (chat_turn_id IS NOT NULL AND chat_turn_id=id AND intake_run_id IS NULL AND run_id IS NULL AND event_id IS NULL AND kind='chat'))
);
INSERT INTO model_costs_chat_upgrade(id,run_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,estimated_micro,confirmed_micro,input_tokens,output_tokens,state,version,created_at,sent_at,finished_at,overrun,manual_reason,manual_by,intake_run_id) SELECT id,run_id,firm_id,company_id,event_id,kind,created_by,period,provider,model,charge_token,pricing_json,quota_enabled,reserved_micro,estimated_micro,confirmed_micro,input_tokens,output_tokens,state,version,created_at,sent_at,finished_at,overrun,manual_reason,manual_by,intake_run_id FROM model_costs;
PRAGMA legacy_alter_table=ON;
DROP TABLE model_costs;
ALTER TABLE model_costs_chat_upgrade RENAME TO model_costs;
PRAGMA legacy_alter_table=OFF;
CREATE INDEX idx_model_costs_month ON model_costs(firm_id,company_id,period,state);
CREATE TRIGGER model_cost_identity BEFORE UPDATE ON model_costs
 WHEN NEW.chat_turn_id IS NOT OLD.chat_turn_id OR NEW.intake_run_id IS NOT OLD.intake_run_id OR NEW.id IS NOT OLD.id OR NEW.run_id IS NOT OLD.run_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.event_id IS NOT OLD.event_id OR NEW.kind IS NOT OLD.kind OR NEW.created_by IS NOT OLD.created_by OR NEW.period IS NOT OLD.period
 OR NEW.provider IS NOT OLD.provider OR NEW.model IS NOT OLD.model OR NEW.charge_token IS NOT OLD.charge_token OR NEW.pricing_json IS NOT OLD.pricing_json
 OR NEW.quota_enabled IS NOT OLD.quota_enabled OR NEW.reserved_micro IS NOT OLD.reserved_micro OR NEW.created_at IS NOT OLD.created_at
 OR NEW.version<>OLD.version+1 OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS NOT OLD.sent_at)
 OR (OLD.state IN ('estimated','released','reconciled') AND NEW.state<>'reconciled')
 OR (OLD.state='uncertain' AND NOT (NEW.state='reconciled' OR (OLD.estimated_micro IS NULL AND OLD.input_tokens IS NULL AND OLD.output_tokens IS NULL AND NEW.state IN ('estimated','released','uncertain'))))
 OR (OLD.state IN ('estimated','released','reconciled') AND (NEW.estimated_micro IS NOT OLD.estimated_micro OR NEW.input_tokens IS NOT OLD.input_tokens OR NEW.output_tokens IS NOT OLD.output_tokens))
 OR (OLD.state='uncertain' AND (OLD.estimated_micro IS NOT NULL OR OLD.input_tokens IS NOT NULL OR OLD.output_tokens IS NOT NULL) AND (NEW.estimated_micro IS NOT OLD.estimated_micro OR NEW.input_tokens IS NOT OLD.input_tokens OR NEW.output_tokens IS NOT OLD.output_tokens))
 BEGIN SELECT RAISE(ABORT,'Model cost evidence and provenance are immutable'); END;
CREATE TRIGGER model_costs_no_delete BEFORE DELETE ON model_costs BEGIN SELECT RAISE(ABORT,'Model cost evidence is immutable'); END;
CREATE TRIGGER ops_dirty_model_costs_insert AFTER INSERT ON model_costs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;
CREATE TRIGGER ops_dirty_model_costs_update AFTER UPDATE ON model_costs BEGIN
 INSERT INTO ops_company_dirty(firm_id,company_id,dirty_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=ops_company_dirty.revision+1;
 INSERT INTO ops_company_refresh(firm_id,company_id,next_due_at) SELECT NEW.firm_id,NEW.company_id,strftime('%Y-%m-%dT%H:%M:%f+00:00','now') WHERE 1 ON CONFLICT(firm_id,company_id) DO NOTHING;
END;
