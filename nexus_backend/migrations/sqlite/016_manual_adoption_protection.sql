UPDATE drafts SET last_manual_edit_version=version
 WHERE status='draft' AND last_manual_edit_version=0 AND (
  EXISTS(SELECT 1 FROM audit_events a WHERE a.book_id=drafts.book_id AND a.object_id=drafts.id AND a.action='draft.update')
  OR (EXISTS(SELECT 1 FROM audit_events a WHERE a.book_id=drafts.book_id AND a.object_id=drafts.id AND a.action='intelligence.proposal.apply')
   AND NOT EXISTS(SELECT 1 FROM ai_proposal_applications ap JOIN proposal_jobs j
    ON j.current_run_id=ap.run_id AND j.draft_id=ap.draft_id AND j.book_id=ap.book_id AND j.status='succeeded'
    WHERE ap.draft_id=drafts.id AND ap.book_id=drafts.book_id AND ap.applied_draft_version=drafts.version))
 );
CREATE TRIGGER draft_manual_marker_guard BEFORE UPDATE ON drafts
 WHEN NEW.last_manual_edit_version<OLD.last_manual_edit_version OR NEW.last_manual_edit_version>NEW.version
 OR (OLD.status<>'draft' AND NEW.last_manual_edit_version IS NOT OLD.last_manual_edit_version)
 BEGIN SELECT RAISE(ABORT,'Manual draft intervention marker cannot be cleared or changed after approval'); END;
