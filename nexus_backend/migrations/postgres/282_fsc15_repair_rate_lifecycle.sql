-- FSC15: rate-card version lifecycle. Historical rows become active v1.
ALTER TABLE accounting.repair_rate_cards ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE accounting.repair_rate_cards ADD COLUMN status TEXT NOT NULL DEFAULT 'active'
  CHECK(status IN ('draft','confirmed','active','superseded','inactive'));
ALTER TABLE accounting.repair_rate_cards ADD COLUMN confirmed_by TEXT;
ALTER TABLE accounting.repair_rate_cards ADD COLUMN confirmed_at TEXT;
ALTER TABLE accounting.repair_rate_cards ADD COLUMN activated_at TEXT;
ALTER TABLE accounting.repair_rate_cards ADD COLUMN superseded_at TEXT;
ALTER TABLE accounting.repair_rate_cards ADD COLUMN reason TEXT NOT NULL DEFAULT '';
ALTER TABLE accounting.repair_rate_cards ADD COLUMN unit TEXT NOT NULL DEFAULT 'per_vehicle';
ALTER TABLE accounting.repair_rate_cards ADD COLUMN currency TEXT NOT NULL DEFAULT 'CNY';
ALTER TABLE accounting.repair_rate_cards ADD COLUMN tax_included BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE accounting.repair_rate_cards ADD COLUMN logical_id TEXT;
ALTER TABLE accounting.repair_rate_cards ADD COLUMN fingerprint TEXT NOT NULL DEFAULT '';
UPDATE accounting.repair_rate_cards SET logical_id=id WHERE logical_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_repair_rate_cards_logical
  ON accounting.repair_rate_cards(book_id,logical_id,version);
