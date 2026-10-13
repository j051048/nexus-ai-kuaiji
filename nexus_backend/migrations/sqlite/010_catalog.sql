ALTER TABLE accounts ADD COLUMN category TEXT NOT NULL DEFAULT 'other' CHECK(category IN ('asset','liability','equity','cost','income','expense','other'));
ALTER TABLE accounts ADD COLUMN normal_balance TEXT NOT NULL DEFAULT 'debit' CHECK(normal_balance IN ('debit','credit'));
ALTER TABLE accounts ADD COLUMN auxiliary_requirements TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(auxiliary_requirements) AND json_type(auxiliary_requirements)='array');
ALTER TABLE accounts ADD COLUMN version INTEGER NOT NULL DEFAULT 1 CHECK(version>0);

CREATE TABLE journal_templates (
 id TEXT PRIMARY KEY,book_id TEXT NOT NULL REFERENCES books(id),active INTEGER NOT NULL CHECK(active IN (0,1)),
 current_version INTEGER NOT NULL CHECK(current_version>0),created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 UNIQUE(id,book_id)
);
CREATE TABLE journal_template_versions (
 template_id TEXT NOT NULL,book_id TEXT NOT NULL,version INTEGER NOT NULL CHECK(version>0),
 definition TEXT NOT NULL CHECK(json_valid(definition)),definition_fingerprint TEXT NOT NULL,
 created_by TEXT NOT NULL REFERENCES users(id),created_at TEXT NOT NULL,
 PRIMARY KEY(template_id,version),FOREIGN KEY(template_id,book_id) REFERENCES journal_templates(id,book_id)
);
CREATE INDEX idx_journal_templates_book ON journal_templates(book_id,active);
CREATE TRIGGER journal_template_versions_immutable_update BEFORE UPDATE ON journal_template_versions BEGIN
 SELECT RAISE(ABORT,'journal template history is immutable'); END;
CREATE TRIGGER journal_template_versions_immutable_delete BEFORE DELETE ON journal_template_versions BEGIN
 SELECT RAISE(ABORT,'journal template history is immutable'); END;
CREATE TABLE draft_line_auxiliaries (
 draft_id TEXT NOT NULL,book_id TEXT NOT NULL,ordinal INTEGER NOT NULL,
 values_json TEXT NOT NULL CHECK(json_valid(values_json) AND json_type(values_json)='object'),
 PRIMARY KEY(draft_id,ordinal),FOREIGN KEY(draft_id,ordinal) REFERENCES draft_lines(draft_id,ordinal) ON DELETE CASCADE,
 FOREIGN KEY(draft_id,book_id) REFERENCES drafts(id,book_id)
);
CREATE TRIGGER draft_aux_insert_guard BEFORE INSERT ON draft_line_auxiliaries BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=NEW.draft_id AND book_id=NEW.book_id AND status='draft')
 THEN RAISE(ABORT,'approved auxiliary values are immutable') END; END;
CREATE TRIGGER draft_aux_update_guard BEFORE UPDATE ON draft_line_auxiliaries BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=OLD.draft_id AND book_id=OLD.book_id AND status='draft')
 OR NEW.draft_id<>OLD.draft_id OR NEW.book_id<>OLD.book_id OR NEW.ordinal<>OLD.ordinal
 THEN RAISE(ABORT,'approved auxiliary values are immutable') END; END;
CREATE TRIGGER draft_aux_delete_guard BEFORE DELETE ON draft_line_auxiliaries BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM drafts WHERE id=OLD.draft_id AND book_id=OLD.book_id AND status='draft')
 THEN RAISE(ABORT,'approved auxiliary values are immutable') END; END;
