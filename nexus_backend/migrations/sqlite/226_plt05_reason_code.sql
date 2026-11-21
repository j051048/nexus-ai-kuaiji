-- compatibility: incremental
-- PLT05-B: a difference disposition must carry a structured reason code, not
-- free text alone. Existing rows are stamped "legacy" rather than back-filled
-- with a guess: rewriting history to look compliant is worse than showing that
-- the older decision had no code.
ALTER TABLE bridge_return_row_handlers ADD COLUMN reason_code TEXT NOT NULL DEFAULT 'legacy';
