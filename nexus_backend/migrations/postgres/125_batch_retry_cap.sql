-- compatibility: incremental
-- R05/F11: database backstop for the application retry cap. A worker that
-- writes retry_wait after max_attempts is converted to failed/attention.
CREATE OR REPLACE FUNCTION accounting.batch_retry_cap() RETURNS TRIGGER
 LANGUAGE plpgsql SET search_path=pg_catalog,accounting,pg_temp AS $$
BEGIN
 IF NEW.status='retry_wait' AND NEW.attempt>=NEW.max_attempts THEN
  NEW.status:='failed';
  NEW.error_code:='max_attempts';
  NEW.error_detail:='达到重试上限，已转人工处理';
  NEW.finished_at:=to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"');
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS batch_retry_cap ON accounting.batch_operation_items;
CREATE TRIGGER batch_retry_cap BEFORE UPDATE OF status ON accounting.batch_operation_items
 FOR EACH ROW EXECUTE FUNCTION accounting.batch_retry_cap();
