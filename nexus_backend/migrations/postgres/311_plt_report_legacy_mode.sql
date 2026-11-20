-- compatibility: incremental
-- RWK04-B: 扩展 report_runs.mode 支持 legacy（门槛建立前历史报表），将默认值改回安全的 trial，
-- 禁止未发布且缺政策依据的历史行或试算行发布；已发布的历史报表保持可读且标记 legacy。
SET LOCAL search_path=accounting,pg_catalog;

ALTER TABLE accounting.report_runs
  ALTER COLUMN mode SET DEFAULT 'trial';

ALTER TABLE accounting.report_runs
  DROP CONSTRAINT IF EXISTS report_runs_mode_check;

ALTER TABLE accounting.report_runs
  ADD CONSTRAINT report_runs_mode_check CHECK (mode IN ('trial', 'formal', 'legacy'));

CREATE OR REPLACE FUNCTION accounting.report_formal_publish_guard()
RETURNS trigger AS $$
BEGIN
    IF NEW.mode = 'formal' AND (NEW.policy_version IS NULL OR COALESCE(NEW.policy_approver, '') = '') THEN
        IF TG_OP = 'INSERT' THEN
            RAISE EXCEPTION '正式报表必须绑定已批准的核算政策' USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.status = 'published' AND (
        NEW.mode IS DISTINCT FROM 'formal'
        OR NEW.policy_version IS NULL
        OR COALESCE(NEW.policy_approver, '') = ''
    ) THEN
        RAISE EXCEPTION '试算报表不能发布（历史未核定报表同样不可新发布）' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS report_runs_formal_publish ON accounting.report_runs;
CREATE TRIGGER report_runs_formal_publish
BEFORE INSERT OR UPDATE OF status ON accounting.report_runs
FOR EACH ROW EXECUTE FUNCTION accounting.report_formal_publish_guard();
