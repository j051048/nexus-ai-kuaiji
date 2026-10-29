-- compatibility: breaking
-- Reviewers can cancel unsent tasks, never fabricate worker attempt evidence.
CREATE OR REPLACE FUNCTION accounting.delivery_notification_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SECURITY DEFINER SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE payload JSONB; expected JSONB; worker BOOLEAN; b RECORD;
BEGIN
 PERFORM accounting.lock_legacy_write();
 worker:=current_setting('role',true)='accounting_worker' AND pg_has_role(session_user,'accounting_worker','MEMBER');
 IF TG_OP='UPDATE' THEN
  IF (NEW.id,NEW.firm_id,NEW.company_id,NEW.book_id,NEW.period,NEW.package_id,NEW.package_version,NEW.manifest_sha256,
   NEW.kind,NEW.recipient_user_id,NEW.grant_id,NEW.grant_version,NEW.restore_epoch,NEW.dedupe_key,NEW.payload_json,
   NEW.created_by,NEW.created_at,NEW.legacy_payload_json)
   IS DISTINCT FROM
   (OLD.id,OLD.firm_id,OLD.company_id,OLD.book_id,OLD.period,OLD.package_id,OLD.package_version,OLD.manifest_sha256,
   OLD.kind,OLD.recipient_user_id,OLD.grant_id,OLD.grant_version,OLD.restore_epoch,OLD.dedupe_key,OLD.payload_json,
   OLD.created_by,OLD.created_at,OLD.legacy_payload_json) THEN
   RAISE EXCEPTION 'Delivery notification identity is immutable' USING ERRCODE='23514'; END IF;
  IF OLD.state IN('sink_written','cancelled','unknown') THEN
   RAISE EXCEPTION 'Terminal delivery notification cannot restart' USING ERRCODE='23514'; END IF;
  IF NOT worker THEN
   IF NEW.firm_id IS DISTINCT FROM accounting.actor_firm() OR NOT accounting.book_review_allowed(NEW.book_id) THEN
    RAISE EXCEPTION 'Current notification review permission required' USING ERRCODE='42501'; END IF;
   IF NEW.state<>'cancelled' OR OLD.state NOT IN('pending','failed','claimed')
    OR (NEW.attempts,NEW.sink_ref,NEW.dispatched_at,NEW.send_started_at,NEW.available_at,NEW.worker_id,NEW.lease_token,NEW.lease_until)
       IS DISTINCT FROM (OLD.attempts,OLD.sink_ref,OLD.dispatched_at,OLD.send_started_at,OLD.available_at,OLD.worker_id,OLD.lease_token,OLD.lease_until) THEN
    RAISE EXCEPTION 'Only unsent notification cancellation is allowed' USING ERRCODE='23514'; END IF;
  ELSE
   IF NOT ((OLD.state IN('pending','failed') AND NEW.state IN('claimed','cancelled','unknown'))
     OR (OLD.state='claimed' AND NEW.state IN('sending','pending','cancelled','unknown'))
     OR (OLD.state='sending' AND NEW.state IN('sink_written','failed','unknown'))) THEN
    RAISE EXCEPTION 'Invalid notification state transition' USING ERRCODE='23514'; END IF;
   IF NEW.attempts<>OLD.attempts+(CASE WHEN OLD.state='claimed' AND NEW.state='sending' THEN 1 ELSE 0 END) THEN
    RAISE EXCEPTION 'Notification attempts only advance at send boundary' USING ERRCODE='23514'; END IF;
  END IF;
  RETURN NEW;
 END IF;
 IF worker OR NEW.created_by IS DISTINCT FROM accounting.actor_user() OR NEW.firm_id IS DISTINCT FROM accounting.actor_firm()
 OR NOT accounting.book_review_allowed(NEW.book_id) THEN
  RAISE EXCEPTION 'Current notification creator and review permission required' USING ERRCODE='42501'; END IF;
 SELECT * INTO b FROM accounting.books WHERE id=NEW.book_id;
 IF NOT FOUND OR b.firm_id IS DISTINCT FROM NEW.firm_id OR b.company_id IS DISTINCT FROM NEW.company_id
 OR NOT EXISTS(SELECT 1 FROM accounting.periods WHERE book_id=NEW.book_id AND period=NEW.period)
 OR NEW.period !~ '^20[0-9]{2}-(0[1-9]|1[0-2])$' THEN
  RAISE EXCEPTION 'Notification scope or period differs' USING ERRCODE='23514'; END IF;
 IF EXISTS(SELECT 1 FROM accounting.restore_state WHERE hold) OR NEW.restore_epoch IS DISTINCT FROM accounting.n03_restore_epoch() THEN
  RAISE EXCEPTION 'Restore hold or epoch prevents notification creation' USING ERRCODE='55000'; END IF;
 IF NEW.state<>'pending' OR NEW.attempts<>0 OR NEW.sink_ref<>'' OR NEW.legacy_payload_json<>''
 OR NEW.worker_id IS NOT NULL OR NEW.lease_token IS NOT NULL OR NEW.lease_until IS NOT NULL
 OR NEW.send_started_at IS NOT NULL OR NEW.dispatched_at IS NOT NULL OR NEW.available_at<>'' THEN
  RAISE EXCEPTION 'Notifications must start as unattempted pending evidence' USING ERRCODE='23514'; END IF;
 BEGIN payload:=NEW.payload_json::jsonb; EXCEPTION WHEN OTHERS THEN
  RAISE EXCEPTION 'Invalid notification payload' USING ERRCODE='23514'; END;
 expected:=jsonb_build_object('notification_id',NEW.id,'firm_id',NEW.firm_id,'company_id',NEW.company_id,
 'book_id',NEW.book_id,'period',NEW.period,'package_id',NEW.package_id,'package_version',NEW.package_version,
 'manifest_sha256',NEW.manifest_sha256,'recipient_user_id',NEW.recipient_user_id,'grant_id',NEW.grant_id,
 'grant_version',NEW.grant_version,'restore_epoch',NEW.restore_epoch,'kind',NEW.kind,'reason',payload->'reason');
 IF payload IS DISTINCT FROM expected OR jsonb_typeof(payload->'reason') IS DISTINCT FROM 'string'
 OR length(btrim(payload->>'reason')) NOT BETWEEN 3 AND 1000 THEN
  RAISE EXCEPTION 'Notification payload differs from frozen scope' USING ERRCODE='23514'; END IF;
 IF NEW.kind='missing_contact_followup' THEN
  IF NEW.package_id IS NOT NULL OR NEW.package_version IS NOT NULL OR NEW.manifest_sha256 IS NOT NULL
   OR NEW.recipient_user_id IS NOT NULL OR NEW.grant_id IS NOT NULL OR NEW.grant_version<>0 THEN
   RAISE EXCEPTION 'Internal followup cannot carry an outbound recipient' USING ERRCODE='23514'; END IF;
 ELSIF NOT accounting.n03_notification_current(NEW) THEN
  RAISE EXCEPTION 'Notification package recipient grant binding is stale or invalid' USING ERRCODE='23514';
 END IF;
 IF NEW.package_id IS NOT NULL AND EXISTS(SELECT 1 FROM accounting.delivery_notifications prior
 WHERE prior.package_id=NEW.package_id AND prior.recipient_user_id=NEW.recipient_user_id
 AND prior.state IN('claimed','sending','unknown')) THEN
  RAISE EXCEPTION 'Prior delivery attempt unresolved; no replacement retry allowed' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
