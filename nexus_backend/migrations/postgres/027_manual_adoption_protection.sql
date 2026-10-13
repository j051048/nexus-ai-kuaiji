-- Manual adoption is human intervention, including records created before v7.
-- A terminal successful worker application is positive evidence of automation;
-- ambiguous historical human adoption is conservatively protected.
UPDATE accounting.drafts d SET last_manual_edit_version=version
 WHERE status='draft' AND last_manual_edit_version=0 AND (
  EXISTS(SELECT 1 FROM accounting.audit_events a WHERE a.book_id=d.book_id AND a.object_id=d.id AND a.action='draft.update')
  OR (EXISTS(SELECT 1 FROM accounting.audit_events a WHERE a.book_id=d.book_id AND a.object_id=d.id AND a.action='intelligence.proposal.apply')
   AND NOT EXISTS(SELECT 1 FROM accounting.ai_proposal_applications ap JOIN accounting.proposal_jobs j
    ON j.current_run_id=ap.run_id AND j.draft_id=ap.draft_id AND j.book_id=ap.book_id AND j.status='succeeded'
    WHERE ap.draft_id=d.id AND ap.book_id=d.book_id AND ap.applied_draft_version=d.version))
 );

CREATE FUNCTION accounting.guard_manual_draft_marker() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
 BEGIN
  IF NEW.last_manual_edit_version<OLD.last_manual_edit_version OR NEW.last_manual_edit_version>NEW.version
   OR (OLD.status<>'draft' AND NEW.last_manual_edit_version IS DISTINCT FROM OLD.last_manual_edit_version) THEN
   RAISE EXCEPTION 'Manual draft intervention marker cannot be cleared or changed after approval' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END
$$;
REVOKE ALL ON FUNCTION accounting.guard_manual_draft_marker() FROM PUBLIC;
CREATE TRIGGER draft_manual_marker_guard BEFORE UPDATE ON accounting.drafts FOR EACH ROW EXECUTE FUNCTION accounting.guard_manual_draft_marker();
