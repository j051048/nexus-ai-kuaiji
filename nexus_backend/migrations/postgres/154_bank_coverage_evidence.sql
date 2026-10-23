-- compatibility: breaking
-- Bank coverage is a submitted, independently reviewed evidence claim. Preserve
-- old rows and their historic verifier without treating unbound claims as verified.
ALTER TABLE accounting.bank_statement_coverages
 ADD COLUMN state TEXT NOT NULL DEFAULT 'legacy_unverified',
 ADD COLUMN version INTEGER NOT NULL DEFAULT 1,
 ADD COLUMN source_file_id TEXT REFERENCES accounting.bank_import_files(id),
 ADD COLUMN source_preview_id TEXT REFERENCES accounting.bank_import_confirmations(preview_id),
 ADD COLUMN source_sha256 TEXT,
 ADD COLUMN created_by TEXT REFERENCES accounting.users(id),
 ADD COLUMN reason TEXT NOT NULL DEFAULT '',
 ADD COLUMN review_reason TEXT,
 ADD COLUMN fingerprint TEXT;
ALTER TABLE accounting.bank_statement_coverages
 ALTER COLUMN state SET DEFAULT 'pending_review',
 ALTER COLUMN verified_by DROP NOT NULL,
 ALTER COLUMN verified_at DROP NOT NULL;
ALTER TABLE accounting.bank_statement_coverages
 DROP CONSTRAINT bank_statement_coverages_account_id_period_key,
 ADD CONSTRAINT bank_coverage_state CHECK(state IN('legacy_unverified','pending_review','verified','rejected')),
 ADD CONSTRAINT bank_coverage_version CHECK(version>0),
 ADD CONSTRAINT bank_coverage_evidence_shape CHECK(state='legacy_unverified' OR (
  source_file_id IS NOT NULL AND source_preview_id IS NOT NULL
  AND source_sha256 IS NOT NULL AND source_sha256~'^[0-9a-f]{64}$'
  AND created_by IS NOT NULL AND length(btrim(reason)) BETWEEN 3 AND 1000
  AND fingerprint IS NOT NULL AND fingerprint~'^[0-9a-f]{64}$'
  AND opening_cents BETWEEN -99999999999999 AND 99999999999999
  AND closing_cents BETWEEN -99999999999999 AND 99999999999999
  AND period~'^[0-9]{4}-(0[1-9]|1[0-2])$'
  AND source_ref='bank_import_file:'||source_file_id
  AND ((state='pending_review' AND verified_by IS NULL AND verified_at IS NULL AND review_reason IS NULL)
   OR (state IN('verified','rejected') AND verified_by IS NOT NULL AND verified_at IS NOT NULL
    AND length(btrim(verified_at))>0 AND review_reason IS NOT NULL
    AND length(btrim(review_reason)) BETWEEN 3 AND 1000))
 ));
CREATE UNIQUE INDEX bank_coverage_current_account_period
 ON accounting.bank_statement_coverages(account_id,period)
 WHERE state IN('pending_review','verified');
CREATE INDEX bank_coverage_scope_created
 ON accounting.bank_statement_coverages(book_id,account_id,period,created_at DESC,id DESC);

-- Only the old generic identity/append-only triggers are replaced. The existing
-- company scope and close-dependency revision triggers remain installed.
DROP TRIGGER c01_bank_coverage_identity ON accounting.bank_statement_coverages;
DROP TRIGGER c01_bank_coverage_append_only ON accounting.bank_statement_coverages;
DROP TRIGGER c01_bank_coverage_lock ON accounting.bank_statement_coverages;

CREATE FUNCTION accounting.bank_coverage_evidence_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE b RECORD; payload JSONB; item JSONB; fact JSONB; net NUMERIC:=0; cents BIGINT;
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'Bank coverage history is immutable' USING ERRCODE='23514';
 END IF;
 SELECT firm_id,company_id,period INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR NOT accounting.book_allowed(NEW.book_id,true) THEN
  RAISE EXCEPTION 'Current scoped bank coverage writer required' USING ERRCODE='42501';
 END IF;
 PERFORM accounting.lock_company_write(b.firm_id,b.company_id);
 IF NEW.period IS DISTINCT FROM b.period
  OR NOT EXISTS(SELECT 1 FROM accounting.periods p
   WHERE p.book_id=NEW.book_id AND p.period=NEW.period AND p.status='open')
  OR EXISTS(SELECT 1 FROM accounting.internal_period_locks l
   WHERE l.book_id=NEW.book_id AND l.period=NEW.period) THEN
  RAISE EXCEPTION 'Bank coverage requires current open unlocked period' USING ERRCODE='23514';
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.state IS DISTINCT FROM 'pending_review' OR NEW.version IS DISTINCT FROM 1
   OR NEW.created_by IS DISTINCT FROM accounting.actor_user()
   OR NEW.verified_by IS NOT NULL OR NEW.verified_at IS NOT NULL OR NEW.review_reason IS NOT NULL THEN
   RAISE EXCEPTION 'New bank coverage must be an unreviewed current actor submission' USING ERRCODE='42501';
  END IF;
 ELSE
  IF OLD.state<>'pending_review' OR NEW.state NOT IN('verified','rejected')
   OR NEW.version<>OLD.version+1
   OR (to_jsonb(NEW)-'state'-'version'-'verified_by'-'verified_at'-'review_reason')
    IS DISTINCT FROM (to_jsonb(OLD)-'state'-'version'-'verified_by'-'verified_at'-'review_reason') THEN
   RAISE EXCEPTION 'Bank coverage evidence is immutable; only pending review may finish' USING ERRCODE='23514';
  END IF;
  IF NOT accounting.book_review_allowed(NEW.book_id)
   OR OLD.created_by IS NOT DISTINCT FROM accounting.actor_user()
   OR NEW.verified_by IS DISTINCT FROM accounting.actor_user() THEN
   RAISE EXCEPTION 'Independent current bank coverage reviewer required' USING ERRCODE='42501';
  END IF;
 END IF;
 IF NOT EXISTS(
  SELECT 1 FROM accounting.bank_accounts a
  JOIN accounting.bank_import_files f ON f.id=NEW.source_file_id
   AND f.book_id=NEW.book_id AND f.firm_id=a.firm_id AND f.company_id=a.company_id
  JOIN accounting.bank_import_previews p ON p.id=NEW.source_preview_id AND p.file_id=f.id
   AND p.book_id=f.book_id AND p.firm_id=f.firm_id AND p.company_id=f.company_id
  JOIN accounting.bank_import_confirmations c ON c.preview_id=p.id AND c.book_id=p.book_id
   AND c.firm_id=p.firm_id AND c.company_id=p.company_id
  WHERE a.id=NEW.account_id AND a.book_id=NEW.book_id
   AND a.firm_id=b.firm_id AND a.company_id=b.company_id AND a.active
   AND f.sha256=NEW.source_sha256
   AND p.payload_json::jsonb->'request'->>'bank_account'=a.id
   AND NEW.source_ref='bank_import_file:'||f.id
 ) THEN
  RAISE EXCEPTION 'Bank coverage requires confirmed same-account original and hash' USING ERRCODE='23514';
 END IF;
 SELECT p.payload_json::jsonb INTO payload
  FROM accounting.bank_import_previews p WHERE p.id=NEW.source_preview_id;
 IF jsonb_typeof(payload->'rows') IS DISTINCT FROM 'array'
  OR jsonb_array_length(payload->'rows')=0 THEN
  RAISE EXCEPTION 'Bank coverage requires nonempty frozen statement rows' USING ERRCODE='23514';
 END IF;
 FOR item IN SELECT value FROM jsonb_array_elements(payload->'rows') LOOP
  fact:=item->'source';
  IF jsonb_typeof(item) IS DISTINCT FROM 'object'
   OR item->'errors' IS DISTINCT FROM '[]'::jsonb
   OR jsonb_typeof(fact) IS DISTINCT FROM 'object'
   OR jsonb_typeof(fact->'date') IS DISTINCT FROM 'string'
   OR coalesce(fact->>'date','') !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
   OR left(fact->>'date',7) IS DISTINCT FROM NEW.period
   OR jsonb_typeof(fact->'amount') IS DISTINCT FROM 'string'
   OR coalesce(fact->>'direction','') NOT IN('income','expense') THEN
   RAISE EXCEPTION 'Bank coverage row must have valid same-period canonical facts and no errors' USING ERRCODE='23514';
  END IF;
  IF (fact->>'date')::date::text IS DISTINCT FROM fact->>'date' THEN
   RAISE EXCEPTION 'Bank coverage row date must be canonical' USING ERRCODE='23514';
  END IF;
  cents:=accounting.domain_money_cents(fact->>'amount');
  IF cents<=0 THEN
   RAISE EXCEPTION 'Bank coverage transaction amount must be positive' USING ERRCODE='23514';
  END IF;
  net:=net+CASE WHEN fact->>'direction'='income' THEN cents ELSE -cents END;
 END LOOP;
 IF NEW.opening_cents::NUMERIC+net IS DISTINCT FROM NEW.closing_cents::NUMERIC THEN
  RAISE EXCEPTION 'Bank opening plus frozen statement net must equal closing' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.bank_coverage_evidence_guard()
 FROM PUBLIC,accounting_app,accounting_worker,accounting_system;
CREATE TRIGGER c01_bank_coverage_evidence BEFORE INSERT OR UPDATE OR DELETE
 ON accounting.bank_statement_coverages FOR EACH ROW
 EXECUTE FUNCTION accounting.bank_coverage_evidence_guard();
