-- TAX03: transport subjects (vehicles/drivers), effective relations and assignments.
CREATE TABLE accounting.transport_subjects (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('vehicle','driver')),
    external_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL REFERENCES accounting.users(id),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(firm_id, company_id, kind, external_id)
);
ALTER TABLE accounting.transport_subjects ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.transport_subjects FORCE ROW LEVEL SECURITY;
CREATE POLICY transport_subject_read ON accounting.transport_subjects FOR SELECT
    USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));
CREATE POLICY transport_subject_write ON accounting.transport_subjects FOR ALL
    USING (firm_id = accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id))
    WITH CHECK (firm_id = accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id));

CREATE TABLE accounting.transport_relations (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('vehicle','driver')),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('project','counterparty')),
    target_id TEXT NOT NULL,
    contract_external_id TEXT NOT NULL,
    operating_right TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','ended')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL REFERENCES accounting.users(id),
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CHECK ((subject_kind='vehicle' AND target_kind='project') OR (subject_kind='driver' AND target_kind='counterparty')),
    UNIQUE(subject_id, target_id, valid_from)
);
ALTER TABLE accounting.transport_relations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.transport_relations FORCE ROW LEVEL SECURITY;
CREATE POLICY transport_relation_read ON accounting.transport_relations FOR SELECT
    USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));
CREATE POLICY transport_relation_write ON accounting.transport_relations FOR ALL
    USING (firm_id = accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id))
    WITH CHECK (firm_id = accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id));

CREATE TABLE accounting.transport_assignments (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    driver_id TEXT NOT NULL,
    shift TEXT NOT NULL CHECK (shift IN ('day','night','full')),
    contract_external_id TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    created_by TEXT NOT NULL REFERENCES accounting.users(id),
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    UNIQUE(vehicle_id, driver_id, shift, valid_from)
);
ALTER TABLE accounting.transport_assignments ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.transport_assignments FORCE ROW LEVEL SECURITY;
CREATE POLICY transport_assignment_read ON accounting.transport_assignments FOR SELECT
    USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));
CREATE POLICY transport_assignment_write ON accounting.transport_assignments FOR ALL
    USING (firm_id = accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id))
    WITH CHECK (firm_id = accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id));

CREATE TABLE accounting.transport_aliases (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('approved','retired')),
    created_by TEXT NOT NULL REFERENCES accounting.users(id),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(firm_id, company_id, subject_id, alias)
);
ALTER TABLE accounting.transport_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.transport_aliases FORCE ROW LEVEL SECURITY;
CREATE POLICY transport_alias_read ON accounting.transport_aliases FOR SELECT
    USING (firm_id = accounting.actor_firm() AND accounting.company_visible(company_id));
CREATE POLICY transport_alias_write ON accounting.transport_aliases FOR ALL
    USING (firm_id = accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id))
    WITH CHECK (firm_id = accounting.actor_firm() AND accounting.firm_manager(accounting.actor_firm()) AND accounting.company_visible(company_id));

CREATE INDEX idx_transport_subject_scope ON accounting.transport_subjects(firm_id, company_id, kind, active);
CREATE INDEX idx_transport_relation_period ON accounting.transport_relations(subject_id, valid_from, valid_to, status);
CREATE INDEX idx_transport_assignment_period ON accounting.transport_assignments(vehicle_id, driver_id, valid_from, valid_to);

REVOKE ALL ON accounting.transport_subjects, accounting.transport_relations, accounting.transport_assignments, accounting.transport_aliases FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE ON accounting.transport_subjects, accounting.transport_relations, accounting.transport_assignments, accounting.transport_aliases TO accounting_app;
