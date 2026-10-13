-- Local XML extraction reuses immutable runs, scoped caches and review controls.
ALTER TABLE accounting.intake_attachments DROP CONSTRAINT intake_attachments_media_type_check;
ALTER TABLE accounting.intake_attachments ADD CONSTRAINT intake_attachments_media_type_check
 CHECK(media_type IN ('application/pdf','image/png','image/jpeg','image/webp','application/xml'));
