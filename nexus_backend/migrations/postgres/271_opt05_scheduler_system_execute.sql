-- The scheduler's system scan evaluates the firm RLS policy while running as
-- accounting_system. The function already exists; grant only this predicate.
GRANT EXECUTE ON FUNCTION accounting.actor_firm() TO accounting_system;
