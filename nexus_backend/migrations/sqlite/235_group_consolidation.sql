-- compatibility: incremental
-- Batch 10-1.2: group related-party mapping and append-only consolidation eliminations.
-- Amounts are integer cents; eliminations never write into a statutory ledger or voucher.
CREATE TABLE IF NOT EXISTS group_related_parties(
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firms(id),
    company_id TEXT NOT NULL REFERENCES companies(id),
    party_key TEXT NOT NULL CHECK(length(trim(party_key)) BETWEEN 2 AND 120),
    party_name TEXT NOT NULL CHECK(length(trim(party_name)) BETWEEN 1 AND 120),
    counterparty_company_id TEXT NOT NULL REFERENCES companies(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','inactive')),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(firm_id,company_id,party_key),
    CHECK(counterparty_company_id<>company_id)
);

CREATE INDEX IF NOT EXISTS idx_group_related_parties_lookup
    ON group_related_parties(firm_id,company_id,status);

CREATE TABLE IF NOT EXISTS consolidation_eliminations(
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES firms(id),
    company_id TEXT NOT NULL REFERENCES companies(id),
    counterparty_company_id TEXT NOT NULL REFERENCES companies(id),
    period TEXT NOT NULL CHECK(length(period)=7 AND substr(period,5,1)='-' AND CAST(substr(period,6,2) AS INTEGER) BETWEEN 1 AND 12),
    kind TEXT NOT NULL CHECK(kind IN('internal_receivable','internal_payable','internal_revenue','internal_cost','internal_funding')),
    amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
    note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=500),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','approved','rejected')),
    state_version INTEGER NOT NULL DEFAULT 1 CHECK(state_version>0),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    reviewed_by TEXT REFERENCES users(id),
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT '' CHECK(length(review_note)<=500),
    CHECK(counterparty_company_id<>company_id),
    CHECK(reviewed_by IS NULL OR reviewed_by<>created_by),
    CHECK(
        (status='pending' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status IN('approved','rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_consolidation_eliminations_period
    ON consolidation_eliminations(firm_id,period,company_id,status);

CREATE TRIGGER IF NOT EXISTS consolidation_eliminations_no_delete
BEFORE DELETE ON consolidation_eliminations
BEGIN
    SELECT RAISE(ABORT,'consolidation eliminations are append-only');
END;

CREATE TRIGGER IF NOT EXISTS consolidation_eliminations_guard
BEFORE UPDATE ON consolidation_eliminations
WHEN OLD.status<>'pending'
    OR NEW.id<>OLD.id
    OR NEW.firm_id<>OLD.firm_id
    OR NEW.company_id<>OLD.company_id
    OR NEW.counterparty_company_id<>OLD.counterparty_company_id
    OR NEW.period<>OLD.period
    OR NEW.kind<>OLD.kind
    OR NEW.amount_cents<>OLD.amount_cents
    OR NEW.note<>OLD.note
    OR NEW.created_by<>OLD.created_by
    OR NEW.created_at<>OLD.created_at
    OR NEW.status='pending'
BEGIN
    SELECT RAISE(ABORT,'consolidation elimination content is frozen');
END;
