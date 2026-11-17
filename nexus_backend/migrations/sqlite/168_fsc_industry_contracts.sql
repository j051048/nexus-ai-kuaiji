-- FSC04: versioned, reviewable industry capability contracts.
CREATE TABLE IF NOT EXISTS fsc_industry_contracts (
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
 UNIQUE(company_id, industry_code, version)
);
CREATE INDEX IF NOT EXISTS idx_fsc_contract_company
 ON fsc_industry_contracts(firm_id, company_id, status, industry_code);
CREATE TABLE IF NOT EXISTS fsc_industry_contract_events (
 id TEXT PRIMARY KEY,
 contract_id TEXT NOT NULL,
 event TEXT NOT NULL,
 actor_id TEXT NOT NULL,
 at TEXT NOT NULL,
 detail_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(detail_json))
);
CREATE TRIGGER IF NOT EXISTS fsc_contract_content_immutable
BEFORE UPDATE ON fsc_industry_contracts
WHEN OLD.status<>'draft' AND (
 NEW.company_id<>OLD.company_id OR NEW.firm_id<>OLD.firm_id OR NEW.industry_code<>OLD.industry_code
 OR NEW.version<>OLD.version OR NEW.processor_code<>OLD.processor_code
 OR NEW.capabilities_json<>OLD.capabilities_json OR NEW.source_contracts_json<>OLD.source_contracts_json
 OR NEW.evidence_policy_json<>OLD.evidence_policy_json OR NEW.fingerprint<>OLD.fingerprint)
BEGIN SELECT RAISE(ABORT,'confirmed FSC industry contract is immutable'); END;
CREATE TRIGGER IF NOT EXISTS fsc_contract_delete_guard
BEFORE DELETE ON fsc_industry_contracts
BEGIN SELECT RAISE(ABORT,'FSC industry contracts are not deleted'); END;
