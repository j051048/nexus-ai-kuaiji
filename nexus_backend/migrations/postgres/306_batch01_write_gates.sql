-- compatibility: incremental
-- UPG Batch 01: close company write serialization gaps found by the static
-- migration gate scanner. These tables already carry company_id and RLS; the
-- guard only serializes writes by company without changing row visibility.
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_export_receipts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_return_evidence
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_return_imports
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.closing_external_evidence
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fee_receivable_allocations
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fee_receivable_refunds
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_advertising_revenue_drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_company_profiles
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_evidence_policies
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_evidence_request_links
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_import_envelopes
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_industry_contracts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.lease_contracts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.lease_draft_bindings
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.monthly_external_evidence
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.oa_approval_snapshots
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.oa_material_requests
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.periodic_asset_vehicle_links
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.repair_package_contracts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.repair_package_usage
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.subject_aliases
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.sync_stub_events
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_basis_reviews
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_cost_batches
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_cost_sources
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_fee_batches
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_pilot_profiles
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_policy_items
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_source_bindings
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_source_contributions
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_source_files
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.transport_aliases
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.transport_assignments
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.transport_relations
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.transport_subjects
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.ai_route_decisions
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_export_artifacts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_return_row_handlers
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
