# B01–B08 总控验收台账（本机候选版本）

日期：2026-10-18
性质：本地/隔离验证；**未提交、未部署、未操作线上、未启动真实试点、未切换账本**

---

## 1. 版本与环境快照

| 项 | 实际 |
|---|---|
| HEAD | `0c906f83`（并行执行者提交了 B03–B08 基线；本总控未提交） |
| 工作区 | B03–B08 未提交；B01/B02 部分修改 |
| 最新 PostgreSQL 迁移 | `114_delivery_manifest_company_lock.sql` |
| 最新 SQLite 迁移 | `082_b05_ai_endpoint_policy.sql` |
| 本机 PostgreSQL | WSL Ubuntu 22.04 / PostgreSQL **14.24**，隔离库 `accounting_b08_local` |
| 数据库角色 | `b08_admin`（仅迁移/测试管理）、`b08_runtime`（API）、`b08_worker`（worker） |
| 容器 | **无 Docker** |
| PG16 | **未验证**；CI 使用 PG16 |
| 前端 | React/Vite；Vitest 320 passed；Playwright 已跑角色/复核/交接 |
| 真实资料/人员 | **未提供** |

> PG 14 的真实测试能证明受限角色/RLS/触发器/并发路径可运行，但不能替代 CI 的 PG16 和容器验收。

---

## 2. B01–B08 验收矩阵

| 批次 | 验收项 | 现状/证据 | 结论 | 阻断/下一步 |
|---|---|---|---|---|
| B01 | 简报金额/有效来源 | 8 个简报测试 | 已验证（合成） | 无 |
| B01 | 原件登记/备份 | 8 类原件、SQLite 恢复 | 部分验证 | 真实对象存储/完整恢复 |
| B01 | 审核/期间/权限 | HTTP + 真实 PG RLS 测试 | 部分验证 | 更多负向/并发场景 |
| B01 | 外部接入禁用 | 代码级关闭/旧 URL | 已隔离 | 网关实测 |
| B02 | 长期账套/期初/科目 | SQLite 测试 | 部分验证 | PG 期初/控制/辅助 |
| B03 | 正式凭证/原子记账 | SQLite/HTTP + PG 全流程（drafts/export） | 部分验证 | PG 正式记账/冲销/并发 |
| B04 | 子账/月结/报表 | SQLite + PG 关期 snapshot/review/close | 部分验证 | PG 报表发布/跨年/银行 |
| B05 | 端点/外发政策 | 7 个 B05 测试；生产无 managed policy 阻断 | 部分验证 | 批量迁移/真实模型 |
| B06 | 角色首页/复核/交接 | 320 Vitest；10 + 2 + 1 Playwright | 部分验证 | 真机/大附件/生产 MFA |
| B07 | 部署/恢复/长事务 | PG 20 passed；上传慢对象测试 | 部分验证 | 完整恢复/容器/PG16/离线 |
| B08 | 比较器/切换预检/模板 | 19 个 B08 测试；合成包 blocked | 部分完成 | 真实资料/人员/两期 |

**正式内部总账切换仍不满足**：B01–B04 没有全部通过，B07 完整恢复未完成，B08 真实双轨未开展。

---

## 3. 本次实际修复的 PG 缺陷

从空库迁移 1–114 后，`test_postgres_adapter.py + test_postgres_integration.py` 由 **16 errors/4 passed** 修到 **20 passed**。

| 迁移 | 问题 | 修复 |
|---|---|---|
| 108 | 企业创建时投影 bump 触发器写入 `workspace_projection_state` 被拒 | bump 函数改 SECURITY DEFINER，固定 search_path，撤销直接执行 |
| 109 | B02–B04 新增 `company_id` 表未进企业写锁白名单 | 带非空 `company_id` 的行统一进入企业锁 |
| 110 | `audit_events` 未落到企业锁；closing snapshot 仍用 legacy 锁 | 审计事件显式企业锁；snapshot 按书企业锁 |
| 111 | 嵌套 SECURITY DEFINER 触发器误回退 legacy 全库锁 | 显式共享全局门 + 企业 key |
| 112 | `review_closing_snapshot`/`close_with_snapshot` 仍 legacy；ops 标记 scope 丢失 | 关期改企业锁；ops 标记设置/使用企业 scope |
| 113 | `ops_company_dirty/refresh` 非财务投影状态被通用写门误判 | 移除这两张非财务表的通用写门；运行账号仍只有 SELECT |
| 114 | `capture_delivery_manifest` 内部调用 legacy 锁 | 按 export 所属书设置企业锁 |

约束：未修改 1–107 已应用迁移；108–114 为新增 PG 迁移，SQLite 无对应变更。

---

## 4. 测试证据

### 4.1 真实 PostgreSQL

```powershell
$env:ACCOUNTING_TEST_POSTGRES_URL="postgresql://b08_admin:...@127.0.0.1:5432/accounting_b08_local"
$env:ACCOUNTING_TEST_DATABASE_URL="postgresql://b08_runtime:...@127.0.0.1:5432/accounting_b08_local"
$env:ACCOUNTING_TEST_WORKER_DATABASE_URL="postgresql://b08_worker:...@127.0.0.1:5432/accounting_b08_local"
pytest nexus_backend/tests/test_postgres_adapter.py `
  nexus_backend/tests/test_postgres_integration.py -q
```

结果：**20 passed**。

覆盖：受限角色、RLS、跨企业/账套、系统角色边界、审核/导出回滚、并发导入/审核/导出、关期竞争、快照/回执、目录授权、撤权、系统角色不能读正式凭证/不能写业务表。

### 4.2 其他定向回归

| 命令 | 结果 |
|---|---|
| `pytest test_b05_policy.py` | 7 passed |
| `pytest test_b08_acceptance.py` | 19 passed |
| B07 选择回归（B01/B02–B04/B07） | 145 passed, 4 skipped |
| `npm test` | 320 passed |
| B06 角色首页+首页加载 | 10 passed |
| B06 角色+同屏复核 | 2 passed |
| 工作交接 E2E | 1 passed |
| `npm run typecheck/lint/build` | 通过 |

### 4.3 未完成/失败

- 全量 `pytest nexus_backend/tests` 尝试运行超过 20 分钟未结束，已终止；不把未完成当通过。
- 未运行 Docker、PG16、真实身份/对象存储、完整恢复、真实模型、离线、真实资料、真机。
- 4 skipped 是 PostgreSQL 边界测试，不计通过。

---

## 5. 关键剩余阻断

1. **完整恢复**：数据库 + 原件 + 身份 + 角色/RLS + 配置/秘密渠道 + 队列/费用；当前仅 SQLite 备份恢复和 PG 普通数据测试。
2. **PG16/容器**：本机 PG14 通过，CI PG16 未跑，Docker 入口未启动。
3. **B03/B04 正式账本 PG 深水区**：正式凭证、冲销、子账、报表发布、跨年尚未在 PG 全量验收。
4. **B06 大附件/生产 MFA**：交接 E2E 已过；大附件上传→授权下载、真实 MFA 未过。
5. **B05 批量迁移**：生产无政策会阻断，但企业政策批量迁移工具未做。
6. **B08 真实双轨/人员/授权**：全部未开始。
7. **性能/人工效率**：无真实数据，不报告提升。
8. **测试工具自身**：已加强为本地证据文件存在、内容哈希、企业/账套、版本和核心门槛不可 N/A；外部证据仍只标人工待核验，不能替代责任人签收。

---

## 6. 四项独立结论

| 结论 | 判定 |
|---|---|
| 本轮本地技术交付完成 | **否，部分完成** |
| 真实企业试点验收 | **未开始** |
| 具备申请正式切换证据 | **否** |
| 正式切换获批/执行 | **未授权、未执行** |

---

## 7. 下一步最小顺序

1. 用 **PG16 + Docker** 重跑 1–114 迁移和 20 个 PG 测试；确认 PG14 特有行为不存在。
2. 做一次完整恢复：新空 PG + 私有对象存储 + 测试身份 + 重新登录 + 权限/原件/业务/复核/导出。
3. 在 PG 上补 B03 正式凭证/冲销、B04 报表发布/跨年测试。
4. 修 B08 切换工具证据真实性检查，再做真实资料双轨。
5. 大附件上传→授权下载、生产 MFA、真机回归。
6. 最后才生成逐企业/账套切换包；本轮不执行切换。

**当前只能称：本地技术部分完成，PG 真实测试显著推进；真实试点、完整恢复、切换均未完成。**
