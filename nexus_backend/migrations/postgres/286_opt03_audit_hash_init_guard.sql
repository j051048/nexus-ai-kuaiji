-- OPT03-B follow-up: PostgreSQL computes the hash in BEFORE INSERT, so there
-- is no two-step initialization update to relax. This forward migration keeps
-- SQLite and PostgreSQL migration numbering aligned.
SELECT 1;
