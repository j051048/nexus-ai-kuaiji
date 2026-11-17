-- OPT06-C: time-bounded review delegation. Expiry is evaluated on every
-- authorization read; the stored status is only an audit/readability aid.
CREATE TABLE IF NOT EXISTS book_review_delegations (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firms(id),
    company_id TEXT NOT NULL REFERENCES companies(id),
    book_id TEXT NOT NULL REFERENCES books(id),
    duty TEXT NOT NULL CHECK (duty = 'reviewer'),
    from_user_id TEXT NOT NULL REFERENCES users(id),
    to_user_id TEXT NOT NULL REFERENCES users(id),
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','active','expired','revoked')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 5 AND 500),
    requested_by TEXT NOT NULL REFERENCES users(id),
    approved_by TEXT REFERENCES users(id),
    approved_at TEXT,
    revoked_by TEXT REFERENCES users(id),
    revoked_at TEXT,
    revoke_reason TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (book_id, to_user_id, starts_at)
);
CREATE INDEX IF NOT EXISTS idx_review_delegation_book
    ON book_review_delegations(book_id, to_user_id, status, starts_at, ends_at);
CREATE INDEX IF NOT EXISTS idx_review_delegation_firm
    ON book_review_delegations(firm_id, status, ends_at);

-- SQLite triggers also authorize through this view. Recreate it so a currently
-- valid delegation can satisfy can_review without granting edit, export or
-- management.
DROP VIEW IF EXISTS service_book_user_permissions;
CREATE VIEW service_book_user_permissions AS
 SELECT b.id AS book_id,u.id AS user_id,
 (m.role IN('owner','admin') OR ba.role IN('editor','reviewer') AND (m.role IN('operator','reviewer') OR m.role IS NULL AND cm.role IN('owner','admin','finance'))) AS can_edit,
 (m.role IN('owner','admin') OR ba.role='reviewer' AND (m.role='reviewer' OR m.role IS NULL AND cm.role='finance')
  OR EXISTS(SELECT 1 FROM book_review_delegations d
            WHERE d.book_id=b.id AND d.firm_id=b.firm_id AND d.to_user_id=u.id
              AND d.status='active' AND datetime(d.starts_at)<=datetime('now')
              AND datetime(d.ends_at)>datetime('now'))) AS can_review,
 (cm.user_id IS NOT NULL) AS can_contact
 FROM books b JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 JOIN users u ON u.active=1 LEFT JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=u.id
 LEFT JOIN company_memberships cm ON cm.company_id=b.company_id AND cm.user_id=u.id
 LEFT JOIN book_access ba ON ba.book_id=b.id AND ba.firm_id=b.firm_id AND ba.user_id=u.id;
