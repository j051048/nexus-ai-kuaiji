-- compatibility: incremental
CREATE UNIQUE INDEX IF NOT EXISTS idx_active_staff_assignment_once ON staff_access_assignments(firm_id,user_id,template,COALESCE(company_id,''),COALESCE(valid_from,''),COALESCE(valid_to,'')) WHERE state='active';
