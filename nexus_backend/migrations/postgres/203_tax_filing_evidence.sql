-- compatibility: incremental
-- NEXT05: append-only external filing evidence. Corrections/voids are new rows,
-- never rewrites; an optional private original and the workpaper version are
-- bound at registration time.
ALTER TABLE accounting.tax_filings ADD COLUMN IF NOT EXISTS entry_kind TEXT NOT NULL DEFAULT 'registration';
ALTER TABLE accounting.tax_filings ADD COLUMN IF NOT EXISTS supersedes_id TEXT;
ALTER TABLE accounting.tax_filings ADD COLUMN IF NOT EXISTS workpaper_id TEXT;
ALTER TABLE accounting.tax_filings ADD COLUMN IF NOT EXISTS workpaper_version INTEGER;
ALTER TABLE accounting.tax_filings ADD COLUMN IF NOT EXISTS workpaper_digest TEXT;
ALTER TABLE accounting.tax_filings ADD COLUMN IF NOT EXISTS workpaper_status TEXT;
ALTER TABLE accounting.tax_filings ADD COLUMN IF NOT EXISTS amount_check TEXT NOT NULL DEFAULT 'no_workpaper';
ALTER TABLE accounting.tax_filings ADD COLUMN IF NOT EXISTS correction_reason TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS accounting.tax_filing_attachments(
 id TEXT PRIMARY KEY,
 filing_id TEXT NOT NULL REFERENCES accounting.tax_filings(id),
 firm_id TEXT NOT NULL REFERENCES accounting.firms(id),
 company_id TEXT NOT NULL REFERENCES accounting.companies(id),
 book_id TEXT NOT NULL REFERENCES accounting.books(id),
 storage_key TEXT NOT NULL UNIQUE,
 filename TEXT NOT NULL CHECK(char_length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL,
 sha256 TEXT NOT NULL CHECK(char_length(sha256)=64),
 size_bytes BIGINT NOT NULL CHECK(size_bytes>0),
 created_by TEXT NOT NULL REFERENCES accounting.users(id),
 created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tax_filing_attachments_filing
 ON accounting.tax_filing_attachments(filing_id,created_at);

ALTER TABLE accounting.tax_filing_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE accounting.tax_filing_attachments FORCE ROW LEVEL SECURITY;
REVOKE ALL ON accounting.tax_filing_attachments FROM PUBLIC, accounting_app, accounting_worker;
GRANT SELECT,INSERT ON accounting.tax_filing_attachments TO accounting_app;
DROP POLICY IF EXISTS dcp_filing_attachment_read ON accounting.tax_filing_attachments;
CREATE POLICY dcp_filing_attachment_read ON accounting.tax_filing_attachments FOR SELECT TO accounting_app
 USING(firm_id=accounting.actor_firm() AND accounting.book_allowed(book_id));
DROP POLICY IF EXISTS dcp_filing_attachment_insert ON accounting.tax_filing_attachments;
CREATE POLICY dcp_filing_attachment_insert ON accounting.tax_filing_attachments FOR INSERT TO accounting_app
 WITH CHECK(firm_id=accounting.actor_firm() AND accounting.book_review_allowed(book_id)
 AND created_by=accounting.actor_user());

CREATE OR REPLACE FUNCTION accounting.dcp_filing_guard() RETURNS trigger
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE old_filing RECORD; paper RECORD;
BEGIN
 PERFORM accounting.lock_company_write(NEW.firm_id,NEW.company_id);
 IF NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NEW.created_by IS DISTINCT FROM accounting.actor_user()
 OR NOT accounting.actor_active() OR NOT accounting.book_review_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'Current filing reviewer and identity required' USING ERRCODE='42501';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM accounting.books b WHERE b.id=NEW.book_id
  AND b.company_id=NEW.company_id AND b.firm_id=NEW.firm_id AND b.period=NEW.period) THEN
  RAISE EXCEPTION 'Filing scope does not match the book' USING ERRCODE='23514';
 END IF;
 IF NEW.entry_kind NOT IN('registration','correction','void') THEN
  RAISE EXCEPTION 'Unsupported filing entry kind' USING ERRCODE='23514';
 END IF;
 IF NEW.amount_check NOT IN('match','mismatch','no_workpaper') THEN
  RAISE EXCEPTION 'Unsupported filing amount check' USING ERRCODE='23514';
 END IF;
 IF NEW.entry_kind='registration' THEN
  IF NEW.supersedes_id IS NOT NULL OR length(trim(NEW.correction_reason))>0 THEN
   RAISE EXCEPTION 'Registration cannot supersede another filing' USING ERRCODE='23514';
  END IF;
 ELSE
  IF NEW.supersedes_id IS NULL OR length(trim(NEW.correction_reason)) NOT BETWEEN 3 AND 1000 THEN
   RAISE EXCEPTION 'Correction reason and predecessor required' USING ERRCODE='23514';
  END IF;
  SELECT * INTO old_filing FROM accounting.tax_filings f WHERE f.id=NEW.supersedes_id;
  IF NOT FOUND OR old_filing.book_id<>NEW.book_id OR old_filing.period<>NEW.period
   OR old_filing.kind<>NEW.kind OR old_filing.entry_kind='void' THEN
   RAISE EXCEPTION 'Filing predecessor is stale or foreign' USING ERRCODE='23514';
  END IF;
  IF EXISTS(SELECT 1 FROM accounting.tax_filings f WHERE f.supersedes_id=old_filing.id) THEN
   RAISE EXCEPTION 'Filing predecessor already superseded' USING ERRCODE='23505';
  END IF;
 END IF;
 IF NEW.workpaper_id IS NOT NULL THEN
  SELECT * INTO paper FROM accounting.tax_workpapers w
   WHERE w.id=NEW.workpaper_id AND w.book_id=NEW.book_id AND w.period=NEW.period;
  IF NOT FOUND OR paper.version IS DISTINCT FROM NEW.workpaper_version
   OR paper.source_digest IS DISTINCT FROM NEW.workpaper_digest THEN
   RAISE EXCEPTION 'Filing workpaper version is stale or foreign' USING ERRCODE='23514';
  END IF;
 END IF;
 BEGIN
  IF NEW.occurred_at !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
   OR to_char(NEW.occurred_at::date,'YYYY-MM-DD')<>NEW.occurred_at THEN
   RAISE EXCEPTION 'Invalid filing date' USING ERRCODE='23514';
  END IF;
 EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
  RAISE EXCEPTION 'Invalid filing date' USING ERRCODE='23514';
 END;
 IF EXISTS(SELECT 1 FROM accounting.tax_filings f WHERE f.book_id=NEW.book_id AND f.period=NEW.period
  AND f.kind=NEW.kind AND f.status=NEW.status AND f.external_ref=NEW.external_ref) THEN
  RAISE EXCEPTION 'Filing receipt already recorded' USING ERRCODE='23505';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.dcp_filing_guard() FROM PUBLIC;
DROP TRIGGER IF EXISTS dcp_filing_guard ON accounting.tax_filings;
CREATE TRIGGER dcp_filing_guard BEFORE INSERT ON accounting.tax_filings
 FOR EACH ROW EXECUTE FUNCTION accounting.dcp_filing_guard();
