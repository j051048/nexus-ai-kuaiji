-- OPT06-C: explicit, time-bounded review delegation. The access function is
-- replaced so DB-enforced review evidence accepts a currently valid delegate,
-- while managers under separation remain unable to regain review through it.
CREATE TABLE IF NOT EXISTS accounting.book_review_delegations (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
    company_id TEXT NOT NULL REFERENCES accounting.companies(id),
    book_id TEXT NOT NULL REFERENCES accounting.books(id),
    duty TEXT NOT NULL CHECK (duty = 'reviewer'),
    from_user_id TEXT NOT NULL REFERENCES accounting.users(id),
    to_user_id TEXT NOT NULL REFERENCES accounting.users(id),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','active','expired','revoked')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 5 AND 500),
    requested_by TEXT NOT NULL REFERENCES accounting.users(id),
    approved_by TEXT REFERENCES accounting.users(id),
    approved_at TEXT,
    revoked_by TEXT REFERENCES accounting.users(id),
    revoked_at TEXT,
    revoke_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (book_id, to_user_id, starts_at)
);
CREATE INDEX IF NOT EXISTS idx_review_delegation_book
    ON accounting.book_review_delegations(book_id, to_user_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_review_delegation_firm
    ON accounting.book_review_delegations(firm_id, status, ends_at);

ALTER TABLE accounting.book_review_delegations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.book_review_delegations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS review_delegation_select ON accounting.book_review_delegations;
CREATE POLICY review_delegation_select ON accounting.book_review_delegations
    FOR SELECT USING (firm_id = accounting.actor_firm());
DROP POLICY IF EXISTS review_delegation_insert ON accounting.book_review_delegations;
CREATE POLICY review_delegation_insert ON accounting.book_review_delegations
    FOR INSERT WITH CHECK (
        firm_id = accounting.actor_firm()
        AND requested_by = accounting.actor_user()
    );
DROP POLICY IF EXISTS review_delegation_update ON accounting.book_review_delegations;
CREATE POLICY review_delegation_update ON accounting.book_review_delegations
    FOR UPDATE USING (firm_id = accounting.actor_firm())
    WITH CHECK (firm_id = accounting.actor_firm());

GRANT SELECT, INSERT, UPDATE ON accounting.book_review_delegations TO accounting_app;
GRANT SELECT ON accounting.book_review_delegations TO accounting_worker, accounting_system;

CREATE OR REPLACE FUNCTION accounting.book_review_allowed(target TEXT) RETURNS BOOLEAN
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
 SELECT accounting.book_allowed(target,true) AND (
   accounting.firm_manager(accounting.actor_firm())
   OR (accounting.firm_role(accounting.actor_firm())='reviewer' AND EXISTS(
     SELECT 1 FROM accounting.access_all_book_roles a
     WHERE a.book_id=target AND a.firm_id=accounting.actor_firm()
       AND a.user_id=accounting.actor_user() AND a.role='reviewer'))
   OR (
     NOT accounting.firm_manager(accounting.actor_firm())
     AND EXISTS(
       SELECT 1 FROM accounting.book_review_delegations d
       WHERE d.book_id=target AND d.firm_id=accounting.actor_firm()
         AND d.to_user_id=accounting.actor_user() AND d.status='active'
         AND d.starts_at::timestamptz <= now()
         AND d.ends_at::timestamptz > now()
     )
   )
 )
$$;
