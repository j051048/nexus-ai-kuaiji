-- accounting_system owns the cross-firm scheduler scan; RLS still restricts rows.
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_schedule_plans TO accounting_system;
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_schedule_triggers TO accounting_system;
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_schedule_reminders TO accounting_system;
