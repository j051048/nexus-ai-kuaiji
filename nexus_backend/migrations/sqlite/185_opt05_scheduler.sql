CREATE TABLE IF NOT EXISTS fsc_schedule_plans(
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT,
 kind TEXT NOT NULL CHECK(kind IN('monthly_open','periodic_accrual','tax_deadline')),
 rule_json TEXT NOT NULL,
 next_run_at TEXT NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>=1),
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fsc_schedule_plans_due
 ON fsc_schedule_plans(enabled,next_run_at,id);
CREATE INDEX IF NOT EXISTS idx_fsc_schedule_plans_firm
 ON fsc_schedule_plans(firm_id,company_id,id);

CREATE TABLE IF NOT EXISTS fsc_schedule_triggers(
 id TEXT PRIMARY KEY,
 plan_id TEXT NOT NULL REFERENCES fsc_schedule_plans(id) ON DELETE CASCADE,
 firm_id TEXT NOT NULL,
 company_id TEXT,
 kind TEXT NOT NULL,
 scheduled_for TEXT NOT NULL,
 dedupe_key TEXT NOT NULL UNIQUE,
 status TEXT NOT NULL CHECK(status IN('pending','confirmed','skipped','failed')),
 batch_id TEXT REFERENCES batch_operations(id),
 reminder_id TEXT,
 created_at TEXT NOT NULL,
 confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_fsc_schedule_triggers_plan
 ON fsc_schedule_triggers(plan_id,scheduled_for DESC,id);

CREATE TABLE IF NOT EXISTS fsc_schedule_reminders(
 id TEXT PRIMARY KEY,
 trigger_id TEXT NOT NULL UNIQUE REFERENCES fsc_schedule_triggers(id) ON DELETE CASCADE,
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
 ON fsc_schedule_reminders(firm_id,status,due_at,id);
