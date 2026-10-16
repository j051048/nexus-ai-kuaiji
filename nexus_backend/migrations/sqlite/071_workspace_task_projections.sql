-- compatibility: incremental
-- 统一待办投影：把 14 路实时 UNION 计算物化为单表索引查询。投影只保存
-- 与操作者无关的任务行与可见性键；授权仍由读取时的实时授权谓词与（PostgreSQL）
-- 逐行 RLS 决定。任何源表写入都会把该企业标记为脏；读取路径在干净时使用投影，
-- 脏时回退到实时查询，因此正确性从不依赖投影的新鲜度。
CREATE TABLE workspace_projection_state(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 1,built_revision INTEGER NOT NULL DEFAULT 0,
 dirty_at TEXT NOT NULL,
 PRIMARY KEY(firm_id,company_id)
);
CREATE TABLE workspace_task_projections(
 firm_id TEXT NOT NULL,company_id TEXT NOT NULL,
 kind TEXT NOT NULL,ref TEXT NOT NULL,
 book_id TEXT,event_id TEXT,draft_id TEXT,batch_id TEXT,
 applicant_id TEXT,department_id TEXT,assignee_id TEXT,recipient_id TEXT,created_by TEXT,
 submission_mode TEXT NOT NULL DEFAULT '',ref_kind TEXT NOT NULL DEFAULT '',
 ref_detail TEXT NOT NULL DEFAULT '',
 source_open INTEGER NOT NULL DEFAULT 0,
 state TEXT NOT NULL,title TEXT NOT NULL,due_on TEXT,updated_at TEXT NOT NULL,
 amount_cents INTEGER,
 PRIMARY KEY(firm_id,company_id,kind,ref)
);
CREATE INDEX idx_workspace_projection_kind ON workspace_task_projections(firm_id,kind,company_id);
CREATE INDEX idx_workspace_projection_book ON workspace_task_projections(firm_id,book_id);
CREATE TRIGGER wp_i_intake_drafts AFTER INSERT ON intake_drafts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_intake_drafts AFTER UPDATE ON intake_drafts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_intake_drafts AFTER DELETE ON intake_drafts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_intake_runs AFTER INSERT ON intake_runs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_intake_runs AFTER UPDATE ON intake_runs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_intake_runs AFTER DELETE ON intake_runs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_business_events AFTER INSERT ON business_events BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_business_events AFTER UPDATE ON business_events BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_business_events AFTER DELETE ON business_events BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_business_mappings AFTER INSERT ON business_mappings BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_business_mappings AFTER UPDATE ON business_mappings BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_business_mappings AFTER DELETE ON business_mappings BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_case_tasks AFTER INSERT ON case_tasks BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_case_tasks AFTER UPDATE ON case_tasks BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_case_tasks AFTER DELETE ON case_tasks BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_case_responses AFTER INSERT ON case_responses BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_case_responses AFTER UPDATE ON case_responses BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_case_responses AFTER DELETE ON case_responses BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_ai_bank_links AFTER INSERT ON ai_bank_links BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_ai_bank_links AFTER UPDATE ON ai_bank_links BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_ai_bank_links AFTER DELETE ON ai_bank_links BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_proposal_jobs AFTER INSERT ON proposal_jobs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_proposal_jobs AFTER UPDATE ON proposal_jobs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_proposal_jobs AFTER DELETE ON proposal_jobs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_ocr_jobs AFTER INSERT ON ocr_jobs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_ocr_jobs AFTER UPDATE ON ocr_jobs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_ocr_jobs AFTER DELETE ON ocr_jobs BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_delivery_receipts AFTER INSERT ON delivery_receipts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_delivery_receipts AFTER UPDATE ON delivery_receipts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_delivery_receipts AFTER DELETE ON delivery_receipts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_ops_alerts AFTER INSERT ON ops_alerts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_ops_alerts AFTER UPDATE ON ops_alerts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_ops_alerts AFTER DELETE ON ops_alerts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_material_requests AFTER INSERT ON material_requests BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_material_requests AFTER UPDATE ON material_requests BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_material_requests AFTER DELETE ON material_requests BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_material_request_actions AFTER INSERT ON material_request_actions BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_material_request_actions AFTER UPDATE ON material_request_actions BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_material_request_actions AFTER DELETE ON material_request_actions BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(OLD.firm_id,OLD.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_companies AFTER INSERT ON companies BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT fc.firm_id,NEW.id,1,0,datetime('now') FROM firm_clients fc WHERE fc.company_id=NEW.id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_companies AFTER UPDATE ON companies BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT fc.firm_id,NEW.id,1,0,datetime('now') FROM firm_clients fc WHERE fc.company_id=NEW.id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_firm_clients AFTER INSERT ON firm_clients BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_firm_clients AFTER UPDATE ON firm_clients BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) VALUES(NEW.firm_id,NEW.company_id,1,0,datetime('now')) ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_drafts AFTER INSERT ON drafts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_drafts AFTER UPDATE ON drafts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_drafts AFTER DELETE ON drafts BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=OLD.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_sources AFTER INSERT ON sources BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_sources AFTER UPDATE ON sources BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_d_sources AFTER DELETE ON sources BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=OLD.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_periods AFTER INSERT ON periods BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_periods AFTER UPDATE ON periods BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_exports AFTER INSERT ON exports BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_u_exports AFTER UPDATE ON exports BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
CREATE TRIGGER wp_i_bank_source_dispositions AFTER INSERT ON bank_source_dispositions BEGIN
 INSERT INTO workspace_projection_state(firm_id,company_id,revision,built_revision,dirty_at) SELECT b.firm_id,b.company_id,1,0,datetime('now') FROM books b WHERE b.id=NEW.book_id ON CONFLICT(firm_id,company_id) DO UPDATE SET revision=revision+1,dirty_at=datetime('now');END;
