-- compatibility: incremental
-- 税务底稿守卫加固（与 PostgreSQL 170 同语义）。
CREATE TRIGGER IF NOT EXISTS tax_workpaper_insert_draft_only
BEFORE INSERT ON tax_workpapers
WHEN NEW.status<>'draft' OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
  OR NEW.review_reason<>''
BEGIN
  SELECT RAISE(ABORT,'New tax workpaper must be an unreviewed draft');
END;

CREATE TRIGGER IF NOT EXISTS tax_workpaper_identity_immutable
BEFORE UPDATE ON tax_workpapers
WHEN NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id
  OR NEW.book_id<>OLD.book_id OR NEW.period<>OLD.period OR NEW.version<>OLD.version
  OR NEW.profile_id<>OLD.profile_id OR NEW.created_by<>OLD.created_by
  OR NEW.created_at<>OLD.created_at OR NEW.source_digest<>OLD.source_digest
BEGIN
  SELECT RAISE(ABORT,'Tax workpaper identity is immutable');
END;

CREATE TRIGGER IF NOT EXISTS tax_workpaper_review_needs_evidence
BEFORE UPDATE ON tax_workpapers
WHEN OLD.status='draft' AND NEW.status='reviewed'
 AND (NEW.reviewed_by IS NULL OR NEW.reviewed_at IS NULL
      OR length(trim(NEW.review_reason))<3 OR NEW.reviewed_by=OLD.created_by)
BEGIN
  SELECT RAISE(ABORT,'Review needs an independent reviewer and opinion');
END;
