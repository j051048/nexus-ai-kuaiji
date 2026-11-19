-- compatibility: incremental
-- PLT05-C: 返回差异进入统一待办投影后，返回结果文件与逐笔处置的写入必须把企业
-- 标记为脏。两张源表都是只追加的：导入时新增逐笔差异，处置时新增 handler 让差异
-- 从待办消失；因此 INSERT 触发器即可覆盖投影可见行的全部变化。
CREATE TRIGGER IF NOT EXISTS wp_i_bridge_return_imports AFTER INSERT ON bridge_return_imports BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER IF NOT EXISTS wp_i_bridge_return_row_handlers AFTER INSERT ON bridge_return_row_handlers BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
