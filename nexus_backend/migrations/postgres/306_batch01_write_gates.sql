-- compatibility: incremental
-- UPG Batch 01: close company write serialization gaps found by the static
-- migration gate scanner. These tables already carry company_id and RLS; the
-- guard only serializes writes by company without changing row visibility.
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.bridge_export_receipts;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_export_receipts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.bridge_return_evidence;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_return_evidence
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.bridge_return_imports;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_return_imports
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.closing_external_evidence;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.closing_external_evidence
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fee_receivable_allocations;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fee_receivable_allocations
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fee_receivable_refunds;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fee_receivable_refunds
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fsc_advertising_revenue_drafts;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_advertising_revenue_drafts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fsc_company_profiles;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_company_profiles
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fsc_evidence_policies;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_evidence_policies
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fsc_evidence_request_links;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_evidence_request_links
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fsc_import_envelopes;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_import_envelopes
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.fsc_industry_contracts;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.fsc_industry_contracts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.lease_contracts;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.lease_contracts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.lease_draft_bindings;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.lease_draft_bindings
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.monthly_external_evidence;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.monthly_external_evidence
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.oa_approval_snapshots;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.oa_approval_snapshots
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.oa_material_requests;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.oa_material_requests
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.periodic_asset_vehicle_links;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.periodic_asset_vehicle_links
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.repair_package_contracts;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.repair_package_contracts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.repair_package_usage;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.repair_package_usage
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.subject_aliases;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.subject_aliases
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.sync_stub_events;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.sync_stub_events
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_basis_reviews;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_basis_reviews
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_cost_batches;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_cost_batches
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_cost_sources;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_cost_sources
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_fee_batches;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_fee_batches
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_pilot_profiles;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_pilot_profiles
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_policy_items;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_policy_items
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_source_bindings;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_source_bindings
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_source_contributions;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_source_contributions
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.tax_source_files;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.tax_source_files
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.transport_aliases;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.transport_aliases
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.transport_assignments;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.transport_assignments
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.transport_relations;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.transport_relations
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.transport_subjects;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.transport_subjects
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.ai_route_decisions;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.ai_route_decisions
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.bridge_export_artifacts;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_export_artifacts
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
DROP TRIGGER IF EXISTS batch2_company_write_gate ON accounting.bridge_return_row_handlers;
CREATE TRIGGER batch2_company_write_gate
 BEFORE INSERT OR UPDATE OR DELETE ON accounting.bridge_return_row_handlers
 FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
