-- compatibility: incremental
-- Batch 10-1.2: group related-party mapping and append-only consolidation eliminations.
-- Every amount is an integer number of cents. Eliminations never touch a statutory ledger.
SET LOCAL search_path=accounting,pg_catalog;

CREATE TABLE IF NOT EXISTS accounting.group_related_parties (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
    company_id TEXT NOT NULL REFERENCES accounting.companies(id),
    party_key TEXT NOT NULL CHECK (length(trim(party_key)) BETWEEN 2 AND 120),
    party_name TEXT NOT NULL CHECK (length(trim(party_name)) BETWEEN 1 AND 120),
    counterparty_company_id TEXT NOT NULL REFERENCES accounting.companies(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_by TEXT NOT NULL REFERENCES accounting.users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (firm_id, company_id, party_key),
    CHECK (counterparty_company_id <> company_id)
);

CREATE INDEX IF NOT EXISTS idx_group_related_parties_lookup
    ON accounting.group_related_parties (firm_id, company_id, status);

CREATE TABLE IF NOT EXISTS accounting.consolidation_eliminations (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
    company_id TEXT NOT NULL REFERENCES accounting.companies(id),
    counterparty_company_id TEXT NOT NULL REFERENCES accounting.companies(id),
    period TEXT NOT NULL CHECK (period ~ '^20[0-9]{2}-(0[1-9]|1[0-2])$'),
    kind TEXT NOT NULL CHECK (kind IN (
        'internal_receivable', 'internal_payable', 'internal_revenue',
        'internal_cost', 'internal_funding'
    )),
    amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
    note TEXT NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    state_version INTEGER NOT NULL DEFAULT 1 CHECK (state_version > 0),
    created_by TEXT NOT NULL REFERENCES accounting.users(id),
    created_at TEXT NOT NULL,
    reviewed_by TEXT REFERENCES accounting.users(id),
    reviewed_at TEXT,
    review_note TEXT NOT NULL DEFAULT '' CHECK (length(review_note) <= 500),
    CHECK (counterparty_company_id <> company_id),
    CHECK (reviewed_by IS NULL OR reviewed_by <> created_by),
    CHECK (
        (status = 'pending' AND reviewed_by IS NULL AND reviewed_at IS NULL)
        OR (status IN ('approved', 'rejected') AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_consolidation_eliminations_period
    ON accounting.consolidation_eliminations (firm_id, period, company_id, status);

ALTER TABLE accounting.group_related_parties ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.group_related_parties FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.consolidation_eliminations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.consolidation_eliminations FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS group_related_parties_read ON accounting.group_related_parties;
CREATE POLICY group_related_parties_read ON accounting.group_related_parties
    FOR SELECT TO accounting_app
    USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));

DROP POLICY IF EXISTS group_related_parties_insert ON accounting.group_related_parties;
CREATE POLICY group_related_parties_insert ON accounting.group_related_parties
    FOR INSERT TO accounting_app
    WITH CHECK (
        firm_id = accounting.actor_firm()
        AND accounting.company_visible(company_id)
        AND accounting.company_visible(counterparty_company_id)
        AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
        AND created_by = accounting.actor_user()
    );

DROP POLICY IF EXISTS group_related_parties_update ON accounting.group_related_parties;
CREATE POLICY group_related_parties_update ON accounting.group_related_parties
    FOR UPDATE TO accounting_app
    USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id))
    WITH CHECK (
        firm_id = accounting.actor_firm()
        AND accounting.company_visible(company_id)
        AND accounting.company_visible(counterparty_company_id)
        AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
    );

DROP POLICY IF EXISTS consolidation_eliminations_read ON accounting.consolidation_eliminations;
CREATE POLICY consolidation_eliminations_read ON accounting.consolidation_eliminations
    FOR SELECT TO accounting_app
    USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));

DROP POLICY IF EXISTS consolidation_eliminations_insert ON accounting.consolidation_eliminations;
CREATE POLICY consolidation_eliminations_insert ON accounting.consolidation_eliminations
    FOR INSERT TO accounting_app
    WITH CHECK (
        firm_id = accounting.actor_firm()
        AND accounting.company_visible(company_id)
        AND accounting.company_visible(counterparty_company_id)
        AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
        AND created_by = accounting.actor_user()
        AND status = 'pending'
        AND reviewed_by IS NULL
    );

DROP POLICY IF EXISTS consolidation_eliminations_review ON accounting.consolidation_eliminations;
CREATE POLICY consolidation_eliminations_review ON accounting.consolidation_eliminations
    FOR UPDATE TO accounting_app
    USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id))
    WITH CHECK (
        firm_id = accounting.actor_firm()
        AND accounting.company_visible(company_id)
        AND accounting.company_visible(counterparty_company_id)
        AND (accounting.firm_manager(firm_id) OR accounting.company_manager(company_id))
        AND reviewed_by = accounting.actor_user()
        AND reviewed_by <> created_by
    );

REVOKE ALL ON accounting.group_related_parties FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
REVOKE ALL ON accounting.consolidation_eliminations FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
GRANT SELECT, INSERT, UPDATE ON accounting.group_related_parties TO accounting_app;
GRANT SELECT, INSERT, UPDATE ON accounting.consolidation_eliminations TO accounting_app;

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.group_related_parties;
CREATE TRIGGER batch2_company_write_gate
    BEFORE INSERT OR UPDATE OR DELETE ON accounting.group_related_parties
    FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();

DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.consolidation_eliminations;
CREATE TRIGGER batch2_company_write_gate
    BEFORE INSERT OR UPDATE OR DELETE ON accounting.consolidation_eliminations
    FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();

CREATE OR REPLACE FUNCTION accounting.group_related_parties_no_delete()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=accounting,pg_catalog
AS $$
BEGIN
    RAISE EXCEPTION 'group related parties cannot be deleted; mark inactive instead'
        USING ERRCODE='55000';
END;
$$;
REVOKE ALL ON FUNCTION accounting.group_related_parties_no_delete()
    FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;
DROP TRIGGER IF EXISTS group_related_parties_no_delete ON accounting.group_related_parties;
CREATE TRIGGER group_related_parties_no_delete
    BEFORE DELETE ON accounting.group_related_parties
    FOR EACH ROW EXECUTE FUNCTION accounting.group_related_parties_no_delete();

CREATE OR REPLACE FUNCTION accounting.consolidation_elimination_guard()
RETURNS trigger
LANGUAGE plpgsql
SET search_path=accounting,pg_catalog
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'consolidation eliminations are append-only'
            USING ERRCODE='55000';
    END IF;
    IF OLD.id <> NEW.id
        OR OLD.firm_id <> NEW.firm_id
        OR OLD.company_id <> NEW.company_id
        OR OLD.counterparty_company_id <> NEW.counterparty_company_id
        OR OLD.period <> NEW.period
        OR OLD.kind <> NEW.kind
        OR OLD.amount_cents <> NEW.amount_cents
        OR OLD.note <> NEW.note
        OR OLD.created_by <> NEW.created_by
        OR OLD.created_at <> NEW.created_at THEN
        RAISE EXCEPTION 'consolidation elimination content is frozen'
            USING ERRCODE='55000';
    END IF;
    IF OLD.status <> 'pending' THEN
        RAISE EXCEPTION 'consolidation elimination has already been reviewed'
            USING ERRCODE='55000';
    END IF;
    RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION accounting.consolidation_elimination_guard()
    FROM PUBLIC, accounting_worker, accounting_system, accounting_projection;

DROP TRIGGER IF EXISTS consolidation_eliminations_guard ON consolidation_eliminations;
CREATE TRIGGER consolidation_eliminations_guard
    BEFORE UPDATE OR DELETE ON consolidation_eliminations
    FOR EACH ROW EXECUTE FUNCTION accounting.consolidation_elimination_guard();
