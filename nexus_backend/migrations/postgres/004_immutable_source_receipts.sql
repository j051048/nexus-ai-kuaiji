-- Originals, appended evidence and delivered receipts are immutable at the
-- runtime SQL boundary as well as in the application workflow.
REVOKE UPDATE,DELETE ON accounting.sources,accounting.source_evidence,accounting.exports FROM accounting_app;
DROP POLICY ledger_update ON accounting.sources;
DROP POLICY ledger_update ON accounting.source_evidence;
DROP POLICY ledger_update ON accounting.exports;
