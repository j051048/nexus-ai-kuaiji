# 垂直会计记账领域 Agent —— 全栈审计报告

**审计对象**：`nexus-ai-accounting` v0.19.0（根目录 `C:\Users\Fei\Desktop\AI应用\nexus-ai-kuaiji`）
**审计日期**：2026-10-22
**审计角色视角**：AI Agent 架构 / 会计信息系统审计（CAS 语境）/ 代账中心业务 / 前端产品与交互
**审计方法**：全量源码静态审阅 + 依赖与门禁核验 + 关键路径逐行阅读 + 迁移与 RLS 策略抽样 + 测试套件实跑
**样本规模**：后端 `nexus_backend/app` 61,424 行 / 404 条路由 / 43 个领域模块 / 138 个 PostgreSQL 迁移；测试 55,972 行 292 个文件（含 90 个 PostgreSQL 专项）；前端 `src` 60,544 行 / 210 个文件；e2e 8,576 行 / 63 个文件。

---

## 一、总体结论

> **这是一套"把会计做成不可绕过的约束系统"的工程，而不是"让模型替会计记账"的演示。**
> 工程质量在同类产品中处于头部（8.0/10，接近可对外交付的企业级水准）；但**代账业务闭环只完成了一半——"账"这一半做得很硬，"税"这一半几乎空白**（5.5/10）。

三条最重要的事实：

1. **模型被严格关在"只读 + 有界工具"的笼子里**。凭证的生成、审核、过账、报表全部由确定性代码完成，模型只能输出"建议 + 可核验事实引用"，且输出在发布边界上被逐叶校验（`app/assistant/fact_validation.py`）。这在财务场景是**正确的架构选择**，也是本代码库最值得肯定的决策。
2. **数据层是真正的纵深防御**：138 个迁移、528 行 RLS、NOLOGIN 强约束角色、`audit_events` 仅授予 `SELECT,INSERT`（DB 级不可篡改）、正式凭证 append-only、三条状态轴（复核 / 过账 / 交付）互不覆盖。这一层的严谨度**超过多数商用代账 SaaS**。
3. **但"代理记账"的法定与商业闭环缺了两块硬骨头**：**纳税申报**（增值税/附加税/企业所得税/个税/印花税全无，`app/tax/` 合计 79 行且 `filing: "not_filed"`）与**客户资料交接包**（`docs/BACKUP_RECOVERY.md` 明确写"尚未实现"）。这两块恰是代账中心收费项与合规义务的主体。

| 维度 | 评分 | 一句话判词 |
|---|---|---|
| 1. 代码架构 | **7.5** | 领域切分真实、内核确定性极强；但 43 个模块中藏着 1823 行巨型服务，双数据库方言靠 185 处分支硬撑 |
| 2. 代码质量 | **7.5** | 零 TODO、零 `any`、文档串即契约；但 `_audit` 复制 21 份、`encode` 复制 14 份，无类型检查、无覆盖率门禁 |
| 3. 调度与管线控制 | **7.0** | 持久队列 + 租约围栏 + 抢占式恢复，设计成熟；但无死信、无退避、无内置调度器，56% 写路径抢同一把全局锁，且有已复现的停机崩溃 |
| 4. 风险控制 | **9.0** | 本库最强项。整数分、版本并发、理由必填、幂等键、指纹绑定、AI 不可过账、审计日志 DB 级只增 |
| 5. 权限体系与数据隔离 | **8.5** | 授权从 DB 成员关系实时推导、RLS 全表覆盖、404/403 语义区分防探测；差额在 MFA/密钥与越权测试面 |
| 6. 前端产品设计与交互 | **7.5** | 角色化导航、能力过滤、零内联样式、并发冲突可视；但 `App.tsx` 1,784 行 34 个 `useState`，路由靠手写解析 |
| 7. 领域功能覆盖（补充） | **5.5** | 账务链路完整；税务、薪酬、发票开具、账簿输出、交接导出缺失 |
| **工程加权综合** | **8.0** | 架构 20% / 质量 15% / 调度 15% / 风控 25% / 权限 15% / 前端 10% |
| **代账业务闭环** | **5.5** | 只能覆盖"记账户"角色，覆盖不了"报税 + 交接 + 薪酬"三块日常 |

**给决策者的一句话**：可以用它替换现有人工做账与复核流程（风险可控、审计可查），但**不能**用它替换报税流程；若目标是"一家事务所的全业务系统"，需再投入约 30%–40% 的增量开发补齐税务与交接两块。

---

## 二、维度一：代码架构（7.5 / 10）

### 2.1 关键优点（有证据）

| 优点 | 证据 |
|---|---|
| **确定性内核 / 概率性外挂**分层清楚，且写在文档串里当契约 | `app/accounting/service.py:1` 「金额处理和审核规则均为确定性代码。此模块不创建正式总账分录，也不调用模型。」；`app/assistant/provider.py:1` 「Accounting dialogue through the same bounded, tool-free model transport.」；`app/agent_workspace/service.py:1` 「No model invocation, write, approval or export.」 |
| **Agent 能力极窄且显式枚举**，模型无法自选对象 | `app/assistant/actions.py` 文档串：「It never selects identifiers, approves accounting work, edits business facts or invokes arbitrary endpoints.」；`ActionKind = Literal["ocr_batch","proposal_batch","bank_drafts"]` |
| **金额用整数分 + 正则白名单**，从入口拒绝浮点与指数 | `app/accounting/invariants.py`：`MONEY_PATTERN = r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?\Z"`、`money_to_cents` 明确拒绝 float/指数 |
| **多租户隔离三层叠加**：schema 私有 + NOLOGIN 角色 + RLS | `migrations/postgres/001_accounting.sql`：`CREATE SCHEMA accounting`、`revoke public`、`accounting_app`/`accounting_system` 均 `NOLOGIN NOSUPERUSER NOBYPASSRLS`；全库 528 行 RLS 策略 |
| **依赖面极窄**：36 个锁定依赖，无 ORM、无 Redis、无 Celery | `nexus_backend/requirements.lock`（36 行）：fastapi / psycopg / pydantic / httpx / PyJWT / uvicorn，无 SQLAlchemy、无队列中间件 |
| **无隐藏技术债标记** | `nexus_backend/app` 下 `TODO/FIXME/XXX/HACK` 计数为 **0** |

### 2.2 关键问题（有证据）

1. **领域模块按"交付批次"命名，而非按业务能力命名**：`b04_close`、`monthly`、`periodic`、`closing` 四个模块语义重叠（期末、月结、循环草稿、关期各占一块），新人无法从目录名判断"月末结转该去哪个模块"。`app/periodic/__init__.py:1` 甚至直接写「B04 deterministic periodic drafts」——把内部编号写进了产品代码。
2. **巨型服务突破单一职责**：`app/legacy_migration/service.py` 1,823 行、`app/intelligence/service.py` 1,821 行、`app/accounting/service.py` 1,375 行（单个类 47 个方法）、`app/workspace/service.py` 1,297 行；全库 **33 个文件 >600 行**、5 个 >1,200 行。
3. **双数据库方言靠 185 处 `is_postgres` 分支硬撑**：集中在 `app/jobs/worker.py`(14)、`app/cases/service.py`(8)、`app/accounting/database.py`(8)、`app/settlements/service.py`(7)。这不是适配层，是**在每个业务函数里写两遍 SQL**——新增一个查询要写两次、测两次。
4. **服务定位器代替依赖注入**：`AccountingService.__init__` 接收 9 个可选服务引用（intelligence / settlements / cases / learning / bank_import / delivery / ledgers / revisions / dispositions），构造顺序与 None 语义成为隐形契约，单元测试必须手工拼装。
5. **同一领域两套实现**：`app/legacy_migration/learning.py`(1,022) / `settlements.py`(964) 与 `app/learning/service.py`(1,303) / `app/settlements/service.py`(808) 并行实现同一语义（迁移上下文专用，但规则会漂移）。
6. **404 条路由平铺，无版本前缀、无聚合 API**：`app/main.py` 1,078 行做装配；前端一个工作台页面往往要打 4–7 个接口，无 BFF 层。

### 2.3 改良建议

**高**：把 `b04_close / monthly / periodic / closing` 合并为 `period_end/`，内部按 `accrual | depreciation | closing | reporting` 分包；`legacy_migration` 拆为 `ingest / normalize / map / export` 四段。
**高**：抽出 `Dialect` 适配层（把 `?`↔`%s`、`json_extract`↔`jsonb->>`、`INSERT OR IGNORE` 收敛到一处 SQL 构造器），把 185 处分支压到 20 处以内。
**中**：`AccountingService` 改为显式依赖注入（构造参数全部必填或提供空实现），消除 9 个 `Optional`。
**中**：`legacy_migration` 的 learning/settlements 改为复用核心服务 + 上下文参数，而非复制。
**低**：路由加 `/api/v1/`；为高频页面提供读模型聚合端点。

### 2.4 对代账业务的实际影响

架构决定了**交付节奏**：新增一种税种或一张报表模板，当前需要在 `reports/service.py` 的模板枚举 + 映射函数 + 前端类型 + e2e 四处改，估算 3–5 人日；若模板化改造到位可降到 0.5 人日。代账中心最痛的是"政策一变就要改"（税率、报表格式、附注要求），**这个可扩展性差距会直接转化为合规滞后风险**。

---

## 三、维度二：代码质量（7.5 / 10）

### 3.1 关键优点（有证据）

- **文档串就是业务契约**，且写得比注释有用：`app/posting/service.py:1` 「Formal vouchers are append-only. Draft review, internal posting and external delivery remain three separate state axes」；`app/reports/service.py:1` 「Versioned financial report snapshots over formal posted vouchers only.」——读文档串即可理解不变量。
- **门禁真实且严格**：`.github/workflows/accounting.yml` 串起 `prettier --check` → `eslint` → `ruff check` → `ruff format --check` → `scripts/check_migration_idempotency.py` → `migrate` → `status --strict` → 双角色登录供给 → `check_pg_test_target.py` → vitest → `tsc --noEmit && vite build` → pytest（全量）；e2e 另起 4 分片。这条流水线**不是装饰**（2026-10-23 已移除独立 PaddleOCR 服务与其 CI 步骤）。
- **迁移有幂等与兼容性断言**：`app/accounting/database.py` 对迁移做 sha256 摘要（`\r\n` 归一化）、`-- compatibility: incremental|breaking` 声明、`status --strict` 校验漂移。
- **手写方言翻译器有专项单测**：`tests/test_postgres_adapter.py:15` 断言 `postgres_sql` 跳过字符串字面量、注释、`$$`/`$body$` 美元引用，并把 `INSERT OR IGNORE` 改写为 `ON CONFLICT DO NOTHING`。
- **前端类型洁癖**：`src/` 下 `: any` / `as any` / `@ts-ignore` 计数 **0**，`as unknown as` 仅 6 处，`style={{` 仅 1 处（样式走 token/CSS）。
- **测试体量与业务对齐**：292 个后端测试文件对应 43 个模块；测试夹具统一登记并关闭连接池（`tests/conftest.py`），避免池泄漏污染后续用例。

### 3.2 关键问题（有证据）

1. **横切关注点没有抽象，靠复制**：`def _audit` 在 `app/` 下出现 **21 份**，裸 `INSERT INTO audit_events` **29 处**；`def encode`（JSON 规范化）**14 份**；`def _fingerprint` **7 份**。任何审计字段变更（如加 `request_id`、`client_ip`）需要改 50 处。
2. **无 Python 静态类型检查**：无 `mypy`/`pyright` 配置，全靠运行时 pydantic + 人工。对一个 6 万行、含 185 处方言分支的代码库，这是**最大的单点质量风险**。
3. **无覆盖率门禁**：CI 不计 `coverage`。292 个文件很多，但**财务计算分支**（报表符号、现金流量分类、期末结转、税额取整）没有覆盖率约束，重构时无护栏。
4. **90 个 PostgreSQL 测试靠环境变量跳过**：`pytestmark = skipif(not ADMIN_URL ...)`，本地/预发默认全部 skip，**RLS 与并发围栏的真实行为在开发者机器上从未被验证**，只有 CI 验证。
5. **命名与类型瑕疵**：`app/monthly/batch.py:29` 字段 `companies: list[UserId]`——语义是企业列表，类型别名却复用 `UserId`。
6. **测试套件慢且无超时护栏**：本机全量 pytest 运行 >15 分钟（Python 3.14 + SQLite 内存库为主），`pytest-timeout` 未安装，`--timeout` 直接报错退出。CI 中一个挂死用例会吃掉整个 job 的默认超时。

### 3.3 改良建议

**高**：抽 `app/accounting/audit.py`（单一 `record(conn, actor, action, object_id, **details)`）+ 批量替换 50 处；用 AST 脚本一次性迁移并在 CI 加"禁止裸 INSERT audit_events"的 lint。
**高**：接入 `mypy --strict` 的渐进模式（先对 `accounting/`、`posting/`、`reports/`、`tax/` 四个财务包开启），并装 `pytest-timeout`（`--timeout=120`）。
**中**：CI 对 `accounting/ invariants / posting / reports / tax` 设 85% 覆盖率门槛。
**中**：把 postgres 测试改为"本地一键 docker compose"，并在 PR 模板里要求附 postgres 通过截图。
**低**：`encode` / `_fingerprint` 收敛到 `app/accounting/serialization.py`；修 `companies: list[CompanyId]`。

### 3.4 对代账业务的实际影响

代账中心每年至少两次"政策/报表格式变更"。当前代码形态下，**变更成本 ≈ 修改点数 × 2（方言） × 2（前后端）**，且没有覆盖率护栏。这不是"代码丑"的问题，而是**合规响应速度**问题：一次税率或报表格式调整，若回归不充分，出错的是 200 家客户的一整月账。

---

## 四、维度三：调度与管线控制（7.0 / 10）

### 4.1 管线设计（可圈可点）

真实链路是 **证据 → 事项 → 草稿 → 复核 → 导出 → 过账 → 报表 → 关期/交付**，每一跳都有独立状态与审计：

| 阶段 | 实现 | 关键约束 |
|---|---|---|
| 证据（OCR/发票/银行流水） | `app/intelligence`、`app/invoices`、`app/import_wizard` | 只落证据，不产生会计事实（`app/invoices/service.py:1` 明文） |
| 业务事项 | `app/business` | 部门/财务/申请人三方校验，`app/business/service.py:741` 「申请人不可审批本人事项」 |
| 凭证草稿 | `app/accounting/service.py` | 关键词建议规则标注「仅用于验证流程…不构成税务或会计处理判定」 |
| 复核 | `app/accounting/review.py` + `service.py:1199` | 单条与批量共用同一守卫与事务 |
| 导出 | `app/delivery` | 与过账分离，导出不产生总账 |
| 过账 | `app/posting/service.py` | 期初绑定指纹 + 期间必须 open + 账簿链接 |
| 报表 | `app/reports/service.py` | 只读已过账凭证，模板带版本号 |
| 关期/批量开期 | `app/closing`、`app/monthly/batch.py` | 单企业失败不回滚其他企业 |

### 4.2 关键优点（有证据）

- **持久队列 + 租约围栏**：`app/jobs/worker.py` 用 `lease_token` 写入 `ocr_attempts`，完成时 `UPDATE ... WHERE job_id=? AND attempt_no=? AND status='running'`，**天然防"僵尸 worker 覆盖新结果"**；`claim(worker_id, lease_seconds=180)`（范围校验 30–600）。
- **任务失效显式标记**：`_invalidate_run` 把 `ai_runs` 置 `stale` 并写 `error_code='queue_interrupted'`，不静默吞掉。
- **单企业并发约束**：SQLite 路径用 `NOT EXISTS(... status='running')` 强制同企业同刻只有一个运行中任务，避免同一账套并发写。
- **公平调度**：`ocr_company_schedule.last_dispatch_seq` 做 round-robin，防止大客户饿死小客户。
- **单一进程多队列 + 预算**：`app/unified_worker.py` 一个进程按 `assistant 1 / queue 2 / batch 2 / migration 1` 权重分配线程，`MONITOR_INTERVAL 15.0`，每 tick 每角色最多提交 1 个任务，注释明确「The database claim remains the authority」。
- **批量语义正确**：`app/monthly/batch.py` `MAX_COMPANIES = 200`，逐企业报告，"one failing company never rolls back the others"。

### 4.3 关键问题（有证据）

0. **【已复现的 P0 缺陷】本地模式停机必然抛异常，且被 CI 掩盖**：`app/main.py:538` 在 lifespan 收尾调用 `worker.wait_for_idle(30)`，但 `wait_for_idle` 只定义在 `app/unified_worker.py:159` 的 `UnifiedWorker` 上；`worker` 此处是 `app/jobs/worker.py:385` 的 `QueueWorker`（其方法仅 `recover/renew/_failure/process/process_proposal`）。实跑结果：
   ```
   E   AttributeError: 'QueueWorker' object has no attribute 'wait_for_idle'
   nexus_backend\app\main.py:538
   → 2 failed, 3 errors, 61 passed（test_posting_api.py / test_accounting.py 的 HTTP 用例在 lifespan 退出时崩）
   ```
   而 CI 掩盖了它：`app/main.py:482-489` 的 `embedded` 默认仅在 `settings.mode == "local"` 时为 `1`，且 `:490-491` 在 postgres/production 下直接 `raise`——**CI 用 PostgreSQL 跑，`embedded=False`、`worker=None`，第 538 行的 `if worker:` 永远短路**。后果：SQLite 本地试点（`docs/BACKUP_RECOVERY.md` 明确支持的部署形态）每次关停都异常退出，`unified_drain_incomplete` 永不置位，连接池可能被在活线程下关闭。这也证明**停机路径没有任何 CI 覆盖**。
1. **无内置调度器（无 cron/beat）**：全库无 APScheduler/Celery beat，唯一的到期字段是 `ops_company_refresh.next_due_at`（`app/operations/service.py:1025`）。"每月 1 日自动开期 / 每月 15 日报税提醒 / 逾期催收"**必须由外部 cron 调 HTTP 触发**——运维缺一环就整月不动，且没有任何"上次调度时间/漏跑告警"。
2. **无死信队列、无指数退避**：`grep dead_letter` = 0、`grep backoff` = 0；重试延迟是调用方传入的 `delay` 参数，默认 0——**失败即立刻重试**，对上游限流（OCR/模型）是二次伤害。
3. **重试集合含 `cancelled`**：`RETRYABLE = {"failed","interrupted","stale","cancelled"}`——人为取消的任务会被自动重试，语义冲突（若产品确有此意，应改名 `resumable` 并加显式标志）。
4. **写路径 56% 抢同一把全局咨询锁**：`migrations/postgres/077_company_write_gate.sql:10` 定义全局键 `pg_advisory_xact_lock(6420031786281903101)`，`:25-26` 让 company 路径"先取共享锁、再取企业锁"；更关键的是 `:45-47` 会**扫描并重写所有已有函数的函数体**，把裸 `pg_advisory_xact_lock(全局键)` 一律替换成 `accounting.lock_legacy_write()`，再 `:103-104` 为**每张表**挂 `BEFORE ROW` 与 `BEFORE STATEMENT` 双触发器兜底。`app/` 下 `transaction(write=True)` **165 处**，其中显式 `write_scope="company"` 仅 **73 处**。→ 约 **92 条写路径在跨租户维度串行**，且语句级触发器让"同一事务多条语句"反复抢锁，与"代账中心 200 家客户同时月结"直接冲突。
5. **连接池极小**：`app/accounting/database.py:318` `ACCOUNTING_DB_POOL_MAX` 默认 **4**、超时 5s、`max_waiting 32`。叠加全局锁后，吞吐上限由"锁 + 4 连接"共同决定。
6. **批量能力只覆盖开期**：`app/batch_ops/models.py` 仅有 `CreateMonthlyOpenBatch` 与 `RetryBatchRequest`；**没有跨客户批量生成/批量复核/批量过账/批量出表**。200 家客户的月末，仍要逐户点进去操作。

### 4.4 改良建议

**高**：把 077 的"全域兜底锁"降级为"仅未声明 `write_scope` 的兼容域"，并把剩余 92 条写路径逐个标注 company scope（有现成的 `lock_company_write` 与迁移 111/112/114 范式可抄）。
**高**：补 `scheduler`（哪怕是一个 `accounting.scheduled_tasks` 表 + worker 每 tick 扫到期行），把"月结/报税/催收"从外部 cron 收回系统内，并记录 `last_run_at/next_run_at/missed`。
**高**：加死信表 + 指数退避（`delay = min(2^attempt * 5s, 15min)`），把 `cancelled` 从 RETRYABLE 摘出。
**中**：批量作业扩展到"批量复核 / 批量过账 / 批量出表"，复用 `batch_ops` 的父子状态机与逐企业报告。
**中**：池默认值按部署规模可配置化（文档给出并发估算公式），并暴露 `pool_snapshot()` 到运维面板（已有该函数）。

### 4.5 对代账业务的实际影响

代账的峰值是**每月 1–15 日**：200–2,000 家客户在一个窗口内完成开期、取票、做账、复核、出表、报税。当前架构在**功能上**支持，在**吞吐上**会在全球锁处排队；耗时随客户数近似线性放大，且失败重试没有退避，容易在窗口期雪崩。这是"能跑 50 家、跑不动 500 家"的典型形态。

---

## 五、维度四：风险控制（9.0 / 10）——本库最强项

### 5.1 财务数据准确性保障（证据密集）

| 机制 | 实现位置 | 说明 |
|---|---|---|
| **金额整数分 + 白名单正则** | `app/accounting/invariants.py` | 拒绝 float、拒绝指数、最多 2 位小数；`MAX_CENTS` 上限防溢出 |
| **正式凭证 append-only** | `app/posting/service.py:1` | 文档串即不变量；更正走"明确更正流程"而非改旧凭证 |
| **三条状态轴互不覆盖** | 同上 | 复核 / 内部过账 / 对外交付 分离，杜绝"导出即入账"混淆 |
| **报表快照带模板版本** | `app/reports/service.py` | `TEMPLATE_ID="small_enterprise_basic"`、`TEMPLATE_VERSION=1`、`effective_from="2026-01"`、映射方法名入快照 |
| **期初绑定指纹** | `app/posting/service.py` `_binding()` | 要求 `snapshot_fingerprint == package.config_fingerprint`；重复绑定同版本幂等，异版本 409「该账套已绑定其他期初版本；更正请走明确更正流程」 |
| **试算平衡自检** | `app/reports/service.py:216-234` | `balance_ok = total_assets == total_liabilities + total_equity + profit_in_equity`，不平衡即暴露 |
| **确定性期末草稿** | `app/periodic/models.py` | 「Generated lines must still be reviewed and posted by B03」——折旧/摊销/计提/结转全部落草稿，人工复核后才过账 |

### 5.2 关键操作的人工审核节点（四眼原则）

- **AI 不可越权**：`app/assistant/actions.py` 只允许 `ocr_batch / proposal_batch / bank_drafts` 三类**预览型**动作，且分两段：`ActionPrepare`（生成不可变预览 + 指纹）→ `ActionConfirm`（校验 64 位 hex 指纹 + `confirmed` 严格布尔 + 幂等键）。模型**不选 ID、不审批、不改业务事实**。
- **AI 回复发布边界**：`app/assistant/fact_validation.py` 对每个叶子做类型白名单（`amount`、`gross_amount`、`lines/N/debit` 等），缺失即弃用，冲突即弃用；弃用兜底句为「当前回复缺少可核验的事实依据，请人工核对当前资料；仍须会计复核后导出。」；渲染时服务端强制加注「（待人工核验的 OCR）」/「（当前已保存，尚不代表会计审核通过）」。
- **所有命令必带 `reason`（3–1000 字）与 `expected_version`**：`app/reports/router.py` `ReportCommand(expected_version, reason)`，全库一致；乐观并发用 `UPDATE ... WHERE version=? AND status='draft'`，`rowcount != 1` 即 409（`app/accounting/service.py:1213-1218`）。
- **申请人 ≠ 审核人**：`app/accounting/service.py:1209` 「申请人不能审核本人业务事项的凭证」，依据 `business_mappings → business_events.applicant_id` 反查。

### 5.3 审计轨迹（超出常规）

- `audit_events` 对应用角色**只授予 `SELECT, INSERT`**：`migrations/postgres/001_accounting.sql:199` `GRANT SELECT,INSERT ON accounting.audit_events TO accounting_app;`，策略仅 `audit_read`(SELECT) 与 `audit_append`(INSERT)——**数据库层保证审计日志不可篡改**，不靠应用自觉。
- 29 个业务动作写审计，含 `posting.opening.bind`、`draft.approve`（记录 `reviewed_version` 与逐行金额）。
- CSV 注入防护：`app/accounting/service.py` `safe_csv_text()` 对 `= + - @ \t \r \n` 前置 `'`——导出给客户/税局的 CSV 不会变成公式炸弹。

### 5.4 关键问题（扣分点，均为"制度已写、代码未锁"）

1. **制单人与审核人可同一人**：四眼原则只在"客户申请"场景硬校验；事务所内部由导入/OCR 生成的草稿，**同一账号可自审自过**。`app/accounting/onboarding.py:40-41` 的 `staffed_books` 只要求"存在可编辑的人"且"存在可复核的人"，**未要求二者不同人**（对比同文件 `:47-49` 的审批人统计显式写了 `user_id<>s.user_id`）。上岗检查是"建议"，不是"约束"。
2. **税务口径 = 无**：`app/tax/estimate.py:77` 返回 `"filing": "not_filed"`，全库无税率表、无申报表结构、无税会差异台账。**报表上的"应交税费"没有任何申报口径校验**。
3. **现金流量表是启发式**：`app/reports/service.py` `_cash_flow()` 对凭证按"现金行对方科目类别"归类，含多笔现金行且无对方科目的凭证直接进 `unclassified`（有 `unclassified_count` 兜底，诚实但不等于正确）。
4. **账簿输出缺位**：`app/ledgers` 提供余额表/试算平衡，但**没有总账、明细账、日记账、多栏账的标准化输出**——税局稽查与客户交接第一个要的就是这些。
5. **无数据保全承诺**：已实现备份/恢复演练（`docs/BACKUP_RECOVERY.md`，含 `pg_dump --snapshot` 与原件哈希一致性、恢复环境 `serving_enabled=false` 熔断），但**未实现"企业资料交付包"**（文档表格明确写"尚未实现"），而这是代账合同解约的法定动作。

### 5.5 改良建议

**高**：在 `approve` 增加 `approved_by <> created_by`（或 `<> 最近一次 draft 编辑人`）校验，事务所内部至少做到"经办会计 ≠ 复核会计"，并对 owner 单人事务所提供"书面豁免 + 审计留痕"开关。
**高**：建立 `tax/` 最小闭环：税率表（按纳税人类型/税目/有效期）、销项-进项勾稽、增值税测算与附加税、申报表草稿（不申报），并把 `estimate_vat` 接进 API 与月结检查清单。
**高**：用 `tax_profile` 与报表"应交税费"做**交叉校验**，差异超阈值即阻断关期。
**中**：补总账/明细账/日记账导出（同一 `reports` 快照机制，模板化）。
**中**：实现"企业资料交付包"导出（科目+凭证+账簿+报表+证据索引+原件哈希清单），解约时一键交付。
**低**：现金流量表增加"手工指定现金流项目"覆盖能力，并统计 `unclassified` 比例作为质量指标。

### 5.6 对代账业务的实际影响

风控维度是**可以直接拿去应对检查的**：审计日志不可改、正式凭证不可改、AI 不能过账、每次操作有理由与版本。这能显著降低"代账会计私自改账""AI 幻觉凭证"两类致命风险，也便于对客户交付"可核查"的服务承诺。
但**审计轨迹完整 ≠ 申报口径正确**：一旦客户被稽查，缺的是申报表与账簿，而不是凭证。

---

## 六、维度五：权限体系与数据隔离（8.5 / 10）

### 6.1 优点（有证据）

- **授权不信任 JWT**：`app/accounting/access.py:1` 「Authorization derives from current database membership, never JWT metadata.」；每次判定在**同一条 SQL** 里同时读当前授权与目标资源，且「There is no process/request authorization cache.」——撤权立即生效。
- **四级角色 + 能力求并集**：`FIRM_MANAGERS={owner,admin}`、`FIRM_EDITORS={owner,admin,operator,reviewer}`、`COMPANY_LEDGER_ROLES={owner,admin,finance}`，`book(capability="read"|"edit"|"review"|"manage")` 输出 `{read,edit,review,export,manage}`。
- **404 与 403 语义分离防探测**：无权限返回 404「账套不存在或无权访问」，有权限但角色不足返回 403——避免通过状态码枚举他人账套。
- **RLS 全域覆盖**：`users_read`/`firms_read`/`companies_read`/`books_read` 等策略以 `accounting.user_visible()/firm_visible()/company_visible()/book_allowed()` 为谓词；528 行策略覆盖 138 个迁移。
- **越权自我修改被阻断**：`app/accounting/admin_identity.py:76` 「不能通过开户邀请变更自己的权限」；`app/accounting/directory.py:379` 「企业管理员不能变更自己的账套权限，请联系事务所管理员」。
- **会话级作用域与熔断**：`src/api.ts` 用 `setApiScope` 递增 `scopeRevision` 并 abort 上一作用域全部在途请求；401 通过 `CustomEvent('accounting:unauthorized')` 全局登出，避免"切账号后旧响应覆盖新状态"这一经典越权/串账 bug。
- **对话与检索同样受权限裁剪**：`app/assistant/scope.py` 把权限谓词**放在聚合与分页之前**；被撤权的凭证/事项会连带隐藏整个会话（`assistant_conversation_allowed`），不是"先取全量再过滤"。

### 6.2 问题

1. **无内置 MFA/设备信任**：认证完全外包给 Supabase JWT；`tests/` 出现 `InsecureKeyLengthWarning: The HMAC key is 9 bytes long`（测试密钥，但说明校验未强制最小密钥长度）。
2. **越权测试面不透明**：90 个 postgres 测试覆盖 RLS，但默认 skip；**没有一套"角色 × 资源 × 动作"的矩阵化越权用例**（如 operator 尝试 review、viewer 尝试 export、跨 firm 读 company）。
3. **导出权限与数据出境未分级**：`export` 能力存在（`permissions()` 返回 `export`），但**没有导出审计的独立审批流**（导出后仅落 audit），大批量客户资料导出缺少二次确认与水印/限速。
4. **AI 授权许可按企业开关**（`onboarding` 的 `ai` 步骤），但**未见按凭证/期间的细粒度限制**（如"仅允许 AI 处理本月未审核草稿"）。

### 6.3 建议

**高**：补角色×资源的越权矩阵测试（可参数化生成，跑在 postgres），并纳入 CI 必过。
**高**：导出加"二次确认 + 一次性令牌 + 下载审计（谁/何时/多少行/文件哈希）"，批量导出需 manager。
**中**：JWT 校验显式要求 HMAC ≥32 字节；支持 MFA 或至少"新设备登录告警"。
**中**：AI 许可下沉到"账套/期间/动作"三级。

### 6.4 业务影响

代账中心的信任基础是"我的账别人看不到，我的人只能看该看的"。当前实现足以支撑**多事务所 SaaS** 的合规叙事（可对客户答"数据库层强制隔离"），但缺少可出示的**越权测试报告**与**导出管控**，在等保/客户尽调时会失分。

---

## 七、维度六：前端产品设计与交互审美（7.5 / 10）

### 7.1 优点（有证据）

- **角色化首页 + 能力过滤导航**：`src/workspace-presentation.ts:146-168` `primaryNav(role, profile)` 按 `can_review / can_accounting / can_manage / can_submit / can_approve / can_finance / can_firm_operations` **逐项过滤**，不是按角色字符串硬编码——权限改了导航自动变。
- **客户门户与事务所工作台分权**：`clientPortalTabs()` 对 `manager/viewer/employee/approver` 输出不同 tab 集合（`home/materials/messages/briefing/submit`），一个代码库服务两类用户。
- **样式体系化**：`src` 仅 **1 处** `style={{`，`any` 为 **0**；有 `styles/tokens.css` + 组件目录分工（`components/accounting`、`journal`、`primitives`），说明有设计系统意识而非拼页面。
- **并发冲突可见可解**：全库 `expected_version` 贯穿前后端，前端把 409 呈现为"凭证版本已变化，审核未提交"，而不是静默覆盖——**这是财务软件的关键交互**。
- **全局助手是"受约束的检索"而非聊天玩具**：`GlobalAccountingAssistant.tsx`(1,708 行) 展示事实引用与"待人工核验"标注，与后端 `fact_validation` 的兜底句一致。
- **测试分层**：45 个 vitest 文件覆盖 helpers/types/route/role-home/api（纯逻辑层），e2e 63 个文件 4 分片覆盖真实流程。

### 7.2 问题（有证据）

1. **`src/App.tsx` 1,784 行、34 个 `useState`、12 个 `useEffect`、~40 个路由/目标状态变量**：单组件承担会话、路由、工作台、抽屉、提示、缓存失效。`clearWorkspace()` 一次性重置 25 个状态——**任何一个漏重置就是跨账号串数据的隐患**。
2. **手写路由解析**：`readWorkspaceRoute(location.pathname, location.search)` 自己解析 URL，与 react-router 并存；路由成为"字符串约定"，无类型化 path 常量，重构靠搜索。
3. **无数据层缓存库**（无 react-query/swr/redux/zustand）：`src/api.ts` 手写 abort scope + 缓存失效，页面切换要重新拉 4–7 个接口；200 家客户的列表页无虚拟滚动证据，大列表渲染风险高。
4. **`GlobalAccountingAssistant.tsx` 1,708 行、`LegacyMigrationWorkspace.tsx` 3,141 行**：与后端巨型服务同构的"巨型组件"，可维护性同步下降。
5. **无 i18n 抽象**：全量中文硬编码（对代账行业可接受，但同期无法出海/多语言）。
6. **前端单测只测纯函数**：无组件级渲染测试（无 RTL），交互回归完全依赖 e2e（63 文件、分片跑），本地反馈慢。

### 7.3 建议

**高**：拆 `App.tsx`——`SessionProvider` / `WorkspaceRouter` / `ToastHost` / `DrawerHost` 四层；把 34 个 `useState` 收敛为 3–4 个域 store（可用 zustand，仅 2KB）。
**高**：引入 react-query（或自建 `useResource`）做请求缓存与失效；列表页上虚拟滚动（200+ 客户是代账常态）。
**中**：路由改为 `route.ts` 的常量 + 类型化参数，删掉手写解析。
**中**：为关键页（凭证网格、复核抽屉、月结看板）补 RTL 组件测试，覆盖键盘与空态。
**低**：抽出 `t()` 与文案表，为未来多语言留口。

### 7.4 业务影响

代账会计是**高频、键盘驱动、批量操作**的用户群：一天要看几十本账、几百张凭证。`App.tsx` 的重渲染与"切页重拉"会直接表现为**卡顿与重复等待**；手写路由与 25 个状态的重置逻辑，则是"偶发串账"这类最难排查的生产事故温床。审美层面（配色、留白、排版）从代码组织看是有节制的，未见花哨，符合财务软件"安静、可信"的调性。

---

## 八、维度七（补充）：领域功能覆盖与业务闭环（5.5 / 10）

用户未在截断的维度清单中列出此项，但作为代账审计必须给出，因为它决定"能不能真替代人工"。

| 代账中心日常能力 | 覆盖度 | 证据 |
|---|---|---|
| 取票/OCR/发票导入 | ✅ 强 | `app/intelligence` 5,882 行、`app/invoices` 只落证据 |
| 银行流水导入与勾对 | ✅ 有 | `app/import_wizard`、`app/reconciliation` |
| 业务事项 → 凭证草稿 | ✅ 强 | `app/business` + `app/accounting` 确定性规则 |
| 复核 / 导出 / 过账 | ✅ 很强 | 三状态轴 + 版本并发 |
| 期末计提/折旧/摊销/结转 | ✅ 有（直线法） | `app/periodic`，仅 `life_months` 直线 |
| 期初建账 / 旧账迁移 | ✅ 很强 | `legacy_migration` 7,078 行 + 结算/学习/校验 |
| 财务报表（三表） | ⚠️ 基础版 | 单一模板 `small_enterprise_basic` v1；现金流量表启发式 |
| 总账 / 明细账 / 日记账 | ❌ 缺 | `app/ledgers` 仅余额表/试算 |
| **纳税申报（增值税/附加/企税/个税/印花税）** | ❌ **缺** | `app/tax` 79 行，`filing: "not_filed"` |
| 发票开具 / 进项勾选认证 | ❌ 缺 | 仅"读入发票导出表" |
| 工资 / 社保 / 个税代扣 | ❌ 缺 | 全库无 `工资/薪酬/社保` 关键字命中 |
| 固定资产卡片与折旧方法 | ⚠️ 部分 | 仅直线法草稿，无卡片台账/变更 |
| 客户解约资料交接包 | ❌ 缺 | `docs/BACKUP_RECOVERY.md` 表格注明"尚未实现" |
| 多客户批量作业 | ⚠️ 仅开期 | `app/batch_ops/models.py` 两种请求 |
| 按账套计费/结算 | ✅ 有 | `app/settlements`、`app/service_plans` |
| 交付/回访/效率度量 | ✅ 有 | `app/delivery`、`app/operations`、`app/quality` |

**结论**：代码把"记账 + 复核 + 过账 + 出表 + 迁移 + 计费 + 质检"做成了闭环，**但把"报税"整块留白**。按工时结构，代账中心约 **40%–50%** 的工作量在税务与薪酬，因此当前系统能替代的是"记账户"，不能替代"税务会计"。

---

## 九、整改路线图

### P0（0–30 天，风险阻断）
0. **修 `QueueWorker.wait_for_idle` 缺失**（`app/main.py:538`）：给 `QueueWorker` 补 `wait_for_idle(timeout)` 或在调用处按类型分支；并新增"local 模式 lifespan 启停"测试（当前 CI 因 postgres 路径短路而完全未覆盖停机）。
1. **制单 ≠ 复核**硬校验 + 单人事务所豁免开关（`app/accounting/service.py:1199` 附近）。
2. 写路径 `write_scope` 补齐（92 条 → 目标 <20 条），先做 `posting / closing / monthly / delivery` 四个高并发域。
3. `pytest-timeout` + 死信表 + 指数退避；`cancelled` 移出 RETRYABLE。
4. 角色×资源越权矩阵测试进 CI（postgres）。

### P1（30–90 天，业务补齐）
5. `tax/` 最小闭环：税率表 → 增值税/附加测算 → 申报表草稿 → 与"应交税费"交叉校验。
6. 内置调度器（`scheduled_tasks` 表 + worker 扫表），接走外部 cron。
7. 总账/明细账/日记账导出（复用报表快照机制）。
8. 批量复核/批量过账/批量出表。
9. `mypy` 渐进 + 财务包 85% 覆盖率门禁。

### P2（90–180 天，架构与产品）
10. `Dialect` 适配层，消除 185 处 `is_postgres`。
11. 拆分 `App.tsx` 与三大巨型服务；引入 react-query + 虚拟列表。
12. 企业资料交接包 + 导出审批/水印/限速。
13. 固定资产卡片、工资社保模块（若目标客户含全代账）。

---

## 十、审计局限（必须声明）

- 本次为**静态审阅 + 工具核验 + 定点实跑**：PostgreSQL 专项测试（90 个文件）在本机因缺少 `ACCOUNTING_TEST_POSTGRES_URL` 等变量而跳过，**RLS 与并发围栏的运行时行为未经本机实证**，结论基于 SQL 与代码阅读。
- 定点实跑（5 个财务核心测试文件）：`61 passed, 2 failed, 3 errors in 103.31s`，失败全部源于 `app/main.py:538` 的 `QueueWorker.wait_for_idle` 缺失（见 §4.3-0），非财务计算逻辑错误——**已过账、报表、税金估算的断言全部通过**。
- 全量 pytest 在本机运行超过 22 分钟未结束（约为 3,800 用例、均值 ≈1.5s/用例），且 `pytest-timeout` 未安装、无法用 `--timeout` 约束，因此**未取得完整通过率**；这不影响对设计的判断，但本身计入"测试工程"扣分。
- 未做渗透测试、未做性能压测；吞吐结论来自锁粒度与连接池配置的推理，建议以 200 家客户月结场景做一次实测。
- 未审计 `services/`（API Gateway 等本地服务包装）的深度实现，仅确认其在 CI 中被测试。

---

## 附录 A：核心证据索引

| 主题 | 文件:行 |
|---|---|
| 确定性内核声明 | `nexus_backend/app/accounting/service.py:1` |
| 金额不变量 | `nexus_backend/app/accounting/invariants.py`（`MONEY_PATTERN`、`MAX_CENTS`） |
| 授权实时推导 | `nexus_backend/app/accounting/access.py:1`、`FIRM_MANAGERS` 常量段 |
| 全局写锁 + 函数体重写 + 双触发器兜底 | `nexus_backend/migrations/postgres/077_company_write_gate.sql:4,10,25-26,45-47,103-104` |
| 审计表只增授权 | `nexus_backend/migrations/postgres/001_accounting.sql:199,263,265` |
| 过账三状态轴/期初指纹 | `nexus_backend/app/posting/service.py:1`、`_binding()`、`bind_opening()` |
| 报表平衡自检/现金流启发式 | `nexus_backend/app/reports/service.py:216-234`、`_cash_flow()` |
| AI 发布边界 | `nexus_backend/app/assistant/fact_validation.py`（`_descriptor`、`FALLBACK`） |
| AI 有界工具 | `nexus_backend/app/assistant/actions.py:1-6`、`ActionKind` |
| 税：未申报 | `nexus_backend/app/tax/estimate.py:77` |
| 上岗检查非强制双人 | `nexus_backend/app/accounting/onboarding.py:40-41` vs `:47-49` |
| 申请人≠审核人 | `nexus_backend/app/accounting/service.py:1209` |
| 乐观并发 409 | `nexus_backend/app/accounting/service.py:1213-1218` |
| 方言翻译器 + 单测 | `nexus_backend/app/accounting/database.py:85-130`；`nexus_backend/tests/test_postgres_adapter.py:15-21` |
| 池默认 4 连接 | `nexus_backend/app/accounting/database.py:318` |
| 批量上限 200 / 命名瑕疵 | `nexus_backend/app/monthly/batch.py:27,29` |
| 批量仅开期 | `nexus_backend/app/batch_ops/models.py` |
| 备份边界与未实现项 | `docs/BACKUP_RECOVERY.md:5-8` |
| 前端巨型组件 | `src/App.tsx:75`（1,784 行） |
| 角色化导航 | `src/workspace-presentation.ts:146-168,170-176` |
| 会话作用域熔断 | `src/api.ts`（`setApiScope` / `invalidateApiScope` / `accounting:unauthorized`） |
| **停机崩溃缺陷（已复现）** | `nexus_backend/app/main.py:538`（`QueueWorker.wait_for_idle` 不存在）vs `nexus_backend/app/unified_worker.py:159`（方法只在 `UnifiedWorker` 上）；掩盖条件 `nexus_backend/app/main.py:482-491` |
| 嵌入式 worker 安全护栏 | `nexus_backend/app/main.py:490-493`（非 local/生产/postgres 直接 raise；租约 <150s raise） |

## 附录 B：复现命令

```bash
# 规模
find nexus_backend/app -name '*.py' | xargs wc -l | tail -1
wc -l src/*.tsx | sort -rn | head

# 锁与方言
grep -rn "pg_advisory_xact_lock(6420031786281903101)" nexus_backend/migrations/postgres/*.sql | wc -l
grep -rn "transaction(write=True" nexus_backend/app | wc -l
grep -rn "write_scope=" nexus_backend/app | wc -l
grep -rn "is_postgres" nexus_backend/app | wc -l

# 重复横切
grep -rn "def _audit" nexus_backend/app | wc -l
grep -rn "INSERT INTO audit_events" nexus_backend/app | wc -l

# 测试
./.venv/Scripts/python.exe -m pytest nexus_backend/tests -q
```

---

*审计人：AI Agent 架构 / 会计信息系统审计视角；本报告仅基于所提供代码库的快照，不构成法律或税务意见。*
