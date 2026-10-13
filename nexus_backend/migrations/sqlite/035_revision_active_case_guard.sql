CREATE TRIGGER revision_active_case_guard BEFORE INSERT ON fact_revision_lineage
 WHEN EXISTS(SELECT 1 FROM case_tasks t WHERE t.state<>'closed' AND
  (t.book_id=NEW.old_book_id AND (t.source_id=NEW.old_source_id OR t.draft_id=NEW.old_draft_id)
   OR t.event_id=NEW.event_id AND COALESCE(t.book_id,(SELECT book_id FROM business_mappings WHERE event_id=NEW.event_id))=NEW.old_book_id))
 BEGIN SELECT RAISE(ABORT,'original active exception or supplement must finish before fact replacement'); END;
