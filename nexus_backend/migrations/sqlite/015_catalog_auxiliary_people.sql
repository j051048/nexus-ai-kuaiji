-- Local verification enforces immutable, proven accounting identity facts.
CREATE TABLE catalog_auxiliary_people (
 book_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 employee_id TEXT NOT NULL REFERENCES users(id),display_name TEXT NOT NULL,
 recorded_by TEXT NOT NULL REFERENCES users(id),recorded_at TEXT NOT NULL,
 PRIMARY KEY(book_id,employee_id),FOREIGN KEY(book_id,firm_id,company_id) REFERENCES books(id,firm_id,company_id)
);
CREATE TRIGGER catalog_people_insert_proof BEFORE INSERT ON catalog_auxiliary_people BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id AND p.status='open')
 THEN RAISE(ABORT,'accounting person requires scoped open period') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM company_memberships WHERE user_id=NEW.employee_id AND company_id=NEW.company_id)
 AND NOT EXISTS(SELECT 1 FROM business_mappings m JOIN business_events e ON e.id=m.event_id AND e.firm_id=m.firm_id AND e.company_id=m.company_id WHERE m.book_id=NEW.book_id AND m.firm_id=NEW.firm_id AND m.company_id=NEW.company_id AND e.applicant_id=NEW.employee_id)
 THEN RAISE(ABORT,'accounting person requires company ownership proof') END; END;
CREATE TRIGGER catalog_people_immutable_update BEFORE UPDATE ON catalog_auxiliary_people BEGIN SELECT RAISE(ABORT,'accounting person facts are immutable'); END;
CREATE TRIGGER catalog_people_immutable_delete BEFORE DELETE ON catalog_auxiliary_people BEGIN SELECT RAISE(ABORT,'accounting person facts are immutable'); END;
