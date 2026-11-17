-- OPT06-B: optional firm policy that removes manager review/edit shortcuts.
ALTER TABLE firm_review_controls
  ADD COLUMN management_review_separation INTEGER NOT NULL DEFAULT 0
  CHECK(management_review_separation IN (0,1));
