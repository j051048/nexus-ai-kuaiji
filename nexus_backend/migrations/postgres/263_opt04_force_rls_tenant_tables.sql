-- OPT04-A: new tenant tables must enforce RLS for the table owner as well.
-- Global reference/internal tables have no tenant column; they get an explicit
-- policy only after ENABLE+FORCE so the owner path stays auditable.

ALTER TABLE accounting.ai_route_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ai_route_decisions FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fee_receivable_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fee_receivable_items FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fee_receivable_allocations ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fee_receivable_allocations FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fee_receivable_refunds ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fee_receivable_refunds FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_company_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_company_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.lease_invoice_reds ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.lease_invoice_reds FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.repair_rate_cards ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.repair_rate_cards FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.oa_approval_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.oa_approval_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.oa_material_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.oa_material_requests FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.monthly_external_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.monthly_external_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.sync_stub_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.sync_stub_events FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_industry_contracts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_industry_contracts FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_import_envelopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_import_envelopes FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_evidence_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_evidence_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_advertising_revenue_drafts ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_advertising_revenue_drafts FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_evidence_request_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_evidence_request_links FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_industry_contract_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_industry_contract_events FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_import_envelope_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_import_envelope_events FORCE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_evidence_policy_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.fsc_evidence_policy_events FORCE ROW LEVEL SECURITY;

CREATE POLICY fsc_contract_events_company ON accounting.fsc_industry_contract_events
  FOR ALL TO PUBLIC
  USING (EXISTS (
    SELECT 1 FROM accounting.fsc_industry_contracts c
    WHERE c.id = contract_id AND accounting.company_visible(c.company_id)
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM accounting.fsc_industry_contracts c
    WHERE c.id = contract_id AND accounting.company_visible(c.company_id)
  ));

CREATE POLICY fsc_evidence_events_company ON accounting.fsc_evidence_policy_events
  FOR ALL TO PUBLIC
  USING (EXISTS (
    SELECT 1 FROM accounting.fsc_evidence_policies p
    WHERE p.id = policy_id AND accounting.company_visible(p.company_id)
  ))
  WITH CHECK (EXISTS (
    SELECT 1 FROM accounting.fsc_evidence_policies p
    WHERE p.id = policy_id AND accounting.company_visible(p.company_id)
  ));

-- Global configuration/reference tables have no tenant column. Their existing
-- access is explicit application-role access; keep that behavior visible under
-- FORCE instead of silently leaving owner bypass enabled.
ALTER TABLE accounting.ai_endpoint_capabilities ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ai_endpoint_capabilities FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_endpoint_capabilities_runtime ON accounting.ai_endpoint_capabilities
  FOR ALL TO PUBLIC USING (true) WITH CHECK (true);
ALTER TABLE accounting.ai_trusted_endpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.ai_trusted_endpoints FORCE ROW LEVEL SECURITY;
CREATE POLICY ai_trusted_endpoints_runtime ON accounting.ai_trusted_endpoints
  FOR ALL TO PUBLIC USING (true) WITH CHECK (true);
ALTER TABLE accounting.recovery_release_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.recovery_release_audit FORCE ROW LEVEL SECURITY;
CREATE POLICY recovery_release_audit_runtime ON accounting.recovery_release_audit
  FOR ALL TO PUBLIC USING (true) WITH CHECK (true);
ALTER TABLE accounting.report_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.report_templates FORCE ROW LEVEL SECURITY;
CREATE POLICY report_templates_runtime ON accounting.report_templates
  FOR ALL TO PUBLIC USING (true) WITH CHECK (true);
ALTER TABLE accounting.restore_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.restore_state FORCE ROW LEVEL SECURITY;
CREATE POLICY restore_state_runtime ON accounting.restore_state
  FOR ALL TO PUBLIC USING (true) WITH CHECK (true);
