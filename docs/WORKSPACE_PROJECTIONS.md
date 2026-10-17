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

1. **独立投影维护进程**（`app.maintenance_worker`）：API 进程不再启动长期后台线程。
   维护进程扫描脏、失败和从未构建的活跃企业，每家企业一个独立事务，走**系统身份 + 企业写锁**
   （`begin_system_company_write`，见迁移 094）：共享全局门 + 企业锁，
   不再抢占全库独占锁——重建期间其他企业的写事务不受影响，只有同企业写入会被短暂串行。
   单企业失败只记账（`failures+1`，同样走企业锁）并继续下一家。
2. **管理端批量重建**：`POST /api/workspace/projections/rebuild?limit=N`（1–50，仅事务所管理层）。
   用于首次部署预热或长时间失败后的修复，返回 `{rebuilt, failed, remaining}`。

失败不会阻塞读取：单企业重建失败会被记账（`failures+1`）并继续下一家；读取照常返回实时结果。

并发语义总结：**`GET /api/workspace/tasks` 永远是纯只读事务**（只做新鲜度检查与实时回退，
并在内存中唤醒后台）；后台刷新与本所其他企业的写入完全并行，与本企业的写入按企业锁
短暂串行（保证投影与已提交状态一致的最小代价）；与仍在使用全局锁的旧写域
（事务所配置、跨企业批量等）共享全局门，互斥但不长期占用。多实例部署时，各实例的
后台刷新会被企业锁串行化，结果幂等。

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

## 6. 为什么普通 Worker 不做预热

Worker 登录按安全设计只持有 `accounting_app` + `accounting_worker` 两个角色，
没有 `accounting_system`，无法在系统身份下读全量源表重建投影。
因此投影维护使用单独进程与单独数据库账号：

```bash
ACCOUNTING_MAINTENANCE_DATABASE_URL=postgresql://accounting_maintenance... \
python scripts/run_accounting_maintenance.py
```

该账号必须可切换 `accounting_system`，但不需要 `accounting_worker`；普通票据/助手 Worker
继续只持有受限角色。API 多副本、统一 Worker 多副本都不再各自运行刷新线程。

## 7. 故障处理

- **读取突然变慢**：先看 `GET /api/workspace/projections`；`dirty` 很大说明写入流量大或
  重建失败堆积 → 执行一次 `rebuild`（可多轮直到 `remaining=0`）。
- **`failing>0`**：查 API 日志中的「统一待办投影重建失败」告警；常见原因是新迁移未同步
  更新刷新 SQL 或权限，按报错修复后重建即自愈。
- **回退期**：投影是纯加速层，可直接停用（删表或让 Worker 不跑都不会影响正确性）；
  恢复后按需重建。
