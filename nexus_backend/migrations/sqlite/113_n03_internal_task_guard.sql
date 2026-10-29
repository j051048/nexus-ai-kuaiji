-- compatibility: breaking
-- Match the native PostgreSQL boundary: internal human tasks cannot carry
-- outbound addressing, and retry scheduling is assigned only after an attempt.
CREATE TRIGGER IF NOT EXISTS delivery_notification_initial_contract_ins
BEFORE INSERT ON delivery_notifications
BEGIN
 SELECT CASE WHEN NEW.available_at<>''
  THEN RAISE(ABORT,'Notification must start without a retry schedule') END;
 SELECT CASE WHEN NEW.kind='missing_contact_followup' AND (
   NEW.package_id IS NOT NULL OR NEW.package_version IS NOT NULL
   OR NEW.manifest_sha256 IS NOT NULL OR NEW.recipient_user_id IS NOT NULL
   OR NEW.grant_id IS NOT NULL OR NEW.grant_version<>0)
  THEN RAISE(ABORT,'Internal followup cannot carry an outbound recipient') END;
END;
