-- compatibility: incremental
-- 投影运营化：记录最近一次成功构建时间与连续失败次数，供运维诊断与告警。
ALTER TABLE accounting.workspace_projection_state ADD COLUMN IF NOT EXISTS built_at TEXT NOT NULL DEFAULT '';
ALTER TABLE accounting.workspace_projection_state ADD COLUMN IF NOT EXISTS failures INTEGER NOT NULL DEFAULT 0;
