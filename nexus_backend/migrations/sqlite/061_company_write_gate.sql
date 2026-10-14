-- SQLite keeps BEGIN IMMEDIATE. Scoped PostgreSQL advisory gates require no
-- substitute process-local lock or weakened SQLite transaction semantics.
SELECT 1;
