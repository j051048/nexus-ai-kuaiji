# 企业写锁范围与迁移向前兼容

## 1. 两级写锁

| 范围 | 使用时机 | 行为 |
|------|----------|------|
| 全局锁（默认） | 事务所级配置、跨企业批量、旧账迁移调度、任何未评审的写域 | `lock_legacy_write()`：全所串行 |
| 企业锁 `write_scope="company"` | 已评审的单企业写域 | 共享全局门 + 按企业 hash 的独立咨询锁，A/B 企业互不阻塞 |
| 系统企业锁（迁移 094） | 系统身份事务，投影刷新等已评审的系统任务 | `begin_system_company_write(firm, company)`：共享全局门 + 企业锁；必须显式 `firm_id`，仅 `accounting_system` 可执行 |

PostgreSQL 侧由 `accounting.company_write_table_guard` 逐行核对：
行所属公司必须等于本次事务声明的公司，越界直接 `42501`；
**未评审的表一旦出现在企业事务里会直接报错（fail-closed）**，不存在“悄悄绕过”。

### 已迁移的域（截至第三、四批）

- 第 1 批（pg 089）：员工录入、业务事项（含提交政策）、异常与补件、资料清单、发票导入。
- 第 2 批（pg 092）：凭证导入/复核/导出（`accounting/service.py`）、批量复核回执
  （`review.py` 按账套事务）、科目与记账模板（`catalog.py`）、银行导入向导
  （`import_wizard`，upload/preview/confirm）、重复来源隔离（`dispositions`）、
  关联回执（`reconciliation.confirm`，仅单企业批次；跨企业批次自动保持全局锁）。
- 第 3 批（pg 095）：月结预检/复核/锁期（`closing`）、交付清单/回执/备注
  （`delivery`）、收付款核销与冲销（`settlements`）、单企业月度参考余额/检查/
  固定事项（`monthly/service.py`，跨企业批量开期仍为全局锁）。
- 第 4 批（pg 097）：相似银行流水人工核验（`bank_duplicate_decisions`），以及
  单项目历史未结清偿的确认/冲销（`migration_clearance_commands/allocations/reversals`）。

### 明确保持全局锁（不要迁移）

`accounting/directory.py`、`admin_identity.py`、`admin_service.py`（事务所级配置）、
`monthly/batch.py`（跨企业批量开期）、`handover.py`（跨企业转派）、
`legacy_migration/*` 的跨企业调度（单项目确认/冲销已按企业锁隔离）、`jobs/*`（队列有独立 dispatch 门）、
`uploads.py`（事务所与用户级上传通道，无企业账务字段）、
`accounting/service.py::create_book`（一次事务里创建企业+账套+科目+长期账套映射）。

### 新增一个域的步骤

1. 列出该事务会写到的**全部**表，逐张确认能解析出唯一公司
   （直列 `company_id` / `book_id→books` / `event_id` / `case_id` / `request_id`）。
2. `accounting/database.py` 的 `transaction(...)` 传 `company_id=` 与 `write_scope="company"`；
   事务开始前不知道公司时，先做一次公司预读（见各服务的 `_company_of_book`）。
3. 新迁移里扩展 `company_write_table_guard` 的 CASE 映射；原生 SECURITY DEFINER 函数
   内部若还调用 `lock_legacy_write()`，用 pg 092 相同的 `pg_get_functiondef` +
   `replace` 手法换成 `lock_company_write(...)`（未声明企业范围时会自动回退为旧锁）。
4. 补一条范围回归用例（模板见 `tests/test_company_write_scope_wave1/2.py`：
   `tap(db)` 记录事务参数，断言写入带 `write_scope="company"` 且公司正确）。
5. 跑 PG 集成套件（CI）确认写门逐行核对与越界报错符合预期。

## 2. 迁移向前兼容（Expand and Contract）

`accounting.schema_migrations` 增加 `compatibility` 分类（sqlite 070 / pg 088）：

- **默认 breaking**：未标记的迁移文件一律按破坏性处理。
- **incremental**：迁移文件头部声明 `-- compatibility: incremental`，表示“只新增表、列、
  索引或函数”，旧镜像可以在数据库领先时继续启动。

启动检查（`Database._validate_postgres` / `_migrate_sqlite`）遇到比代码更新的迁移版本时：
全部为 incremental → 打印告警放行（可秒级回滚旧镜像）；任一 breaking 或缺少分类列 → 拒绝启动。

### 必须标 breaking 的情形（示例）

- 修改/删除已有列、表、RLS 策略或权限函数；
- 修改触发器语义、改变写门行为（除非旧代码永不触发该路径）；
- 任何“旧代码写入会被新约束拒绝”的变更。

### 发布顺序

1. 维护账号执行 `python scripts/migrate_accounting.py migrate`（会记录每条的 compatibility）。
2. 再滚动更新 API / Worker 镜像。
3. 回滚演练：把镜像退到上一版（数据库保持领先），确认可启动且告警可见。

## 3. 新增数据库对象的检查清单（CI 会逐步收紧）

- [ ] 迁移文件头声明 compatibility；
- [ ] 新表：`ENABLE ROW LEVEL SECURITY` + `FORCE ROW LEVEL SECURITY`；
- [ ] 新表：`batch2_company_write_gate` 与 `batch2_statement_write_gate` 两个触发器；
- [ ] 最小授权：只给 `accounting_app` 需要的 DML，`REVOKE ... FROM PUBLIC`；
- [ ] 涉及企业写事务的新表：同时更新 `company_write_table_guard` 映射。
