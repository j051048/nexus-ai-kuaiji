-- OPT04-B: one company may be served by several firms; profile versions are per firm.
DROP INDEX IF EXISTS accounting.idx_admin_single_servicing_firm;
ALTER TABLE accounting.fsc_company_profiles
  DROP CONSTRAINT IF EXISTS fsc_company_profiles_company_id_version_key;
ALTER TABLE accounting.fsc_company_profiles
  ADD CONSTRAINT fsc_company_profiles_firm_company_version_key UNIQUE(firm_id,company_id,version);
