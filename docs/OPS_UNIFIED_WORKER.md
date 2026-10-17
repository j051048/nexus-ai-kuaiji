# 统一后台 Worker 运维说明

## 1. 它解决什么问题

历史部署需要 4 个常驻进程（API + 票据/分录队列 Worker + 会计助手 Worker + 旧账迁移 Worker）。
`app/unified_worker.py` 把这三种后台任务合并进 **一个进程**，按加权优先级轮询：

1. 实时会计助手（高优）
2. 单张票据识别与分录生成队列（中优）
3. 批量旧账迁移（低优）

领取与租约仍由数据库逐条判定，因此统一进程与独立 Worker 可以任意混跑，
不会重复领取任务；大规模时再按队列横向拆分即可。

## 2. 启动

```bash
# 生产（与独立 worker 使用同一个受限数据库账号）
python scripts/run_accounting_unified.py --env-file /path/to/worker.env

# 只处理一轮（巡检/演练）
python scripts/run_accounting_unified.py --once
```

`scripts/run_accounting_worker.py`、`scripts/run_accounting_assistant.py`、
`legacy_migration/worker.py` 仍然可用；同一套环境变量继续生效。

## 3. 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ACCOUNTING_WORKER_DATABASE_URL` | 必填 | 受限 worker 登录（仅 accounting_app + accounting_worker 角色） |
| `ACCOUNTING_ATTACHMENT_ROOT` | 空 | 共享 Supabase 或本地持久目录 |
| `ACCOUNTING_UNIFIED_ROLES` | `assistant,queue,migration` | 只跑子集：如 `queue,migration` 或 `assistant` |
| `ACCOUNTING_UNIFIED_ASSISTANT_BUDGET` | `1` | 每轮最多处理的助手会话数（1–10） |
| `ACCOUNTING_UNIFIED_QUEUE_BUDGET` | `2` | 每轮最多领取的票据/分录任务数（1–10） |
| `ACCOUNTING_UNIFIED_MIGRATION_BUDGET` | `1` | 每轮最多领取的迁移任务数（1–10） |
| `ACCOUNTING_UNIFIED_IDLE_SECONDS` | `1` | 全部空闲时的轮询间隔（0.05–30 秒） |
| `ACCOUNTING_QUEUE_LEASE_SECONDS` | `180` | 队列租约（≥150，覆盖 PDF 准备与模型调用） |
| `ACCOUNTING_QUEUE_CONCURRENCY` | `2` | 队列并发（1–2） |
| `ACCOUNTING_MIGRATION_CONCURRENCY` | `1` | 迁移并发（1–2） |

生产环境仍要求独立 PostgreSQL 账号、`sslmode=require` 或证书校验；
统一进程启动时与旧 Worker 一样执行 `verify_worker_login`（拒绝超级用户/继承角色）。

## 4. 部署建议

- **中小事务所**：1 个 API + 1 个统一 Worker（`ACCOUNTING_UNIFIED_ROLES` 默认全开）。
- **规模上量后**：把 `assistant` 拆成独立进程并保持低延迟；`queue` 与 `migration` 可继续合并，
  或按 `ACCOUNTING_UNIFIED_ROLES=migration` 把迁移单独隔离到低峰时段。
- 灰度替换顺序：先启动统一 Worker → 观察 10 分钟心跳与队列进度 →
  再停掉旧的两个/三个 Worker 容器。期间任务不会重复处理（数据库租约保证）。

## 5. 心跳与停止

- 队列心跳（`worker_heartbeats`）由队列侧 maintenance 维护，节奏与独立 Worker 相同（≥10 秒一次）。
- 会计助手 monitor 在空闲时每 15 秒写一次存活心跳；`SIGINT/SIGTERM` 停止时补写
  `stopped` 状态，队列侧同时补一次 `stopped` 维护调用。
- 进程被强杀不丢任务：所有在途任务按持久租约恢复（`--once` 巡检可观察恢复行为）。

## 6. 常见问题

- **启动报“worker登录必须为独立受限账号”**：数据库连接用的不是
  `migrate_accounting.py provision-worker-login` 创建的那个登录。
- **助手任务长时间排队**：检查 `ACCOUNTING_UNIFIED_ASSISTANT_BUDGET` 是否被调成 0
  （不允许）以及模型供应商可用性；统一进程顺序保证助手优先，不会被迁移任务饿死。
- **迁移任务长时间不动**：确认 `ACCOUNTING_UNIFIED_ROLES` 包含 `migration`，
  或迁移项目仍处于需要人工确认映射/验收的状态（产品行为，不是队列问题）。
