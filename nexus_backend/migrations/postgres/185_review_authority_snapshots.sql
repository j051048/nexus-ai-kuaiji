-- compatibility: incremental
SET LOCAL search_path=accounting,pg_catalog;
CREATE TABLE review_authority_snapshots(subject_kind TEXT NOT NULL CHECK(subject_kind IN('draft','manual','periodic')),subject_id TEXT NOT NULL,reviewed_version INTEGER NOT NULL,book_id TEXT NOT NULL REFERENCES books(id),firm_id TEXT NOT NULL REFERENCES firms(id),reviewer_user_id TEXT NOT NULL REFERENCES users(id),role_snapshot TEXT NOT NULL CHECK(role_snapshot IN('owner','admin','reviewer')),proof_kind TEXT NOT NULL CHECK(proof_kind IN('review_time','cutover_current_membership')),recorded_at TEXT NOT NULL,PRIMARY KEY(subject_kind,subject_id,reviewed_version));
INSERT INTO review_authority_snapshots SELECT 'draft',r.draft_id,r.reviewed_version,r.book_id,b.firm_id,r.reviewer_user_id,m.role,'cutover_current_membership',CAST(CURRENT_TIMESTAMP AS TEXT) FROM draft_review_receipts r JOIN books b ON b.id=r.book_id JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=r.reviewer_user_id WHERE m.role IN('owner','admin','reviewer');
INSERT INTO review_authority_snapshots SELECT 'manual',r.id,r.version,r.book_id,b.firm_id,r.reviewed_by,m.role,'cutover_current_membership',CAST(CURRENT_TIMESTAMP AS TEXT) FROM manual_review_requests r JOIN books b ON b.id=r.book_id JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=r.reviewed_by WHERE r.status='approved' AND m.role IN('owner','admin','reviewer');
INSERT INTO review_authority_snapshots SELECT 'periodic',r.id,r.version,r.book_id,b.firm_id,r.approved_by,m.role,'cutover_current_membership',CAST(CURRENT_TIMESTAMP AS TEXT) FROM periodic_drafts r JOIN books b ON b.id=r.book_id JOIN memberships m ON m.firm_id=b.firm_id AND m.user_id=r.approved_by WHERE r.status IN('approved','posted') AND m.role IN('owner','admin','reviewer');

CREATE OR REPLACE FUNCTION accounting.record_review_authority() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE subject TEXT; number INTEGER; person TEXT; kind TEXT; BEGIN
 IF TG_TABLE_NAME='draft_review_receipts' THEN subject:=NEW.draft_id; number:=NEW.reviewed_version; person:=NEW.reviewer_user_id; kind:='draft';
 ELSIF TG_TABLE_NAME='manual_review_requests' THEN IF NEW.status<>'approved' OR OLD.status='approved' THEN RETURN NEW; END IF; subject:=NEW.id; number:=NEW.version; person:=NEW.reviewed_by; kind:='manual';
 ELSE IF NEW.status<>'approved' OR OLD.status='approved' THEN RETURN NEW; END IF; subject:=NEW.id; number:=NEW.version; person:=NEW.approved_by; kind:='periodic'; END IF;
 IF NOT accounting.book_review_allowed(NEW.book_id) OR person IS DISTINCT FROM accounting.actor_user() THEN RAISE EXCEPTION 'Firm review authority required' USING ERRCODE='42501'; END IF;
 INSERT INTO accounting.review_authority_snapshots SELECT kind,subject,number,b.id,b.firm_id,person,m.role,'review_time',clock_timestamp()::text FROM accounting.books b JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=person WHERE b.id=NEW.book_id AND m.role IN('owner','admin','reviewer');
 RETURN NEW; END $$;
CREATE OR REPLACE FUNCTION accounting.require_review_authority() RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$ BEGIN
 IF TG_TABLE_NAME='drafts' THEN
  IF OLD.status='approved' AND NEW.status='exported' AND NOT EXISTS(SELECT 1 FROM accounting.review_authority_snapshots WHERE subject_kind='draft' AND subject_id=NEW.id AND reviewed_version=OLD.version-1 AND book_id=NEW.book_id) THEN RAISE EXCEPTION 'Historical review requires firm verification' USING ERRCODE='42501'; END IF;
 ELSIF NEW.source_kind='draft' AND NEW.periodic_review_id IS NULL THEN
  IF NOT EXISTS(SELECT 1 FROM accounting.drafts d JOIN accounting.review_authority_snapshots s ON s.subject_id=d.id AND s.subject_kind='draft' AND s.book_id=d.book_id AND s.reviewed_version=CASE WHEN d.status='exported' THEN d.version-2 ELSE d.version-1 END WHERE d.id=NEW.source_id AND d.book_id=NEW.book_id) THEN RAISE EXCEPTION 'Firm draft review proof required' USING ERRCODE='42501'; END IF;
 ELSIF NEW.review_request_id IS NOT NULL THEN
  IF NOT EXISTS(SELECT 1 FROM accounting.manual_review_requests r JOIN accounting.review_authority_snapshots s ON s.subject_id=r.id AND s.subject_kind='manual' AND s.book_id=r.book_id AND s.reviewed_version=r.version WHERE r.id=NEW.review_request_id AND r.book_id=NEW.book_id) THEN RAISE EXCEPTION 'Firm manual review proof required' USING ERRCODE='42501'; END IF;
 ELSIF NEW.periodic_review_id IS NOT NULL THEN
  IF NOT EXISTS(SELECT 1 FROM accounting.periodic_drafts r JOIN accounting.review_authority_snapshots s ON s.subject_id=r.id AND s.subject_kind='periodic' AND s.book_id=r.book_id AND s.reviewed_version=r.version WHERE r.id=NEW.periodic_review_id AND r.book_id=NEW.book_id) THEN RAISE EXCEPTION 'Firm periodic review proof required' USING ERRCODE='42501'; END IF;
 END IF; RETURN NEW; END $$;
ALTER TABLE accounting.review_authority_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.review_authority_snapshots FORCE ROW LEVEL SECURITY;
GRANT SELECT ON accounting.review_authority_snapshots TO accounting_app;
DROP POLICY IF EXISTS authority_read ON accounting.review_authority_snapshots;
CREATE POLICY authority_read ON accounting.review_authority_snapshots FOR SELECT TO accounting_app USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
DROP TRIGGER IF EXISTS record_firm_authority ON accounting.draft_review_receipts;
CREATE TRIGGER record_firm_authority AFTER INSERT ON accounting.draft_review_receipts FOR EACH ROW EXECUTE FUNCTION accounting.record_review_authority();
DROP TRIGGER IF EXISTS record_firm_authority ON accounting.manual_review_requests;
CREATE TRIGGER record_firm_authority AFTER UPDATE ON accounting.manual_review_requests FOR EACH ROW EXECUTE FUNCTION accounting.record_review_authority();
DROP TRIGGER IF EXISTS record_firm_authority ON accounting.periodic_drafts;
CREATE TRIGGER record_firm_authority AFTER UPDATE ON accounting.periodic_drafts FOR EACH ROW EXECUTE FUNCTION accounting.record_review_authority();
DROP TRIGGER IF EXISTS require_firm_authority ON accounting.drafts;
CREATE TRIGGER require_firm_authority BEFORE UPDATE ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.require_review_authority();
DROP TRIGGER IF EXISTS require_firm_authority ON accounting.vouchers;
CREATE TRIGGER require_firm_authority BEFORE INSERT ON accounting.vouchers FOR EACH ROW EXECUTE FUNCTION accounting.require_review_authority();
DROP TRIGGER IF EXISTS authority_immutable ON accounting.review_authority_snapshots;
CREATE TRIGGER authority_immutable BEFORE UPDATE OR DELETE ON accounting.review_authority_snapshots FOR EACH ROW EXECUTE FUNCTION accounting.delivery_immutable();
DROP TRIGGER IF EXISTS authority_company_guard ON accounting.review_authority_snapshots;
CREATE TRIGGER authority_company_guard BEFORE INSERT OR UPDATE OR DELETE ON accounting.review_authority_snapshots FOR EACH ROW EXECUTE FUNCTION accounting.company_write_table_guard();
REVOKE ALL ON FUNCTION accounting.record_review_authority(),accounting.require_review_authority() FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
