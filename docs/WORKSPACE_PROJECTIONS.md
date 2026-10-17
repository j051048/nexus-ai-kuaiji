# 统一待办投影（Workspace Task Projections）

## 1. 机制

首页「我的待办」原本每次请求都要跑 14 路 UNION（业务事项、补件、复核、导出、交付、
OCR、分录、告警、资料、迁移……）。第三批起引入物化投影：

- `workspace_task_projections`：只保存**与操作者无关**的任务行与可见性键
  （申请/指派/收件/部门/账套/企业/金额/状态/标题等）；
- `workspace_projection_state`：每企业一条，记录 `revision`、`built_revision`、
  `dirty_at`、`built_at`、`failures`。

**授权永远由读取时的实时谓词决定**（成员、部门审批、账套授权、复核范围、案件可见性），
PostgreSQL 侧还有逐行 RLS：投影策略逐行调用与源表完全相同的可见性函数
（`business_event_visible` / `intake_allowed` / `case_manage` / `book_allowed` /
`proposal_scope` / `ops_company_allowed` / `service_material_visible` / `migration_allowed`），
不会放宽任何一行的暴露面。legacy_migration 支线暂不进投影，继续走实时分支。

## 2. 脏标记

20 张源表（业务事项、草稿、来源、期间、案件、材料、OCR、分录、交付、告警、企业状态等）
的写入触发器只做一件事：把该企业 `revision+1`。成本是每条写入一次同值 upsert；
读取路径据此判断投影是否可用。

## 3. 读取语义（重要）

- 投影**干净**（`built_revision >= revision` 且所有可达企业均已构建）→ 单表索引查询；
- 投影**脏或从未构建** → 完整回退实时 14 路查询。

**正确性从不依赖投影的新鲜度**：这是刻意设计。任何写入在提交前都不会改变读取结果；
投影只是把"干净时刻"的读取成本从十几张表的 UNION 降为一张小表的索引扫描。

## 4. 刷新路径

1. **交互请求**（`WorkspaceService._prepare_projection`）：每次最多重建 5 家脏企业，
   其余由后续请求继续收敛；重建在 API 进程的系统身份事务内执行。
2. **管理端批量重建**：`POST /api/workspace/projections/rebuild?limit=N`（1–50，仅事务所管理层）。
   用于首次部署预热或长时间失败后的修复，返回 `{rebuilt, failed, remaining}`。

失败不会阻塞读取：单企业重建失败会被记账（`failures+1`）并继续下一家；读取照常返回实时结果。

## 5. 运维诊断

`GET /api/workspace/projections`（仅事务所管理层）返回：

| 字段 | 含义 |
|------|------|
| `total` | 事务所活跃企业数 |
| `dirty` | 有未构建变更的企业数 |
| `never_built` | 从未构建过投影的企业数 |
| `failing` | `failures>0`（连续重建失败）的企业数 |
| `oldest_dirty_at` | 最久的未构建时间（用于判断积压） |
| `samples` | 失败/脏/未构建企业的明细样本（含 revision、built_at 等） |

建议的告警口径：`failing>0` 或 `never_built` 持续增长（>24h）→ 触发运维处理；
正常态应为 `dirty` 短暂出现、`failing=0`。

## 6. 为什么 Worker 不做预热

Worker 登录按安全设计只持有 `accounting_app` + `accounting_worker` 两个角色，
没有 `accounting_system`，无法在系统身份下读全量源表重建投影。
因此预热入口放在管理层（有人值守）与交互请求（自动收敛），而不是后台 Worker。
若未来希望在 Worker 侧预热，需要新增 SECURITY DEFINER 的刷新函数并逐表放行只读——属于
第三批写锁工作之后的独立议题。

## 7. 故障处理

- **读取突然变慢**：先看 `GET /api/workspace/projections`；`dirty` 很大说明写入流量大或
  重建失败堆积 → 执行一次 `rebuild`（可多轮直到 `remaining=0`）。
- **`failing>0`**：查 API 日志中的「统一待办投影重建失败」告警；常见原因是新迁移未同步
  更新刷新 SQL 或权限，按报错修复后重建即自愈。
- **回退期**：投影是纯加速层，可直接停用（删表或让 Worker 不跑都不会影响正确性）；
  恢复后按需重建。
