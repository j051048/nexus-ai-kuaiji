-- compatibility: incremental
-- RWK04-B: 回填「门槛建立前生成」的历史报表。
-- 口径：mode='formal' 且缺少 policy_version 或 policy_approver 的行，
-- 即 292 迁移把列默认值设为 formal 后被自动标成正式的存量行。
-- 只改 mode，不动 status：已发布的历史报表保持已发布与可读，界面上标注「门槛建立前发布」。
SET LOCAL search_path=accounting,pg_catalog;

DO $$
DECLARE
    affected integer;
BEGIN
    UPDATE accounting.report_runs
       SET mode = 'legacy'
     WHERE mode = 'formal'
       AND (policy_version IS NULL OR COALESCE(TRIM(policy_approver), '') = '');
    GET DIAGNOSTICS affected = ROW_COUNT;
    RAISE NOTICE 'report_runs legacy backfill: % row(s)', affected;
END
$$;
