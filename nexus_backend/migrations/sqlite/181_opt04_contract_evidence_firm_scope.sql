-- OPT04-B: contract/evidence version streams and active uniqueness are per firm.
DROP INDEX IF EXISTS idx_fsc_contract_company;
DROP INDEX IF EXISTS fsc_industry_contracts_one_active;
DROP TRIGGER IF EXISTS fsc_contract_content_immutable;
DROP TRIGGER IF EXISTS fsc_contract_delete_guard;
ALTER TABLE fsc_industry_contracts RENAME TO fsc_industry_contracts_legacy;
CREATE TABLE fsc_industry_contracts (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 industry_code TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),
 status TEXT NOT NULL CHECK(status IN ('draft','confirmed','active','superseded','inactive')),
 processor_code TEXT NOT NULL,
 capabilities_json TEXT NOT NULL CHECK(json_valid(capabilities_json) AND json_type(capabilities_json)='array'),
 source_contracts_json TEXT NOT NULL CHECK(json_valid(source_contracts_json) AND json_type(source_contracts_json)='array'),
 evidence_policy_json TEXT NOT NULL CHECK(json_valid(evidence_policy_json) AND json_type(evidence_policy_json)='object'),
 fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 confirmed_by TEXT,
 confirmed_at TEXT,
 activated_by TEXT,
 activated_at TEXT,
 superseded_at TEXT,
 UNIQUE(firm_id, company_id, industry_code, version)
);
INSERT INTO fsc_industry_contracts(
 id,firm_id,company_id,industry_code,version,status,processor_code,capabilities_json,
 source_contracts_json,evidence_policy_json,fingerprint,created_by,created_at,
 confirmed_by,confirmed_at,activated_by,activated_at,superseded_at)
SELECT id,firm_id,company_id,industry_code,version,status,processor_code,capabilities_json,
 source_contracts_json,evidence_policy_json,fingerprint,created_by,created_at,
 confirmed_by,confirmed_at,activated_by,activated_at,superseded_at
FROM fsc_industry_contracts_legacy;
DROP TABLE fsc_industry_contracts_legacy;
CREATE INDEX idx_fsc_contract_company
 ON fsc_industry_contracts(firm_id, company_id, status, industry_code);
CREATE UNIQUE INDEX fsc_industry_contracts_one_active
 ON fsc_industry_contracts(firm_id, company_id, industry_code) WHERE status='active';
CREATE TRIGGER fsc_contract_content_immutable
BEFORE UPDATE ON fsc_industry_contracts
WHEN OLD.status<>'draft' AND (
 NEW.company_id<>OLD.company_id OR NEW.firm_id<>OLD.firm_id OR NEW.industry_code<>OLD.industry_code
 OR NEW.version<>OLD.version OR NEW.processor_code<>OLD.processor_code
 OR NEW.capabilities_json<>OLD.capabilities_json OR NEW.source_contracts_json<>OLD.source_contracts_json
 OR NEW.evidence_policy_json<>OLD.evidence_policy_json OR NEW.fingerprint<>OLD.fingerprint)
BEGIN SELECT RAISE(ABORT,'confirmed FSC industry contract is immutable'); END;
CREATE TRIGGER fsc_contract_delete_guard
BEFORE DELETE ON fsc_industry_contracts
BEGIN SELECT RAISE(ABORT,'FSC industry contracts are not deleted'); END;

DROP INDEX IF EXISTS idx_fsc_evidence_policies_scope;
DROP INDEX IF EXISTS fsc_evidence_policies_one_active;
DROP TRIGGER IF EXISTS fsc_evidence_policy_immutable;
ALTER TABLE fsc_evidence_policies RENAME TO fsc_evidence_policies_legacy;
CREATE TABLE fsc_evidence_policies (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 industry_code TEXT NOT NULL,
 version INTEGER NOT NULL,
 status TEXT NOT NULL CHECK (status IN ('draft','confirmed','active','superseded','inactive')),
 requirements_json TEXT NOT NULL,
 exceptions_json TEXT NOT NULL,
 fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL,
 created_at TEXT NOT NULL,
 confirmed_by TEXT,
 confirmed_at TEXT,
 activated_by TEXT,
 activated_at TEXT,
 superseded_at TEXT,
 UNIQUE(firm_id, company_id, industry_code, version)
);
INSERT INTO fsc_evidence_policies(
 id,firm_id,company_id,industry_code,version,status,requirements_json,exceptions_json,
 fingerprint,created_by,created_at,confirmed_by,confirmed_at,activated_by,activated_at,superseded_at)
SELECT id,firm_id,company_id,industry_code,version,status,requirements_json,exceptions_json,
 fingerprint,created_by,created_at,confirmed_by,confirmed_at,activated_by,activated_at,superseded_at
FROM fsc_evidence_policies_legacy;
DROP TABLE fsc_evidence_policies_legacy;
CREATE INDEX idx_fsc_evidence_policies_scope
 ON fsc_evidence_policies(firm_id, company_id, industry_code, status);
CREATE UNIQUE INDEX fsc_evidence_policies_one_active
 ON fsc_evidence_policies(firm_id, company_id, industry_code) WHERE status='active';
CREATE TRIGGER fsc_evidence_policy_immutable
BEFORE UPDATE ON fsc_evidence_policies
WHEN OLD.status <> 'draft' AND (
 NEW.requirements_json <> OLD.requirements_json OR NEW.exceptions_json <> OLD.exceptions_json
 OR NEW.industry_code <> OLD.industry_code OR NEW.version <> OLD.version)
BEGIN SELECT RAISE(ABORT, 'confirmed evidence policy is immutable'); END;
