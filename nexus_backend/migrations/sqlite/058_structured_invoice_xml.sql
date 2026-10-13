-- Keep every original, identifier and foreign key while adding a bounded XML MIME.
CREATE TABLE intake_attachments_xml_upgrade(
 id TEXT PRIMARY KEY,intake_id TEXT NOT NULL,firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 storage_key TEXT NOT NULL,filename TEXT NOT NULL CHECK(length(filename) BETWEEN 1 AND 150),
 media_type TEXT NOT NULL CHECK(media_type IN ('application/pdf','image/png','image/jpeg','image/webp','application/xml')),
 sha256 TEXT NOT NULL CHECK(length(sha256)=64),size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 1 AND 10485760),created_at TEXT NOT NULL,created_by TEXT NOT NULL REFERENCES users(id),
 UNIQUE(intake_id,sha256),UNIQUE(id,intake_id),FOREIGN KEY(intake_id,firm_id,company_id) REFERENCES intake_drafts(id,firm_id,company_id)
);
INSERT INTO intake_attachments_xml_upgrade SELECT * FROM intake_attachments;
PRAGMA legacy_alter_table=ON;
DROP TABLE intake_attachments;
ALTER TABLE intake_attachments_xml_upgrade RENAME TO intake_attachments;
PRAGMA legacy_alter_table=OFF;
CREATE TRIGGER intake_attachments_no_update BEFORE UPDATE ON intake_attachments BEGIN SELECT RAISE(ABORT,'Intake originals are immutable'); END;
CREATE TRIGGER intake_attachments_no_delete BEFORE DELETE ON intake_attachments BEGIN SELECT RAISE(ABORT,'Intake originals are immutable'); END;
CREATE TRIGGER intake_attachments_open BEFORE INSERT ON intake_attachments WHEN NOT EXISTS(SELECT 1 FROM intake_drafts d WHERE d.id=NEW.intake_id AND d.status='draft' AND d.created_by=NEW.created_by) OR (SELECT count(*) FROM intake_attachments WHERE intake_id=NEW.intake_id)>=10 OR (SELECT coalesce(sum(size_bytes),0) FROM intake_attachments WHERE intake_id=NEW.intake_id)+NEW.size_bytes>52428800
 BEGIN SELECT RAISE(ABORT,'Intake attachment scope or limits invalid'); END;
