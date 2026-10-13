CREATE TRIGGER disposition_active_case_guard BEFORE INSERT ON bank_source_dispositions
 WHEN EXISTS(SELECT 1 FROM case_tasks t WHERE t.book_id=NEW.book_id AND t.source_id=NEW.source_id AND t.state<>'closed')
 BEGIN SELECT RAISE(ABORT,'original active exception or supplement must finish before duplicate isolation'); END;
