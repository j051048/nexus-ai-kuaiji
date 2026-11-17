-- FSC17: stable asset-to-vehicle association; vehicle plate is not asset identity.
CREATE TABLE accounting.periodic_asset_vehicle_links (
    id TEXT PRIMARY KEY,
    firm_id TEXT NOT NULL,
    company_id TEXT NOT NULL,
    book_id TEXT NOT NULL REFERENCES accounting.books(id),
    asset_id TEXT NOT NULL REFERENCES accounting.periodic_assets(id),
    vehicle_external_id TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK (source_kind = 'transport_subject'),
    source_version INTEGER NOT NULL CHECK (source_version > 0),
    source_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revoked')),
    linked_by TEXT NOT NULL REFERENCES accounting.users(id),
    linked_at TIMESTAMPTZ NOT NULL,
    revoked_by TEXT REFERENCES accounting.users(id),
    revoked_at TIMESTAMPTZ,
    revoke_reason TEXT,
    CHECK ((status='active' AND revoked_at IS NULL) OR (status='revoked' AND revoked_at IS NOT NULL AND revoke_reason IS NOT NULL))
);
CREATE UNIQUE INDEX uq_periodic_asset_vehicle_active ON accounting.periodic_asset_vehicle_links(asset_id) WHERE status='active';
CREATE INDEX idx_periodic_asset_vehicle_lookup ON accounting.periodic_asset_vehicle_links(company_id, vehicle_external_id, status);
ALTER TABLE accounting.periodic_asset_vehicle_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.periodic_asset_vehicle_links FORCE ROW LEVEL SECURITY;
CREATE POLICY periodic_asset_vehicle_company ON accounting.periodic_asset_vehicle_links
  USING (accounting.company_visible(company_id)) WITH CHECK (accounting.company_visible(company_id));
GRANT SELECT, INSERT, UPDATE ON accounting.periodic_asset_vehicle_links TO accounting_app;
