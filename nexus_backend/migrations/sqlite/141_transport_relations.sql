-- TAX03: transport subjects (vehicles/drivers), effective relations and assignments.
CREATE TABLE transport_subjects (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('vehicle','driver')),
    external_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE(firm_id, company_id, kind, external_id)
);

CREATE TABLE transport_relations (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('vehicle','driver')),
    target_kind TEXT NOT NULL CHECK (target_kind IN ('project','counterparty')),
    target_id TEXT NOT NULL,
    contract_external_id TEXT NOT NULL,
    operating_right TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','ended')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CHECK ((subject_kind='vehicle' AND target_kind='project') OR (subject_kind='driver' AND target_kind='counterparty')),
    UNIQUE(subject_id, target_id, valid_from)
);

CREATE TABLE transport_assignments (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    vehicle_id TEXT NOT NULL,
    driver_id TEXT NOT NULL,
    shift TEXT NOT NULL CHECK (shift IN ('day','night','full')),
    contract_external_id TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    UNIQUE(vehicle_id, driver_id, shift, valid_from)
);

CREATE TABLE transport_aliases (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    source TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('approved','retired')),
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE(firm_id, company_id, subject_id, alias)
);

CREATE INDEX idx_transport_subject_scope ON transport_subjects(firm_id, company_id, kind, active);
CREATE INDEX idx_transport_relation_period ON transport_relations(subject_id, valid_from, valid_to, status);
CREATE INDEX idx_transport_assignment_period ON transport_assignments(vehicle_id, driver_id, valid_from, valid_to);

CREATE TRIGGER transport_subject_update BEFORE UPDATE ON transport_subjects BEGIN
  SELECT CASE WHEN NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id
    OR NEW.kind<>OLD.kind OR NEW.external_id<>OLD.external_id OR NEW.created_by<>OLD.created_by
    OR NEW.created_at<>OLD.created_at OR NEW.version<>OLD.version+1
  THEN RAISE(ABORT,'transport subject identity or version is immutable') END;
END;
CREATE TRIGGER transport_subject_delete BEFORE DELETE ON transport_subjects BEGIN
  SELECT RAISE(ABORT,'transport subject history is immutable');
END;
CREATE TRIGGER transport_relation_update BEFORE UPDATE ON transport_relations BEGIN
  SELECT CASE WHEN NEW.id<>OLD.id OR NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id
    OR NEW.subject_id<>OLD.subject_id OR NEW.target_id<>OLD.target_id
    OR NEW.valid_from<>OLD.valid_from OR NEW.created_by<>OLD.created_by OR NEW.created_at<>OLD.created_at
    OR NEW.version<>OLD.version+1
  THEN RAISE(ABORT,'transport relation identity is immutable') END;
END;
CREATE TRIGGER transport_assignment_delete BEFORE DELETE ON transport_assignments BEGIN
  SELECT RAISE(ABORT,'transport assignment history is immutable');
END;
