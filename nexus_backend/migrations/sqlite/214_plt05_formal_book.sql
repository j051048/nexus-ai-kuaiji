-- PLT05-A: formal book system is an approved policy decision.
ALTER TABLE accounting_policies ADD COLUMN formal_book_system TEXT NOT NULL DEFAULT 'unassessed'
 CHECK(formal_book_system IN('unassessed','local','external'));
ALTER TABLE accounting_policies ADD COLUMN formal_book_system_name TEXT NOT NULL DEFAULT '';

ALTER TABLE report_runs ADD COLUMN formal_book_system TEXT NOT NULL DEFAULT 'unassessed'
 CHECK(formal_book_system IN('unassessed','local','external'));
ALTER TABLE report_runs ADD COLUMN formal_book_system_name TEXT NOT NULL DEFAULT '';
