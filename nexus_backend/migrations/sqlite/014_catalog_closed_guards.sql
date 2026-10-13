CREATE TRIGGER catalog_account_insert_open BEFORE INSERT ON accounts BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=NEW.book_id AND p.status='open')
 THEN RAISE(ABORT,'catalog requires open period') END; END;
CREATE TRIGGER catalog_account_update_guard BEFORE UPDATE ON accounts BEGIN
 SELECT CASE WHEN NEW.book_id<>OLD.book_id OR NEW.code<>OLD.code THEN RAISE(ABORT,'account code and book are immutable') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=OLD.book_id AND p.status='open')
 THEN RAISE(ABORT,'catalog requires open period') END;
 SELECT CASE WHEN json(NEW.auxiliary_requirements)<>json(OLD.auxiliary_requirements) AND EXISTS(SELECT 1 FROM draft_lines WHERE book_id=OLD.book_id AND account_code=OLD.code)
 THEN RAISE(ABORT,'used account auxiliary requirements are immutable') END; END;
CREATE TRIGGER draft_aux_insert_open BEFORE INSERT ON draft_line_auxiliaries BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=NEW.book_id AND p.status='open')
 THEN RAISE(ABORT,'auxiliary writes require open period') END; END;
CREATE TRIGGER draft_aux_update_open BEFORE UPDATE ON draft_line_auxiliaries BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=OLD.book_id AND p.status='open')
 THEN RAISE(ABORT,'auxiliary writes require open period') END; END;
CREATE TRIGGER draft_aux_delete_open BEFORE DELETE ON draft_line_auxiliaries BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=OLD.book_id AND p.status='open')
 THEN RAISE(ABORT,'auxiliary writes require open period') END; END;
CREATE TRIGGER template_create_open BEFORE INSERT ON journal_templates BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=NEW.book_id AND p.status='open')
 THEN RAISE(ABORT,'catalog requires open period') END; END;
CREATE TRIGGER template_update_open BEFORE UPDATE ON journal_templates BEGIN
 SELECT CASE WHEN NEW.book_id<>OLD.book_id OR NEW.id<>OLD.id THEN RAISE(ABORT,'template identity is immutable') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=OLD.book_id AND p.status='open')
 THEN RAISE(ABORT,'catalog requires open period') END; END;
CREATE TRIGGER template_version_create_open BEFORE INSERT ON journal_template_versions BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=b.period WHERE b.id=NEW.book_id AND p.status='open')
 THEN RAISE(ABORT,'catalog requires open period') END; END;
