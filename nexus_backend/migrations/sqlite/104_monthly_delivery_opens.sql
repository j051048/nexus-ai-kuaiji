-- compatibility: incremental
-- A client opening records that the contact reached this exact frozen version.
-- It is view evidence, never a signature: monthly_delivery_acknowledgements stays the receipt.
CREATE TABLE monthly_delivery_opens(
 package_id TEXT PRIMARY KEY,book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 opened_by TEXT NOT NULL REFERENCES users(id),opened_at TEXT NOT NULL,
 FOREIGN KEY(package_id,book_id,firm_id,company_id) REFERENCES monthly_delivery_packages(id,book_id,firm_id,company_id)
);
CREATE TRIGGER monthly_delivery_open_guard BEFORE INSERT ON monthly_delivery_opens BEGIN
 SELECT CASE WHEN NOT EXISTS(
  SELECT 1 FROM monthly_delivery_packages p
  JOIN monthly_delivery_eligible_contacts c ON c.book_id=p.book_id AND c.user_id=p.contact_user_id
  WHERE p.id=NEW.package_id AND p.book_id=NEW.book_id AND p.firm_id=NEW.firm_id
  AND p.company_id=NEW.company_id AND p.contact_user_id=NEW.opened_by
  AND NEW.opened_by=accounting_actor_user() AND NEW.firm_id=accounting_actor_firm())
 THEN RAISE(ABORT,'Current client contact required to open a package') END;
END;
CREATE TRIGGER monthly_delivery_opens_no_update BEFORE UPDATE ON monthly_delivery_opens
 BEGIN SELECT RAISE(ABORT,'Client opening evidence is append-only'); END;
CREATE TRIGGER monthly_delivery_opens_no_delete BEFORE DELETE ON monthly_delivery_opens
 BEGIN SELECT RAISE(ABORT,'Client opening evidence is append-only'); END;
