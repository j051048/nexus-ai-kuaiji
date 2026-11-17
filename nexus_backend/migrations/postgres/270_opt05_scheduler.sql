CREATE TABLE IF NOT EXISTS accounting.fsc_schedule_plans(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN('monthly_open','periodic_accrual','tax_deadline')),
 rule_json TEXT NOT NULL,
 next_run_at TEXT NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT TRUE,
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fsc_schedule_plans_due
 ON accounting.fsc_schedule_plans(enabled,next_run_at,id);
CREATE INDEX IF NOT EXISTS idx_fsc_schedule_plans_firm
 ON accounting.fsc_schedule_plans(firm_id,company_id,id);

CREATE TABLE IF NOT EXISTS accounting.fsc_schedule_triggers(
 id TEXT PRIMARY KEY,
 plan_id TEXT NOT NULL REFERENCES accounting.fsc_schedule_plans(id) ON DELETE CASCADE,
 firm_id TEXT NOT NULL,
 company_id TEXT,
 kind TEXT NOT NULL,
 scheduled_for TEXT NOT NULL,
 dedupe_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK(status IN('pending','confirmed','skipped','failed')),
 batch_id TEXT REFERENCES accounting.batch_operations(id),
 reminder_id TEXT,
 created_at TEXT NOT NULL,
 confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fsc_schedule_triggers_plan
 ON accounting.fsc_schedule_triggers(plan_id,scheduled_for DESC,id);

CREATE TABLE IF NOT EXISTS accounting.fsc_schedule_reminders(
 id TEXT PRIMARY KEY,
 trigger_id TEXT NOT NULL UNIQUE REFERENCES accounting.fsc_schedule_triggers(id) ON DELETE CASCADE,
 firm_id TEXT NOT NULL,
 company_id TEXT,
 kind TEXT NOT NULL,
 title TEXT NOT NULL,
 detail TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN('open','confirmed','dismissed')),
 due_at TEXT NOT NULL,
 created_at TEXT NOT NULL,
 confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fsc_schedule_reminders_firm
 ON accounting.fsc_schedule_reminders(firm_id,status,due_at,id);

ALTER TABLE accounting.fsc_schedule_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_schedule_plans FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_schedule_triggers ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_schedule_triggers FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_schedule_reminders ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_schedule_reminders FORCE ROW LEVEL SECURITY;
CREATE POLICY fsc_schedule_plans_firm ON accounting.fsc_schedule_plans
 USING (firm_id=accounting.actor_firm()) WITH CHECK (firm_id=accounting.actor_firm());
CREATE POLICY fsc_schedule_triggers_firm ON accounting.fsc_schedule_triggers
 USING (firm_id=accounting.actor_firm()) WITH CHECK (firm_id=accounting.actor_firm());
CREATE POLICY fsc_schedule_reminders_firm ON accounting.fsc_schedule_reminders
 USING (firm_id=accounting.actor_firm()) WITH CHECK (firm_id=accounting.actor_firm());
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_schedule_plans TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_schedule_triggers TO accounting_app;
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_schedule_reminders TO accounting_app;
