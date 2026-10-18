# B07 执行报告：部署、恢复隔离、上传事务与性能基线

日期：2026-10-18
基线：`a9daee5b` + B03–B06 未提交改动
性质：本机 SQLite/脚本验证；**未提交、未推送、未部署、未操作线上**

---

## 0. 状态

**B07 部分完成。** 后续追加：WSL Ubuntu 内 PostgreSQL 14 已用于真实受限角色测试，20 个 PG 适配/集成测试通过；因此“真实 PostgreSQL 完全未验证”不再成立，但容器/PG16/完整恢复/私有身份/离线仍未验收。

已完成：

- 恢复隔离从“可选环境变量”加固为“恢复目录自动检测 + 显式配置 fail-closed”，并接入 API、worker、助手、维护。
- 上传 `store_content/complete` 的对象 IO/哈希/校验移出数据库写锁，短事务重鉴权提交；并发慢对象测试通过。
- 私有部署补 `maintenance` 消费者角色与 compose 服务。
- 生产镜像改用 runtime-only 锁，排除 pytest/ruff。
- 本地性能基线脚本跑出合成数据。

**未通过门槛：**

- 已在 WSL PostgreSQL 14 做真实受限角色测试；仍未做 Docker 容器、PG16、私有身份、离线验证。
- 未完成完整系统恢复（身份/角色/RLS/密钥/报表文件/队列费用）。
- 未做首页/助手端到端性能对比，未做升级/回退、故障注入、真实大附件下载。
- B01–B06 未全部验收；B07 不能把它们变成生产可用。

---

## 1. 基线

| 项 | 实际 |
|---|---|
| HEAD | `a9daee5b` |
| 最新 PostgreSQL 迁移 | `114_delivery_manifest_company_lock.sql` |
| 最新 SQLite 迁移 | `082_b05_ai_endpoint_policy.sql` |
| 工作区 | B03–B06 未提交；本批继续未提交 |
| 本机可用 | Python/SQLite、Vitest/Playwright、本地 HTTP 替身 |
| 本机缺失 | Docker、PostgreSQL、psql、私有身份/对象存储、真实模型 |
| 当前规模 | 2–3 家试点；不宣称 200–500 家 |

---

## 2. 前置验收表

| 条件 | 状态 | 影响 |
|---|---|---|
| B01 原件登记/审核/期间边界 | 部分 | SQLite 验证；真实恢复/对象存储未验收 |
| B01 外部接入禁用 | 已隔离 | 不因 B07 启用 |
| B02 长期账套/期初 | 部分（SQLite） | 未进入生产 |
| B03 正式凭证/账簿 | 部分（SQLite） | 未进入生产 |
| B04 子账/报表/月结 | 部分（SQLite） | 未进入生产 |
| B05 端点/外发政策 | 部分（SQLite） | legacy 企业仍是缺口 |
| B06 角色首页/复核 | 部分 | B07 未改 UI |
| 真实 PostgreSQL 权限/并发 | **部分验证（PG14）** | 20 个 PG 测试通过；PG16/容器未验证 |
| 私有真实身份 | **未验证** | 不能称生产登录 |
| 完全离线 | **未验证** | 不能称离线部署 |

---

## 3. 部署清单

### 3.1 角色与消费者

| 角色 | 入口 | 职责 | 验证 |
|---|---|---|---|
| api | `run_accounting_service.py --role api` | HTTP API；无内嵌 worker | 配置测试 |
| worker | `app.jobs.worker` | OCR/建议/队列任务 | 现有测试，无容器 |
| assistant | `run_accounting_assistant` | 独立助手消费者 | 现有测试，无容器 |
| migration | `app.legacy_migration.worker` | 旧账业务迁移，不是 schema migration | 现有测试 |
| maintenance | `app.maintenance_worker` | 投影维护 | 本批新增；配置测试 |
| schema migration | `scripts/migrate_accounting.py` | 独立数据库迁移 | 现有脚本 |

`docker-compose.demo.yml` 已补 `maintenance` 服务；`ACCOUNTING_MAINTENANCE_DATABASE_URL` 必填。管理员 DSN 仍禁止进入运行容器。

**未验证**：所有队列消费者在真实容器同时启动；batch_ops/维护的端到端消费；真实私有身份、存储、OCR/模型。

### 3.2 配置与依赖

- `Dockerfile` 改用 `nexus_backend/requirements.runtime.lock`。
- 新 runtime lock 排除 pytest/ruff/Pygments 等开发工具；CI 仍用完整 `requirements.lock`。
- 生产仍要求受限 PostgreSQL + TLS；`ACCOUNTING_ENV=production`。
- 私有部署真实登录/OIDC 未实现；local/demo 禁止生产保护保留。
- 完全离线未验收：身份 JWKS、依赖、权重、字体、OCR 首次下载、遥测均未断网核对。

---

## 4. 恢复手册（本批实际覆盖）

### 4.1 备份边界

当前 `backup_accounting.py` 覆盖：

- accounting 数据库快照；
- 8 类登记原件；
- 迁移版本/校验和；
- 原件 storage_key、范围、大小、哈希；
- 对象版本能力缺失时显式 `object_version_supported=false`。

不是完整系统备份：不含身份主体/密码/JWKS、数据库角色/RLS 重建、密钥/证书、报表 PDF、队列/调用/费用全链路。

### 4.2 恢复隔离

恢复目标仍写入 `restore-verification.json`。

B07 加固 `enforce_restore_hold`：

- 显式 `ACCOUNTING_RESTORE_HOLD_FILE` 存在 → 阻断；
- 配置了该路径但文件不存在 → **fail closed**；
- 未配置时自动检查数据库路径父目录、附件/存储根父目录、cwd 下的 `restore-verification.json` / `restore-hold.json`；
- API 启动、独立/统一 worker、助手、维护均接入；
- 恢复副本即使忘记设置环境变量也不会静默启动。

测试：

- 标记存在 → worker 阻断；
- 恢复目录 + DB 路径、无环境变量 → API 启动阻断；
- 显式路径缺失 → 阻断；
- 正常 DB 目录 → 放行。

**仍然不是完整放行工具**：放行需人工移除标记并核对未知任务/费用/权限；旧 token/旧进程限制未做真实网络验证。

### 4.3 恢复演练

已执行：

- SQLite 全库 + 8 类原件备份 → 新目录恢复 → 完整性/迁移/哈希校验 → `restore-verification.json`；
- B03/B04 新表存在于恢复快照；
- 恢复 hold 自动阻断 API/worker。

**未执行**：

- 新空 PostgreSQL 恢复；
- 真实身份主体、角色、RLS/函数/列级授权重建；
- 私有对象存储恢复与授权下载；
- 在途/未知任务、费用、回执核对；
- 恢复后真实业务写入 → 复核 → 导出全链路；
- RPO/RTO 实测。

因此不能填“分钟级恢复/零丢失”。

---

## 5. 上传长事务优化

### 5.1 修改

`uploads.py` 的 `store_content/complete`：

- 短读事务鉴权/读取 intent；
- 事务外对象 `put/read`、哈希、格式校验；
- 短写事务重新鉴权/过期/状态校验，条件 `UPDATE pending→stored`；
- 失败清理先 claim `discarded`，不删除并发赢家对象；
- 同一上传并发只一次有效提交。

### 5.2 证据

新增测试 `test_slow_object_put_does_not_hold_database_write_lock`：

- 第一个上传的对象 `put` 被人为延迟 2 秒；
- 延迟期间第二个上传完成 `store_content`；
- 两个最终均为 stored；
- 证明对象 IO 不持有数据库写锁（SQLite 本机）。

**未验证**：PostgreSQL 并发、对象存储真实网络延迟、孤儿对象清理、服务端对象替换攻击。

---

## 6. 性能基线

### 6.1 方法

- `scripts/benchmark_workbench.py --books 2 --sources 10`
- `scripts/benchmark_processing.py --requests 2 --latency-ms 50`
- 本机 SQLite/合成 HTTP；无真实模型/网络/浏览器。

### 6.2 结果

Workbench summary：

- 2 books / 20 sources / 40 draft lines / 4 SQL；
- **25.54 ms** summary（单进程 SQLite，不是首页端到端）。

Synthetic processing（2 requests，50ms 本地延迟）：

| concurrency | elapsed | throughput | completion p95 |
|---|---|---|---|
| 1 | 0.648s | 3.09 req/s | 344.4ms |
| 2 | 0.334s | 5.99 req/s | 327.8ms |
| 4 | 0.351s | 5.70 req/s | 349.8ms |

**不能**把这些当作首页可操作时间、助手首字/完整回答 p95、真实模型性能或 200–500 家容量。

### 6.3 未完成

- 优化前后首页/助手/上传端到端对比；
- 冷/热缓存、冷启动、混合 OCR+批量；
- PostgreSQL 连接/锁等待；
- 前端渲染、浏览器轮询延迟；
- p50/p95 样本量与错误率。

---

## 7. 可观测性与升级

- 保留现有 telemetry/observability；未新建平行监控。
- 健康探测仍区分进程与业务；未新增完整恢复点/备份失败告警。
- 迁移由独立脚本执行；API/worker 不自动迁移。
- 应用/worker/身份/存储版本矩阵、扩展-迁移-收缩、混合版本回退未验证。
- 有 WSL PostgreSQL 14，但无 Docker/PG16，仍无法做容器组合启动/退出/升级演练。

---

## 8. 修改清单

**修改**

- `nexus_backend/app/jobs/restore_hold.py`：自动检测恢复目录、显式路径 fail-closed。
- `nexus_backend/app/main.py`：API 启动接入恢复门禁。
- `nexus_backend/app/maintenance_worker.py`：维护进程接入恢复门禁。
- `nexus_backend/app/uploads.py`：对象 IO/校验移出写事务，条件提交与安全清理。
- `scripts/run_accounting_service.py`：新增 `maintenance` 角色。
- `scripts/check_demo_deployment.py`：maintenance 角色/DB 校验。
- `docker-compose.demo.yml`：maintenance 消费者。
- `Dockerfile`：改用 runtime-only lock。
- `nexus_backend/requirements.runtime.lock`：新增运行时锁定。
- `nexus_backend/tests/test_restore_hold.py`：自动检测/fail-closed 测试。
- `nexus_backend/tests/test_uploads.py`：慢对象并发测试。
- `nexus_backend/tests/test_demo_deployment.py`：maintenance/runtime lock 测试。

**新增文档**

- `docs/B07_DEPLOYMENT_RECOVERY_CONTRACT.md`
- `docs/B07_EXECUTION_REPORT_20261018.md`

**迁移**

- 新增 PG 108–114；SQLite 无变化（仍 082）。
- 108：投影 bump 改 SECURITY DEFINER，修复企业创建时 `workspace_projection_state` 权限不足。
- 109：企业写锁对带非空 `company_id` 的表统一进入企业域，覆盖 B02–B04 新表。
- 110：审计事件落企业锁；关期 snapshot 函数改企业锁。
- 111：嵌套安全定义函数使用显式 advisory 企业锁，避免错误回退 legacy 全库锁。
- 112：关期 review/close 与 ops 脏标记改用企业锁/显式 scope。
- 113：`ops_company_dirty/refresh` 作为非财务投影状态移除通用企业写门（运行账号仍只有 SELECT）。
- 114：交付清单函数从 legacy 锁改为企业锁。
- 已应用历史迁移未修改。

**兼容/回退**

- 恢复门禁对“显式路径缺失”从放行改为 fail-closed，属安全行为变更；正常未恢复实例不受影响。
- 上传接口路径/响应不变；内部事务边界改变。
- maintenance 角色为新增；旧角色不变。
- runtime lock 可能改变镜像依赖集合；未做容器验证。
- 回退镜像不等于回退财务事实。

---

## 9. 测试证据

```powershell
$env:PYTEST_PLUGINS='tmp_test.pytest_plugin'
pytest `
 test_monthly_briefing.py test_ingestion_service.py `
 test_stage10_backup_recovery.py test_restore_hold.py `
 test_accounting.py test_review_boundary_postgres.py `
 test_posting_service.py test_posting_api.py `
 test_opening_service.py test_opening_api.py `
 test_stage10_ledgers.py test_b04_pilot.py test_b04_api.py `
 test_uploads.py test_demo_deployment.py -q
```

结果：**145 passed, 4 skipped**。

追加真实 PostgreSQL（WSL Ubuntu 22.04 / PostgreSQL 14.24）：

```powershell
$env:ACCOUNTING_TEST_POSTGRES_URL="postgresql://.../accounting_b08_local"
$env:ACCOUNTING_TEST_DATABASE_URL="postgresql://.../accounting_b08_local"
$env:ACCOUNTING_TEST_WORKER_DATABASE_URL="postgresql://.../accounting_b08_local"
pytest nexus_backend/tests/test_postgres_adapter.py `
  nexus_backend/tests/test_postgres_integration.py -q
# 20 passed
```

已用独立 `b08_admin`、`b08_runtime`、`b08_worker` 角色，从空库迁移 1–114 后执行；不是 SQLite 替代。PG 14 与 CI 的 PG16 仍有版本差异，容器未验证。

- `test_uploads.py`：**8 passed**（含慢对象锁测试）。
- `test_stage10_backup_recovery.py` + `test_restore_hold.py`：**14 passed**。
- `test_demo_deployment.py`：**37 passed**（含 maintenance/runtime lock）。
- 4 skipped 为 PostgreSQL 边界，不计通过。

前端 B06 回归（本批未改前端）：

- `npm test`：**320 passed**。
- `npm run typecheck` / `npm run lint` / `npm run build`：通过。
- B06 E2E：`b06-role-home` + `stage20-home-loading` **10 passed**；`b06-role-home` + `stage12-inline-review` **2 passed**。

未运行：Docker、真实 PostgreSQL、真实身份、对象存储、真实模型、离线、完整恢复、升级/回退、首页/助手端到端性能。

---

## 10. 总结状态

| 项目 | 状态 |
|---|---|
| 恢复隔离自动门禁 | **已实现，SQLite 验证** |
| 上传对象 IO 移出写锁 | **已实现，SQLite 并发测试** |
| maintenance 消费者/角色 | **已实现，配置测试** |
| runtime-only 依赖锁 | **已实现，静态测试** |
| 备份/原件完整性 | **已有测试，部分边界** |
| 真实 PostgreSQL | **部分验证（PG14，20 passed）** |
| 容器/PG16/私有身份/离线 | **未验证** |
| 完整系统恢复/RPO/RTO | **未完成** |
| 首页/助手性能对比 | **未完成** |
| B01–B06 生产可用 | **未验收** |

**B07 部分完成，未达到“B07 已完成本地验收”。**

尚未执行线上操作；生产部署、私有身份/存储、完整恢复和 B08 真实账期切换需要另行确认。
