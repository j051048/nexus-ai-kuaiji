-- PLT05-A: formal book system is an approved policy decision.
ALTER TABLE accounting.accounting_policies
 ADD COLUMN formal_book_system TEXT NOT NULL DEFAULT 'unassessed'
 CONSTRAINT accounting_policies_formal_book_system_check
 CHECK(formal_book_system IN('unassessed','local','external'));
ALTER TABLE accounting.accounting_policies
 ADD COLUMN formal_book_system_name TEXT NOT NULL DEFAULT '';

ALTER TABLE accounting.report_runs
 ADD COLUMN formal_book_system TEXT NOT NULL DEFAULT 'unassessed'
 CONSTRAINT report_runs_formal_book_system_check
 CHECK(formal_book_system IN('unassessed','local','external'));
ALTER TABLE accounting.report_runs
 ADD COLUMN formal_book_system_name TEXT NOT NULL DEFAULT '';
