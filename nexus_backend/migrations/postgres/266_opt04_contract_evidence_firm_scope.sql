-- OPT04-B: contract/evidence version streams and active uniqueness are per firm.
ALTER TABLE accounting.fsc_industry_contracts
  DROP CONSTRAINT IF EXISTS fsc_industry_contracts_company_id_industry_code_version_key;
ALTER TABLE accounting.fsc_industry_contracts
  ADD CONSTRAINT fsc_industry_contracts_firm_company_industry_version_key
  UNIQUE(firm_id, company_id, industry_code, version);
DROP INDEX IF EXISTS accounting.fsc_industry_contracts_one_active;
CREATE UNIQUE INDEX fsc_industry_contracts_one_active
  ON accounting.fsc_industry_contracts(firm_id, company_id, industry_code) WHERE status='active';

ALTER TABLE accounting.fsc_evidence_policies
  DROP CONSTRAINT IF EXISTS fsc_evidence_policies_company_id_industry_code_version_key;
ALTER TABLE accounting.fsc_evidence_policies
  ADD CONSTRAINT fsc_evidence_policies_firm_company_industry_version_key
  UNIQUE(firm_id, company_id, industry_code, version);
DROP INDEX IF EXISTS accounting.fsc_evidence_policies_one_active;
CREATE UNIQUE INDEX fsc_evidence_policies_one_active
  ON accounting.fsc_evidence_policies(firm_id, company_id, industry_code) WHERE status='active';
