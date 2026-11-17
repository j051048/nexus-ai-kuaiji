CREATE UNIQUE INDEX IF NOT EXISTS fsc_industry_contracts_one_active
  ON fsc_industry_contracts(company_id, industry_code)
  WHERE status = 'active';
