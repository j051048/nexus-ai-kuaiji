-- OPT06-B: optional firm policy that removes manager review/edit shortcuts.
ALTER TABLE accounting.firm_review_controls
  ADD COLUMN management_review_separation BOOLEAN NOT NULL DEFAULT FALSE;

CREATE OR REPLACE FUNCTION accounting.record_review_authority() RETURNS TRIGGER
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE subject TEXT; number INTEGER; person TEXT; kind TEXT; target_firm TEXT; separation BOOLEAN;
BEGIN
 IF TG_TABLE_NAME='draft_review_receipts' THEN subject:=NEW.draft_id; number:=NEW.reviewed_version; person:=NEW.reviewer_user_id; kind:='draft';
 ELSIF TG_TABLE_NAME='manual_review_requests' THEN IF NEW.status<>'approved' OR OLD.status='approved' THEN RETURN NEW; END IF; subject:=NEW.id; number:=NEW.version; person:=NEW.reviewed_by; kind:='manual';
 ELSE IF NEW.status<>'approved' OR OLD.status='approved' THEN RETURN NEW; END IF; subject:=NEW.id; number:=NEW.version; person:=NEW.approved_by; kind:='periodic'; END IF;
 SELECT b.firm_id,coalesce(c.management_review_separation,false) INTO target_firm,separation
 FROM accounting.books b LEFT JOIN accounting.firm_review_controls c ON c.firm_id=b.firm_id WHERE b.id=NEW.book_id;
 IF separation AND accounting.firm_role(target_firm) IN ('owner','admin') THEN
   RAISE EXCEPTION 'Management/review separation is enabled for this firm' USING ERRCODE='42501';
 END IF;
 IF NOT accounting.book_review_allowed(NEW.book_id) OR person IS DISTINCT FROM accounting.actor_user() THEN RAISE EXCEPTION 'Firm review authority required' USING ERRCODE='42501'; END IF;
 INSERT INTO accounting.review_authority_snapshots SELECT kind,subject,number,b.id,b.firm_id,person,m.role,'review_time',clock_timestamp()::text FROM accounting.books b JOIN accounting.memberships m ON m.firm_id=b.firm_id AND m.user_id=person WHERE b.id=NEW.book_id AND m.role IN('owner','admin','reviewer');
 RETURN NEW; END $$;
