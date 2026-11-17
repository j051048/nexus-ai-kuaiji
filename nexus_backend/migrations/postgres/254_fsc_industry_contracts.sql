CREATE TABLE IF NOT EXISTS accounting.fsc_industry_contracts (
 id TEXT PRIMARY KEY,
 firm_id TEXT NOT NULL,
 company_id TEXT NOT NULL,
 industry_code TEXT NOT NULL,
 version INTEGER NOT NULL CHECK(version>0),
 status TEXT NOT NULL CHECK(status IN ('draft','confirmed','active','superseded','inactive')),
 processor_code TEXT NOT NULL,
 capabilities_json JSONB NOT NULL,
 source_contracts_json JSONB NOT NULL,
 evidence_policy_json JSONB NOT NULL,
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
 ON accounting.fsc_industry_contracts(firm_id, company_id, status, industry_code);
CREATE TABLE IF NOT EXISTS accounting.fsc_industry_contract_events (
 id TEXT PRIMARY KEY,
 contract_id TEXT NOT NULL,
 event TEXT NOT NULL,
 actor_id TEXT NOT NULL,
 at TEXT NOT NULL,
 detail_json JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE OR REPLACE FUNCTION accounting.fsc_contract_content_immutable() RETURNS trigger AS $$
BEGIN
 IF OLD.status <> 'draft' AND (
   NEW.company_id<>OLD.company_id OR NEW.firm_id<>OLD.firm_id OR NEW.industry_code<>OLD.industry_code
   OR NEW.version<>OLD.version OR NEW.processor_code<>OLD.processor_code
   OR NEW.capabilities_json<>OLD.capabilities_json OR NEW.source_contracts_json<>OLD.source_contracts_json
   OR NEW.evidence_policy_json<>OLD.evidence_policy_json OR NEW.fingerprint<>OLD.fingerprint) THEN
   RAISE EXCEPTION 'confirmed FSC industry contract is immutable';
 END IF;
 RETURN NEW;
END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS fsc_contract_content_immutable ON accounting.fsc_industry_contracts;
CREATE TRIGGER fsc_contract_content_immutable BEFORE UPDATE ON accounting.fsc_industry_contracts
FOR EACH ROW EXECUTE FUNCTION accounting.fsc_contract_content_immutable();
ALTER TABLE accounting.fsc_industry_contracts ENABLE ROW LEVEL SECURITY;
CREATE POLICY fsc_contracts_company ON accounting.fsc_industry_contracts
 FOR ALL USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT,INSERT,UPDATE ON accounting.fsc_industry_contracts TO accounting_app;
GRANT SELECT,INSERT ON accounting.fsc_industry_contract_events TO accounting_app;
