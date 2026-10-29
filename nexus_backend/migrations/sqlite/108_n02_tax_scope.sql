-- compatibility: breaking
-- N02 local structural backstops. SQLite has no PostgreSQL RLS/roles; service
-- authorization remains mandatory. Legacy empty snapshots cannot be reviewed.
ALTER TABLE tax_workpapers ADD COLUMN source_snapshot_json TEXT NOT NULL DEFAULT '';

CREATE TRIGGER IF NOT EXISTS n02_tax_snapshot_immutable
 BEFORE UPDATE ON tax_workpapers
 WHEN NEW.source_snapshot_json IS NOT OLD.source_snapshot_json
 BEGIN SELECT RAISE(ABORT,'Tax source snapshot is immutable'); END;

CREATE TRIGGER IF NOT EXISTS n02_tax_scope_insert
 BEFORE INSERT ON tax_workpapers
 WHEN NOT EXISTS(SELECT 1 FROM books b JOIN companies c ON c.id=b.company_id AND c.status='active'
  JOIN firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id AND fc.status='active'
  JOIN periods p ON p.book_id=b.id AND p.period=NEW.period
  WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id
   AND b.period=NEW.period AND p.status='open')
 OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period)
 OR NOT EXISTS(SELECT 1 FROM tax_profiles p WHERE p.id=NEW.profile_id
  AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND p.effective_from<=NEW.period
  AND NOT EXISTS(SELECT 1 FROM tax_profiles n WHERE n.firm_id=NEW.firm_id AND n.company_id=NEW.company_id
   AND n.effective_from>p.effective_from AND n.effective_from<=NEW.period))
 BEGIN SELECT RAISE(ABORT,'Tax book/company/period/profile scope must be current and open'); END;

CREATE TRIGGER IF NOT EXISTS n02_tax_scope_update
 BEFORE UPDATE ON tax_workpapers
 WHEN NOT EXISTS(SELECT 1 FROM books b JOIN companies c ON c.id=b.company_id AND c.status='active'
  JOIN firm_clients fc ON fc.company_id=b.company_id AND fc.firm_id=b.firm_id AND fc.status='active'
  JOIN periods p ON p.book_id=b.id AND p.period=NEW.period
  WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id
   AND b.period=NEW.period AND p.status='open')
 OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period)
 OR NOT EXISTS(SELECT 1 FROM tax_profiles p WHERE p.id=NEW.profile_id
  AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND p.effective_from<=NEW.period
  AND NOT EXISTS(SELECT 1 FROM tax_profiles n WHERE n.firm_id=NEW.firm_id AND n.company_id=NEW.company_id
   AND n.effective_from>p.effective_from AND n.effective_from<=NEW.period))
 BEGIN SELECT RAISE(ABORT,'Tax book/company/period/profile scope must be current and open'); END;

CREATE TRIGGER IF NOT EXISTS n02_tax_snapshot_review
 BEFORE UPDATE ON tax_workpapers
 WHEN NEW.status='reviewed' AND CASE WHEN json_valid(NEW.source_snapshot_json) THEN
  json_extract(NEW.source_snapshot_json,'$.schema') IS NOT 'n02-tax-v2'
  OR json_extract(NEW.source_snapshot_json,'$.scope.firm_id') IS NOT NEW.firm_id
  OR json_extract(NEW.source_snapshot_json,'$.scope.company_id') IS NOT NEW.company_id
  OR json_extract(NEW.source_snapshot_json,'$.scope.book_id') IS NOT NEW.book_id
  OR json_extract(NEW.source_snapshot_json,'$.scope.period') IS NOT NEW.period
  OR json_extract(NEW.source_snapshot_json,'$.period') IS NOT NEW.period
  OR json_extract(NEW.source_snapshot_json,'$.scope.ledger_id') IS NOT
    (SELECT ledger_id FROM ledger_periods WHERE book_id=NEW.book_id AND period=NEW.period)
  OR json_extract(NEW.source_snapshot_json,'$.profile.id') IS NOT NEW.profile_id
  OR json_type(NEW.source_snapshot_json,'$.vat_accounts') IS NOT 'array'
  OR json_type(NEW.source_snapshot_json,'$.invoices') IS NOT 'array'
  OR json_type(NEW.source_snapshot_json,'$.ledger.vouchers') IS NOT 'array'
  OR json_type(NEW.source_snapshot_json,'$.ledger.lines') IS NOT 'array'
  OR json_type(NEW.source_snapshot_json,'$.totals') IS NOT 'array'
  OR json_array_length(NEW.source_snapshot_json,'$.totals') IS NOT 8
 ELSE 1 END
 BEGIN SELECT RAISE(ABORT,'Tax source snapshot schema or scope is invalid'); END;
