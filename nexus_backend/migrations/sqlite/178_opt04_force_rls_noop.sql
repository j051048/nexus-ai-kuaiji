-- OPT04-A applies PostgreSQL RLS only. SQLite has no row-level security; the
-- forward migration is intentionally a no-op so both migration streams remain
-- aligned without touching historical SQL.
SELECT 1;
