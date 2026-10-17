-- compatibility: incremental
-- 投影运营化：记录最近一次成功构建时间与连续失败次数，供运维诊断与告警。
ALTER TABLE workspace_projection_state ADD COLUMN built_at TEXT NOT NULL DEFAULT '';
ALTER TABLE workspace_projection_state ADD COLUMN failures INTEGER NOT NULL DEFAULT 0;
