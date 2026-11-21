-- compatibility: incremental
-- RWK04-B：PostgreSQL 用 313 回填 legacy；SQLite 的 CHECK 无法原地扩展
--（改它需要重建 report_runs 并重放 081/087/207/208/221 共十余个触发器，风险高于收益）。
-- 因此 SQLite 不改存储值，只保留读取口径：reports/service.py::effective_mode
-- 把「mode='formal' 且缺 policy_version/policy_approver」的行呈现为 legacy 并禁止发布。
SELECT 1;
