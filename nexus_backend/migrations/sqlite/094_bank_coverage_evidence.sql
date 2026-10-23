-- compatibility: breaking
-- Preserve unbound historic claims; no foreign table references this coverage
-- table. Rebuild solely to make the old verification columns nullable.
DROP TRIGGER IF EXISTS c01_bank_coverage_lock;
CREATE TABLE bank_statement_coverages_new(
 id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES bank_accounts(id),
 book_id TEXT NOT NULL REFERENCES books(id), period TEXT NOT NULL,
 opening_cents INTEGER NOT NULL, closing_cents INTEGER NOT NULL,
 source_ref TEXT NOT NULL, verified_by TEXT, verified_at TEXT,
 created_at TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending_review'
  CHECK(state IN('legacy_unverified','pending_review','verified','rejected')),
 version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
 source_file_id TEXT REFERENCES bank_import_files(id),
 source_preview_id TEXT REFERENCES bank_import_confirmations(preview_id),
 source_sha256 TEXT, created_by TEXT REFERENCES users(id),
 reason TEXT NOT NULL DEFAULT '', review_reason TEXT, fingerprint TEXT,
 CHECK(state='legacy_unverified' OR (
  source_file_id IS NOT NULL AND source_preview_id IS NOT NULL
  AND source_sha256 IS NOT NULL AND length(source_sha256)=64
  AND source_sha256 NOT GLOB '*[^0-9a-f]*'
  AND created_by IS NOT NULL AND length(trim(reason)) BETWEEN 3 AND 1000
  AND fingerprint IS NOT NULL AND length(fingerprint)=64 AND fingerprint NOT GLOB '*[^0-9a-f]*'
  AND typeof(opening_cents)='integer' AND typeof(closing_cents)='integer'
  AND opening_cents BETWEEN -99999999999999 AND 99999999999999
  AND closing_cents BETWEEN -99999999999999 AND 99999999999999
  AND length(period)=7 AND substr(period,1,4) NOT GLOB '*[^0-9]*'
  AND substr(period,6,2) NOT GLOB '*[^0-9]*'
  AND substr(period,5,1)='-' AND substr(period,6,2) BETWEEN '01' AND '12'
  AND source_ref='bank_import_file:'||source_file_id
  AND ((state='pending_review' AND verified_by IS NULL AND verified_at IS NULL AND review_reason IS NULL)
   OR (state IN('verified','rejected') AND verified_by IS NOT NULL AND verified_at IS NOT NULL
    AND length(trim(verified_at))>0 AND review_reason IS NOT NULL
    AND length(trim(review_reason)) BETWEEN 3 AND 1000))
 ))
);
INSERT INTO bank_statement_coverages_new(
 id,account_id,book_id,period,opening_cents,closing_cents,source_ref,
 verified_by,verified_at,created_at,state)
 SELECT id,account_id,book_id,period,opening_cents,closing_cents,source_ref,
  verified_by,verified_at,created_at,'legacy_unverified' FROM bank_statement_coverages;
DROP TABLE bank_statement_coverages;
ALTER TABLE bank_statement_coverages_new RENAME TO bank_statement_coverages;
CREATE UNIQUE INDEX bank_coverage_current_account_period
 ON bank_statement_coverages(account_id,period) WHERE state IN('pending_review','verified');
CREATE INDEX bank_coverage_scope_created
 ON bank_statement_coverages(book_id,account_id,period,created_at DESC,id DESC);

CREATE VIEW bank_coverage_editable_books AS
 SELECT b.id FROM books b
 JOIN users u ON u.id=accounting_actor_user() AND u.active=1
 JOIN companies c ON c.id=b.company_id AND c.status='active'
 JOIN firm_clients fc ON fc.firm_id=b.firm_id AND fc.company_id=b.company_id AND fc.status='active'
 WHERE b.firm_id=accounting_actor_firm() AND (
  EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=b.firm_id AND m.user_id=u.id AND m.role IN('owner','admin'))
  OR EXISTS(SELECT 1 FROM book_access ba WHERE ba.book_id=b.id AND ba.firm_id=b.firm_id
   AND ba.user_id=u.id AND ba.role IN('editor','reviewer') AND (
    EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=b.firm_id AND m.user_id=u.id AND m.role IN('operator','reviewer'))
    OR (NOT EXISTS(SELECT 1 FROM memberships m WHERE m.firm_id=b.firm_id AND m.user_id=u.id)
     AND EXISTS(SELECT 1 FROM company_memberships cm WHERE cm.company_id=b.company_id
      AND cm.user_id=u.id AND cm.role IN('owner','admin','finance'))))));

CREATE TRIGGER c01_bank_coverage_insert_identity BEFORE INSERT ON bank_statement_coverages
WHEN NEW.state<>'pending_review' OR NEW.version<>1
 OR NEW.created_by IS NOT accounting_actor_user() OR NEW.verified_by IS NOT NULL
 OR NEW.verified_at IS NOT NULL OR NEW.review_reason IS NOT NULL
 OR NEW.book_id NOT IN(SELECT id FROM bank_coverage_editable_books)
BEGIN SELECT RAISE(ABORT,'New bank coverage must be an unreviewed current actor submission'); END;

CREATE TRIGGER c01_bank_coverage_update_identity BEFORE UPDATE ON bank_statement_coverages
WHEN OLD.state<>'pending_review' OR NEW.state NOT IN('verified','rejected')
 OR NEW.version<>OLD.version+1 OR NEW.created_by IS NOT OLD.created_by
 OR NEW.id IS NOT OLD.id OR NEW.account_id IS NOT OLD.account_id OR NEW.book_id IS NOT OLD.book_id
 OR NEW.period IS NOT OLD.period OR NEW.opening_cents IS NOT OLD.opening_cents
 OR NEW.closing_cents IS NOT OLD.closing_cents OR NEW.source_ref IS NOT OLD.source_ref
 OR NEW.source_file_id IS NOT OLD.source_file_id OR NEW.source_preview_id IS NOT OLD.source_preview_id
 OR NEW.source_sha256 IS NOT OLD.source_sha256 OR NEW.created_at IS NOT OLD.created_at
 OR NEW.reason IS NOT OLD.reason OR NEW.fingerprint IS NOT OLD.fingerprint
 OR OLD.created_by IS accounting_actor_user() OR NEW.verified_by IS NOT accounting_actor_user()
 OR NEW.book_id NOT IN(SELECT id FROM close_reviewable_books)
BEGIN SELECT RAISE(ABORT,'Independent current reviewer may only finish pending immutable bank coverage'); END;

CREATE TRIGGER c01_bank_coverage_delete BEFORE DELETE ON bank_statement_coverages
BEGIN SELECT RAISE(ABORT,'Bank coverage history is immutable'); END;

CREATE TRIGGER c01_bank_coverage_insert_lock BEFORE INSERT ON bank_statement_coverages
WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period
 WHERE b.id=NEW.book_id AND b.period=NEW.period AND p.status='open')
 OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period)
BEGIN SELECT RAISE(ABORT,'Bank coverage requires current open unlocked period'); END;
CREATE TRIGGER c01_bank_coverage_update_lock BEFORE UPDATE ON bank_statement_coverages
WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period
 WHERE b.id=NEW.book_id AND b.period=NEW.period AND p.status='open')
 OR EXISTS(SELECT 1 FROM internal_period_locks WHERE book_id=NEW.book_id AND period=NEW.period)
BEGIN SELECT RAISE(ABORT,'Bank coverage requires current open unlocked period'); END;

CREATE TRIGGER c01_bank_coverage_insert_evidence BEFORE INSERT ON bank_statement_coverages
WHEN NOT EXISTS(
 SELECT 1 FROM books b JOIN bank_accounts a ON a.id=NEW.account_id AND a.book_id=b.id
  AND a.firm_id=b.firm_id AND a.company_id=b.company_id AND a.active=1
 JOIN bank_import_files f ON f.id=NEW.source_file_id AND f.book_id=b.id
  AND f.firm_id=b.firm_id AND f.company_id=b.company_id
 JOIN bank_import_previews p ON p.id=NEW.source_preview_id AND p.file_id=f.id
  AND p.book_id=f.book_id AND p.firm_id=f.firm_id AND p.company_id=f.company_id
 JOIN bank_import_confirmations c ON c.preview_id=p.id AND c.book_id=p.book_id
  AND c.firm_id=p.firm_id AND c.company_id=p.company_id
 WHERE b.id=NEW.book_id AND f.sha256=NEW.source_sha256
  AND json_extract(p.payload_json,'$.request.bank_account')=a.id
  AND NEW.source_ref='bank_import_file:'||f.id)
BEGIN SELECT RAISE(ABORT,'Bank coverage requires confirmed same-account original and hash'); END;
CREATE TRIGGER c01_bank_coverage_update_evidence BEFORE UPDATE ON bank_statement_coverages
WHEN NOT EXISTS(
 SELECT 1 FROM books b JOIN bank_accounts a ON a.id=NEW.account_id AND a.book_id=b.id
  AND a.firm_id=b.firm_id AND a.company_id=b.company_id AND a.active=1
 JOIN bank_import_files f ON f.id=NEW.source_file_id AND f.book_id=b.id
  AND f.firm_id=b.firm_id AND f.company_id=b.company_id
 JOIN bank_import_previews p ON p.id=NEW.source_preview_id AND p.file_id=f.id
  AND p.book_id=f.book_id AND p.firm_id=f.firm_id AND p.company_id=f.company_id
 JOIN bank_import_confirmations c ON c.preview_id=p.id AND c.book_id=p.book_id
  AND c.firm_id=p.firm_id AND c.company_id=p.company_id
 WHERE b.id=NEW.book_id AND f.sha256=NEW.source_sha256
  AND json_extract(p.payload_json,'$.request.bank_account')=a.id
  AND NEW.source_ref='bank_import_file:'||f.id)
BEGIN SELECT RAISE(ABORT,'Bank coverage requires confirmed same-account original and hash'); END;

-- accounting_money_cents is the existing deterministic Python money parser
-- registered on every application connection; no REAL arithmetic is used.
CREATE TRIGGER c01_bank_coverage_insert_balance BEFORE INSERT ON bank_statement_coverages
WHEN NOT EXISTS(
 SELECT 1 FROM bank_import_previews p WHERE p.id=NEW.source_preview_id
  AND json_type(p.payload_json,'$.rows')='array' AND json_array_length(p.payload_json,'$.rows')>0
  AND NOT EXISTS(SELECT 1 FROM json_each(p.payload_json,'$.rows') r
   WHERE json_type(r.value)<>'object' OR json_extract(r.value,'$.errors') IS NOT '[]'
    OR json_type(r.value,'$.source') IS NOT 'object'
    OR json_type(r.value,'$.source.date') IS NOT 'text'
    OR length(json_extract(r.value,'$.source.date'))<>10
    OR substr(json_extract(r.value,'$.source.date'),1,7) IS NOT NEW.period
    OR date(json_extract(r.value,'$.source.date'),'+0 days') IS NOT json_extract(r.value,'$.source.date')
    OR json_type(r.value,'$.source.amount') IS NOT 'text'
    OR accounting_money_cents(json_extract(r.value,'$.source.amount'))<=0
    OR coalesce(json_extract(r.value,'$.source.direction'),'') NOT IN('income','expense'))
  AND NEW.closing_cents=NEW.opening_cents+(SELECT sum(
   accounting_money_cents(json_extract(r.value,'$.source.amount'))
    *CASE WHEN json_extract(r.value,'$.source.direction')='income' THEN 1 ELSE -1 END)
   FROM json_each(p.payload_json,'$.rows') r))
BEGIN SELECT RAISE(ABORT,'Bank coverage requires valid same-period rows and opening plus net equals closing'); END;
CREATE TRIGGER c01_bank_coverage_update_balance BEFORE UPDATE ON bank_statement_coverages
WHEN NOT EXISTS(
 SELECT 1 FROM bank_import_previews p WHERE p.id=NEW.source_preview_id
  AND json_type(p.payload_json,'$.rows')='array' AND json_array_length(p.payload_json,'$.rows')>0
  AND NOT EXISTS(SELECT 1 FROM json_each(p.payload_json,'$.rows') r
   WHERE json_type(r.value)<>'object' OR json_extract(r.value,'$.errors') IS NOT '[]'
    OR json_type(r.value,'$.source') IS NOT 'object'
    OR json_type(r.value,'$.source.date') IS NOT 'text'
    OR length(json_extract(r.value,'$.source.date'))<>10
    OR substr(json_extract(r.value,'$.source.date'),1,7) IS NOT NEW.period
    OR date(json_extract(r.value,'$.source.date'),'+0 days') IS NOT json_extract(r.value,'$.source.date')
    OR json_type(r.value,'$.source.amount') IS NOT 'text'
    OR accounting_money_cents(json_extract(r.value,'$.source.amount'))<=0
    OR coalesce(json_extract(r.value,'$.source.direction'),'') NOT IN('income','expense'))
  AND NEW.closing_cents=NEW.opening_cents+(SELECT sum(
   accounting_money_cents(json_extract(r.value,'$.source.amount'))
    *CASE WHEN json_extract(r.value,'$.source.direction')='income' THEN 1 ELSE -1 END)
   FROM json_each(p.payload_json,'$.rows') r))
BEGIN SELECT RAISE(ABORT,'Bank coverage requires valid same-period rows and opening plus net equals closing'); END;

CREATE TRIGGER c01_bank_coverage_insert_revision AFTER INSERT ON bank_statement_coverages
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
CREATE TRIGGER c01_bank_coverage_update_revision AFTER UPDATE ON bank_statement_coverages
BEGIN
 INSERT INTO close_dependency_revisions(scope_kind,scope_id,revision) VALUES('book',NEW.book_id,1)
 ON CONFLICT(scope_kind,scope_id) DO UPDATE SET revision=revision+1;
END;
