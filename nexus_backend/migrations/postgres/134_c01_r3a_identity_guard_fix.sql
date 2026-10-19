-- compatibility: incremental
-- Fix the generic identity guard: NEW.confirmed_by does not exist on every
-- guarded table, so inspect the row as JSONB instead of a typed field.
CREATE OR REPLACE FUNCTION accounting.subledger_control_identity_guard() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE value TEXT;
BEGIN
 value := to_jsonb(NEW)->>'confirmed_by';
 IF value IS NOT NULL AND value IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'confirmed_by must be current authenticated actor' USING ERRCODE='42501';
 END IF;
 value := to_jsonb(NEW)->>'created_by';
 IF value IS NOT NULL AND value IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'created_by must be current authenticated actor' USING ERRCODE='42501';
 END IF;
 value := to_jsonb(NEW)->>'verified_by';
 IF value IS NOT NULL AND value IS DISTINCT FROM accounting.actor_user() THEN
  RAISE EXCEPTION 'verified_by must be current authenticated actor' USING ERRCODE='42501';
 END IF;
 RETURN NEW;
END $$;
