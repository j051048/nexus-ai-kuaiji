-- compatibility: incremental
-- NEXT04's guarded notification adapter was missing from the table allowlist.
-- Keep all RLS, manager, source binding and append-only triggers unchanged.
ALTER TABLE accounting.handoff_tasks
 DROP CONSTRAINT IF EXISTS handoff_tasks_source_kind_check;
ALTER TABLE accounting.handoff_tasks
 ADD CONSTRAINT handoff_tasks_source_kind_check
 CHECK(source_kind IN ('tax_workpaper','delivery_package','batch_item','notification_investigation'));
