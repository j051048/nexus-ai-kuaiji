-- compatibility: breaking
-- Internal human tasks do not select a delivery grant. Require the exact JSON
-- key set, including explicitly null bindings, instead of treating omission as null.
DROP TRIGGER IF EXISTS delivery_notification_guard_ins;

CREATE TRIGGER IF NOT EXISTS delivery_notification_guard_ins BEFORE INSERT ON delivery_notifications
BEGIN
 SELECT CASE WHEN NOT EXISTS(
   SELECT 1 FROM books b JOIN periods p ON p.book_id=b.id AND p.period=NEW.period
   WHERE b.id=NEW.book_id AND b.firm_id=NEW.firm_id AND b.company_id=NEW.company_id)
  THEN RAISE(ABORT,'Notification must match firm/company/book/period') END;
 SELECT CASE WHEN NEW.state<>'pending' OR NEW.attempts<>0 OR NEW.worker_id IS NOT NULL
   OR NEW.lease_token IS NOT NULL OR NEW.lease_until IS NOT NULL
   OR NEW.send_started_at IS NOT NULL OR NEW.dispatched_at IS NOT NULL
   OR NEW.sink_ref<>'' OR NEW.legacy_payload_json<>''
  THEN RAISE(ABORT,'Notification must start pending and unsent') END;
 SELECT CASE WHEN EXISTS(SELECT 1 FROM restore_state WHERE hold<>0)
   OR NEW.restore_epoch IS NOT COALESCE((SELECT restore_id FROM restore_state WHERE instance_id='restored'),'')
  THEN RAISE(ABORT,'Notification is bound to the current released restore epoch') END;
 SELECT CASE WHEN NEW.package_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM monthly_delivery_packages p WHERE p.id=NEW.package_id
   AND p.firm_id=NEW.firm_id AND p.company_id=NEW.company_id AND p.book_id=NEW.book_id
   AND p.period=NEW.period AND p.version=NEW.package_version AND p.manifest_sha256=NEW.manifest_sha256)
  THEN RAISE(ABORT,'Notification package version and manifest must match') END;
 SELECT CASE WHEN NEW.package_id IS NULL AND (NEW.package_version IS NOT NULL OR NEW.manifest_sha256 IS NOT NULL)
  THEN RAISE(ABORT,'Missing package cannot carry a version or manifest') END;
 SELECT CASE WHEN NEW.grant_id IS NOT NULL AND NOT EXISTS(
   SELECT 1 FROM monthly_delivery_grants g WHERE g.id=NEW.grant_id
   AND g.firm_id=NEW.firm_id AND g.company_id=NEW.company_id AND g.book_id=NEW.book_id
   AND g.period=NEW.period AND g.version=NEW.grant_version AND g.state='active'
   AND g.recipient_user_id IS NEW.recipient_user_id)
  THEN RAISE(ABORT,'Notification grant must match current source and recipient') END;
 SELECT CASE WHEN NEW.grant_id IS NULL AND (NEW.grant_version<>0 OR (NEW.kind<>'missing_contact_followup' AND EXISTS(
   SELECT 1 FROM monthly_delivery_grants g WHERE g.book_id=NEW.book_id AND g.period=NEW.period)))
  THEN RAISE(ABORT,'Legacy notification cannot bypass grant history') END;
 SELECT CASE WHEN NEW.kind IN('signature_reminder','signature_overdue') AND (
   NEW.package_id IS NULL OR NEW.recipient_user_id IS NULL OR NOT EXISTS(
     SELECT 1 FROM company_memberships cm JOIN users u ON u.id=cm.user_id
     WHERE cm.company_id=NEW.company_id AND cm.user_id=NEW.recipient_user_id AND u.active=1)
   OR NOT EXISTS(SELECT 1 FROM monthly_delivery_packages p WHERE p.id=NEW.package_id
      AND (p.contact_user_id=NEW.recipient_user_id OR (p.contact_user_id IS NULL AND NEW.grant_id IS NOT NULL)))
   OR EXISTS(SELECT 1 FROM monthly_delivery_acknowledgements a WHERE a.package_id=NEW.package_id))
  THEN RAISE(ABORT,'Reminder must bind an unsigned package and its eligible recipient') END;
 SELECT CASE WHEN json_valid(NEW.payload_json)<>1
  THEN RAISE(ABORT,'Notification payload must be valid JSON') END;
 SELECT CASE WHEN json_type(NEW.payload_json)<>'object'
   OR (SELECT COUNT(*) FROM json_each(NEW.payload_json))<>14
   OR (SELECT COUNT(DISTINCT key) FROM json_each(NEW.payload_json))<>14
   OR EXISTS(SELECT 1 FROM json_each(NEW.payload_json) WHERE key NOT IN(
     'notification_id','firm_id','company_id','book_id','period','package_id','package_version',
     'manifest_sha256','recipient_user_id','grant_id','grant_version','restore_epoch','kind','reason'))
   OR json_extract(NEW.payload_json,'$.notification_id') IS NOT NEW.id
   OR json_extract(NEW.payload_json,'$.firm_id') IS NOT NEW.firm_id
   OR json_extract(NEW.payload_json,'$.company_id') IS NOT NEW.company_id
   OR json_extract(NEW.payload_json,'$.book_id') IS NOT NEW.book_id
   OR json_extract(NEW.payload_json,'$.period') IS NOT NEW.period
   OR json_extract(NEW.payload_json,'$.package_id') IS NOT NEW.package_id
   OR json_extract(NEW.payload_json,'$.package_version') IS NOT NEW.package_version
   OR json_extract(NEW.payload_json,'$.manifest_sha256') IS NOT NEW.manifest_sha256
   OR json_extract(NEW.payload_json,'$.recipient_user_id') IS NOT NEW.recipient_user_id
   OR json_extract(NEW.payload_json,'$.grant_id') IS NOT NEW.grant_id
   OR json_extract(NEW.payload_json,'$.grant_version') IS NOT NEW.grant_version
   OR json_type(NEW.payload_json,'$.grant_version')<>'integer'
   OR (NEW.package_id IS NOT NULL AND json_type(NEW.payload_json,'$.package_version')<>'integer')
   OR json_extract(NEW.payload_json,'$.restore_epoch') IS NOT NEW.restore_epoch
   OR json_extract(NEW.payload_json,'$.kind') IS NOT NEW.kind
   OR json_type(NEW.payload_json,'$.reason')<>'text'
   OR length(trim(json_extract(NEW.payload_json,'$.reason')))<3
  THEN RAISE(ABORT,'Notification payload must exactly bind immutable columns') END;
END;

