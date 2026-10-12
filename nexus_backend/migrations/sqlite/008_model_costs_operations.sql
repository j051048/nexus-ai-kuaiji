-- SQLite local accounting cost and operations integrity; access is checked by services.
CREATE TABLE model_company_policies (
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,quota_enabled INTEGER NOT NULL DEFAULT 0 CHECK(quota_enabled IN (0,1)),
 monthly_limit_micro INTEGER NOT NULL DEFAULT 0 CHECK(monthly_limit_micro BETWEEN 0 AND 999999999999990000),
 ocr_reserve_micro INTEGER NOT NULL DEFAULT 0 CHECK(ocr_reserve_micro BETWEEN 0 AND 999999999999990000),
 proposal_reserve_micro INTEGER NOT NULL DEFAULT 0 CHECK(proposal_reserve_micro BETWEEN 0 AND 999999999999990000),
 failure_threshold INTEGER NOT NULL DEFAULT 3 CHECK(failure_threshold BETWEEN 1 AND 1000),
 failure_window_minutes INTEGER NOT NULL DEFAULT 60 CHECK(failure_window_minutes BETWEEN 1 AND 10080),
 backlog_threshold INTEGER NOT NULL DEFAULT 10 CHECK(backlog_threshold BETWEEN 1 AND 10000),
 backlog_age_minutes INTEGER NOT NULL DEFAULT 60 CHECK(backlog_age_minutes BETWEEN 1 AND 43200),
 queue_wait_minutes INTEGER NOT NULL DEFAULT 10 CHECK(queue_wait_minutes BETWEEN 1 AND 10080),
 worker_stale_seconds INTEGER NOT NULL DEFAULT 90 CHECK(worker_stale_seconds BETWEEN 15 AND 3600),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),updated_by TEXT NOT NULL REFERENCES users(id),updated_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id),FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 CHECK(quota_enabled=0 OR (monthly_limit_micro>0 AND ocr_reserve_micro>0 AND proposal_reserve_micro>0))
);
CREATE UNIQUE INDEX idx_ai_run_cost_scope ON ai_runs(id,event_id,firm_id,company_id,kind,created_by);
CREATE TABLE model_costs (
 id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE CHECK(id=run_id),firm_id TEXT NOT NULL,company_id TEXT NOT NULL,event_id TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('ocr','proposal')),created_by TEXT NOT NULL REFERENCES users(id),
 period TEXT NOT NULL CHECK(length(period)=7 AND substr(period,5,1)='-' AND substr(period,1,4) GLOB '[0-9][0-9][0-9][0-9]' AND CAST(substr(period,6,2) AS INTEGER) BETWEEN 1 AND 12),provider TEXT NOT NULL,model TEXT NOT NULL,charge_token TEXT NOT NULL,
 pricing_json TEXT NOT NULL CHECK(json_valid(pricing_json) AND json_type(pricing_json)='object'),quota_enabled INTEGER NOT NULL CHECK(quota_enabled IN (0,1)),
 reserved_micro INTEGER NOT NULL CHECK(reserved_micro BETWEEN 0 AND 999999999999990000),
 estimated_micro INTEGER CHECK(estimated_micro BETWEEN 0 AND 999999999999990000),confirmed_micro INTEGER CHECK(confirmed_micro BETWEEN 0 AND 999999999999990000),
 input_tokens INTEGER CHECK(input_tokens BETWEEN 0 AND 10000000),output_tokens INTEGER CHECK(output_tokens BETWEEN 0 AND 10000000),
 state TEXT NOT NULL CHECK(state IN ('reserved','sent','estimated','uncertain','released','reconciled')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),created_at TEXT NOT NULL,sent_at TEXT,finished_at TEXT,
 overrun INTEGER NOT NULL DEFAULT 0 CHECK(overrun IN (0,1)),manual_reason TEXT NOT NULL DEFAULT '',manual_by TEXT REFERENCES users(id),
 FOREIGN KEY(run_id,event_id,firm_id,company_id,kind,created_by) REFERENCES ai_runs(id,event_id,firm_id,company_id,kind,created_by),
 UNIQUE(id,event_id,firm_id,company_id),
 CHECK((state IN ('reserved','sent') AND finished_at IS NULL) OR (state NOT IN ('reserved','sent') AND finished_at IS NOT NULL)),
 CHECK(state<>'estimated' OR estimated_micro IS NOT NULL),
 CHECK(state<>'reconciled' OR (confirmed_micro IS NOT NULL AND manual_by IS NOT NULL AND length(trim(manual_reason))>0))
);
CREATE INDEX idx_model_costs_month ON model_costs(firm_id,company_id,period,state);
CREATE TABLE ops_exception_snapshots (
 event_id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 blocking_count INTEGER NOT NULL CHECK(blocking_count BETWEEN 0 AND 10000),warning_count INTEGER NOT NULL CHECK(warning_count BETWEEN 0 AND 10000),
 oldest_opened_at TEXT,context_fingerprint TEXT NOT NULL,updated_at TEXT NOT NULL,
 FOREIGN KEY(event_id,firm_id,company_id) REFERENCES business_events(id,firm_id,company_id)
);
CREATE INDEX idx_ops_exception_company ON ops_exception_snapshots(firm_id,company_id,blocking_count);
CREATE TABLE ops_alerts (
 id TEXT PRIMARY KEY,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 code TEXT NOT NULL CHECK(code IN ('budget_exhausted','quota_overrun','usage_uncertain','task_failures','exception_backlog','queue_wait','worker_unavailable')),
 severity TEXT NOT NULL CHECK(severity IN ('warning','critical')),state TEXT NOT NULL CHECK(state IN ('open','acknowledged','resolved')),
 fingerprint TEXT NOT NULL,payload_json TEXT NOT NULL CHECK(json_valid(payload_json) AND json_type(payload_json)='object'),
 opened_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,resolved_at TEXT,version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 UNIQUE(firm_id,company_id,code),UNIQUE(id,firm_id,company_id),
 FOREIGN KEY(firm_id,company_id) REFERENCES firm_clients(firm_id,company_id),
 CHECK((state='resolved' AND resolved_at IS NOT NULL) OR (state<>'resolved' AND resolved_at IS NULL))
);
CREATE TABLE ops_alert_actions (
 id TEXT PRIMARY KEY,alert_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000),created_at TEXT NOT NULL,
 FOREIGN KEY(alert_id,firm_id,company_id) REFERENCES ops_alerts(id,firm_id,company_id)
);
CREATE TABLE worker_heartbeats (
 worker_id TEXT PRIMARY KEY,started_at TEXT NOT NULL,heartbeat_at TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','idle','stopped')),inflight_count INTEGER NOT NULL CHECK(inflight_count BETWEEN 0 AND 2),
 CHECK(state='active' OR inflight_count=0)
);

CREATE TRIGGER model_cost_identity BEFORE UPDATE ON model_costs
 WHEN NEW.id IS NOT OLD.id OR NEW.run_id IS NOT OLD.run_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id
 OR NEW.event_id IS NOT OLD.event_id OR NEW.kind IS NOT OLD.kind OR NEW.created_by IS NOT OLD.created_by OR NEW.period IS NOT OLD.period
 OR NEW.provider IS NOT OLD.provider OR NEW.model IS NOT OLD.model OR NEW.charge_token IS NOT OLD.charge_token OR NEW.pricing_json IS NOT OLD.pricing_json
 OR NEW.quota_enabled IS NOT OLD.quota_enabled OR NEW.reserved_micro IS NOT OLD.reserved_micro OR NEW.created_at IS NOT OLD.created_at
 OR NEW.version<>OLD.version+1 OR (OLD.sent_at IS NOT NULL AND NEW.sent_at IS NOT OLD.sent_at)
 OR (OLD.state IN ('estimated','uncertain','released','reconciled') AND NEW.state<>'reconciled')
 OR (OLD.state IN ('estimated','uncertain','released','reconciled') AND (NEW.estimated_micro IS NOT OLD.estimated_micro OR NEW.input_tokens IS NOT OLD.input_tokens OR NEW.output_tokens IS NOT OLD.output_tokens))
 BEGIN SELECT RAISE(ABORT,'Model cost evidence and provenance are immutable'); END;
CREATE TRIGGER model_costs_no_delete BEFORE DELETE ON model_costs BEGIN SELECT RAISE(ABORT,'Model cost evidence is immutable'); END;
CREATE TRIGGER ops_alert_actions_no_update BEFORE UPDATE ON ops_alert_actions BEGIN SELECT RAISE(ABORT,'Alert action history is immutable'); END;
CREATE TRIGGER ops_alert_actions_no_delete BEFORE DELETE ON ops_alert_actions BEGIN SELECT RAISE(ABORT,'Alert action history is immutable'); END;
CREATE TRIGGER model_policy_scope BEFORE UPDATE ON model_company_policies WHEN NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id BEGIN SELECT RAISE(ABORT,'Operations scope is immutable'); END;
CREATE TRIGGER ops_alert_scope BEFORE UPDATE ON ops_alerts WHEN NEW.id IS NOT OLD.id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id BEGIN SELECT RAISE(ABORT,'Operations scope is immutable'); END;
CREATE TRIGGER ops_snapshot_scope BEFORE UPDATE ON ops_exception_snapshots WHEN NEW.event_id IS NOT OLD.event_id OR NEW.firm_id IS NOT OLD.firm_id OR NEW.company_id IS NOT OLD.company_id BEGIN SELECT RAISE(ABORT,'Operations scope is immutable'); END;
