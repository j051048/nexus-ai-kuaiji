-- compatibility: incremental
-- RWK04-B: 防止未核定政策的历史报表或漏写 mode 的新行冒充正式报表发布。
-- 已发布的历史报表保持已发布与可读；未发布且缺少政策版本/批准人的行禁止发布；
-- 新插入 formal 行必须携带 policy_version 与 policy_approver。
DROP TRIGGER IF EXISTS report_runs_formal_publish_insert;
CREATE TRIGGER report_runs_formal_publish_insert
BEFORE INSERT ON report_runs
WHEN NEW.status='published'
 AND (NEW.mode<>'formal' OR NEW.policy_version IS NULL OR COALESCE(NEW.policy_approver,'')='')
BEGIN
    SELECT RAISE(ABORT, '试算报表不能发布（历史未核定报表同样不可新发布）');
END;

DROP TRIGGER IF EXISTS report_runs_formal_publish_update;
CREATE TRIGGER report_runs_formal_publish_update
BEFORE UPDATE OF status ON report_runs
WHEN NEW.status='published'
 AND (NEW.mode<>'formal' OR NEW.policy_version IS NULL OR COALESCE(NEW.policy_approver,'')='')
BEGIN
    SELECT RAISE(ABORT, '试算报表不能发布（历史未核定报表同样不可新发布）');
END;

DROP TRIGGER IF EXISTS report_runs_formal_requires_policy_insert;
CREATE TRIGGER report_runs_formal_requires_policy_insert
BEFORE INSERT ON report_runs
WHEN NEW.mode='formal'
 AND (NEW.policy_version IS NULL OR COALESCE(NEW.policy_approver,'')='')
BEGIN
    SELECT RAISE(ABORT, '正式报表必须绑定已批准的核算政策');
END;
