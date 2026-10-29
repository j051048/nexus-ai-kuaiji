-- 税务底稿守卫加固：新建只能是不带复核痕迹的草稿，身份字段不可改写，
-- 复核必须留下独立复核人、时间与意见。
-- compatibility: incremental

CREATE OR REPLACE FUNCTION accounting.tax_workpaper_guard() RETURNS TRIGGER LANGUAGE plpgsql
 SET search_path=pg_catalog,accounting,pg_temp AS $$
DECLARE row_json JSONB;
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'Tax records are append-only' USING ERRCODE='42501';
 END IF;
 row_json:=to_jsonb(NEW);
 PERFORM accounting.lock_company_write(row_json->>'firm_id',row_json->>'company_id');
 IF TG_TABLE_NAME<>'tax_workpapers' THEN
  IF TG_OP='UPDATE' AND NEW.id<>OLD.id THEN
   RAISE EXCEPTION 'Tax profile identity is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 IF TG_OP='INSERT' THEN
  IF NEW.status<>'draft' OR NEW.reviewed_by IS NOT NULL OR NEW.reviewed_at IS NOT NULL
     OR NEW.review_reason<>'' THEN
   RAISE EXCEPTION 'New tax workpaper must be an unreviewed draft' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
 END IF;
 IF NEW.firm_id<>OLD.firm_id OR NEW.company_id<>OLD.company_id OR NEW.book_id<>OLD.book_id
 OR NEW.period<>OLD.period OR NEW.version<>OLD.version OR NEW.profile_id<>OLD.profile_id
 OR NEW.created_by<>OLD.created_by OR NEW.created_at<>OLD.created_at
 OR NEW.source_digest<>OLD.source_digest THEN
  RAISE EXCEPTION 'Tax workpaper identity is immutable' USING ERRCODE='23514';
 END IF;
 IF OLD.status<>'draft' OR NEW.status<>'reviewed' THEN
  RAISE EXCEPTION 'Only a draft workpaper can move to reviewed' USING ERRCODE='23514';
 END IF;
 IF NEW.reviewed_by IS NULL OR NEW.reviewed_at IS NULL
    OR length(btrim(NEW.review_reason))<3 THEN
  RAISE EXCEPTION 'Review needs reviewer, timestamp and opinion' USING ERRCODE='23514';
 END IF;
 IF NEW.reviewed_by=OLD.created_by THEN
  RAISE EXCEPTION 'Applicant cannot review own tax workpaper' USING ERRCODE='42501';
 END IF;
 IF NEW.output_net_cents<>OLD.output_net_cents OR NEW.output_tax_cents<>OLD.output_tax_cents
 OR NEW.input_net_cents<>OLD.input_net_cents OR NEW.input_tax_cents<>OLD.input_tax_cents
 OR NEW.tax_cents<>OLD.tax_cents OR NEW.surtax_cents<>OLD.surtax_cents
 OR NEW.credit_carry_cents<>OLD.credit_carry_cents OR NEW.ledger_cents<>OLD.ledger_cents
 OR NEW.variance_cents<>OLD.variance_cents OR NEW.lines_json<>OLD.lines_json THEN
  RAISE EXCEPTION 'Reviewed workpaper numbers are immutable' USING ERRCODE='23514';
 END IF;
 IF NEW.variance_cents<>0 AND btrim(NEW.variance_reason)='' THEN
  RAISE EXCEPTION 'Variance requires an explanation' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION accounting.tax_workpaper_guard() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION accounting.tax_workpaper_guard() TO accounting_app,accounting_worker,accounting_system;
