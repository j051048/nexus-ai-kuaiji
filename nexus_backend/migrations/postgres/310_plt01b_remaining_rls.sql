-- compatibility: incremental
-- UPG Batch 01B: close the remaining GPT-audit P0/P1 RLS gaps.
--
-- bridge_return_imports / bridge_return_rows were still scoped by
-- company_visible(company_id) only, so any book of the same company could read
-- or attach a return file belonging to another book. Bind them to the book the
-- artifact was exported from, and to actor_firm()/actor_user().
--
-- created_by = actor_user() is enforced on INSERT only: 305 grants the running
-- role UPDATE (evidence_verified) so a reviewer who did not create the import
-- can still mark verified evidence. The UPDATE policy therefore keeps the
-- book/firm/artifact binding instead of the creator identity.
SET LOCAL search_path=accounting,pg_catalog;

DROP POLICY IF EXISTS bridge_return_imports_tenant ON accounting.bridge_return_imports;
DROP POLICY IF EXISTS bridge_return_imports_book_read ON accounting.bridge_return_imports;
DROP POLICY IF EXISTS bridge_return_imports_book_write ON accounting.bridge_return_imports;
DROP POLICY IF EXISTS bridge_return_imports_book_update ON accounting.bridge_return_imports;

CREATE POLICY bridge_return_imports_book_read ON accounting.bridge_return_imports
  FOR SELECT TO accounting_app
  USING (
    accounting.book_allowed(bridge_return_imports.book_id)
    AND bridge_return_imports.firm_id = accounting.actor_firm()
  );

CREATE POLICY bridge_return_imports_book_write ON accounting.bridge_return_imports
  FOR INSERT TO accounting_app
  WITH CHECK (
    accounting.book_allowed(bridge_return_imports.book_id, true)
    AND bridge_return_imports.firm_id = accounting.actor_firm()
    AND bridge_return_imports.created_by = accounting.actor_user()
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_export_artifacts a
      WHERE a.id = bridge_return_imports.artifact_id
        AND a.book_id = bridge_return_imports.book_id
        AND a.company_id = bridge_return_imports.company_id
        AND a.firm_id = accounting.actor_firm()
    )
  );

CREATE POLICY bridge_return_imports_book_update ON accounting.bridge_return_imports
  FOR UPDATE TO accounting_app
  USING (
    accounting.book_allowed(bridge_return_imports.book_id, true)
    AND bridge_return_imports.firm_id = accounting.actor_firm()
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_export_artifacts a
      WHERE a.id = bridge_return_imports.artifact_id
        AND a.book_id = bridge_return_imports.book_id
        AND a.company_id = bridge_return_imports.company_id
        AND a.firm_id = accounting.actor_firm()
    )
  )
  WITH CHECK (
    accounting.book_allowed(bridge_return_imports.book_id, true)
    AND bridge_return_imports.firm_id = accounting.actor_firm()
    AND EXISTS (
      SELECT 1 FROM accounting.bridge_export_artifacts a
      WHERE a.id = bridge_return_imports.artifact_id
        AND a.book_id = bridge_return_imports.book_id
        AND a.company_id = bridge_return_imports.company_id
        AND a.firm_id = accounting.actor_firm()
    )
  );

DROP POLICY IF EXISTS bridge_return_rows_tenant ON accounting.bridge_return_rows;
DROP POLICY IF EXISTS bridge_return_rows_book_read ON accounting.bridge_return_rows;
DROP POLICY IF EXISTS bridge_return_rows_book_write ON accounting.bridge_return_rows;

CREATE POLICY bridge_return_rows_book_read ON accounting.bridge_return_rows
  FOR SELECT TO accounting_app
  USING (
    EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = bridge_return_rows.import_id
        AND accounting.book_allowed(i.book_id)
        AND i.firm_id = accounting.actor_firm()
    )
  );

CREATE POLICY bridge_return_rows_book_write ON accounting.bridge_return_rows
  FOR INSERT TO accounting_app
  WITH CHECK (
    EXISTS (
      SELECT 1 FROM accounting.bridge_return_imports i
      WHERE i.id = bridge_return_rows.import_id
        AND accounting.book_allowed(i.book_id, true)
        AND i.firm_id = accounting.actor_firm()
    )
  );

-- fee_source_relations: 307 scoped only by book_allowed(); a relation row also
-- carries firm_id and the verifier, so the same book id reached through another
-- firm (or another user claiming verification) must not pass.
DROP POLICY IF EXISTS fee_source_relations_read ON accounting.fee_source_relations;
DROP POLICY IF EXISTS fee_source_relations_write ON accounting.fee_source_relations;

CREATE POLICY fee_source_relations_read ON accounting.fee_source_relations
  FOR SELECT TO accounting_app
  USING (
    accounting.book_allowed(fee_source_relations.book_id)
    AND fee_source_relations.firm_id = accounting.actor_firm()
  );

CREATE POLICY fee_source_relations_write ON accounting.fee_source_relations
  FOR INSERT TO accounting_app
  WITH CHECK (
    accounting.book_allowed(fee_source_relations.book_id, true)
    AND fee_source_relations.firm_id = accounting.actor_firm()
    AND fee_source_relations.verified_by = accounting.actor_user()
  );

-- report_reconciliation_notes: writing an explanation is a review act. Require
-- review authority, the writer's own identity, and a report of the same book and
-- firm (the note has no company column; report_runs owns that binding).
DROP POLICY IF EXISTS report_reconciliation_insert ON accounting.report_reconciliation_notes;

CREATE POLICY report_reconciliation_insert ON accounting.report_reconciliation_notes
  FOR INSERT TO accounting_app
  WITH CHECK (
    accounting.book_review_allowed(report_reconciliation_notes.book_id)
    AND report_reconciliation_notes.created_by = accounting.actor_user()
    AND EXISTS (
      SELECT 1 FROM accounting.report_runs r
      WHERE r.id = report_reconciliation_notes.report_id
        AND r.book_id = report_reconciliation_notes.book_id
    )
  );
