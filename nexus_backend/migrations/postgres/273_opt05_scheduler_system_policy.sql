-- The scheduler scanner runs as accounting_system and must see every firm.
-- Application roles remain constrained by the firm-scoped policies.
CREATE POLICY fsc_schedule_plans_system ON accounting.fsc_schedule_plans
 FOR ALL TO accounting_system USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY fsc_schedule_triggers_system ON accounting.fsc_schedule_triggers
 FOR ALL TO accounting_system USING (TRUE) WITH CHECK (TRUE);
CREATE POLICY fsc_schedule_reminders_system ON accounting.fsc_schedule_reminders
 FOR ALL TO accounting_system USING (TRUE) WITH CHECK (TRUE);
