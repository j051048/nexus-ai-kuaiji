# P03 批量办理验收报告

- 日期：2026-10-29
- HEAD：`8a21224d test: add recovery inventory coverage and baseline docs`
- 结论：**阻断**。P01 报告缺失、P02 仅"部分完成"，进入条件不满足；按工作令"P01/P02 未通过只允许盘点与独立测试准备"，本轮交付盘点、缺口、双版本回归与测试设计，**不宣布批量集成通过**。

## 1. 前置核验

| 前置 | 要求 | 实际 | 判定 |
| --- | --- | --- | --- |
| P01 | `docs/P01_RECOVERY_ACCEPTANCE.md` 且恢复门槛通过 | **文件不存在**，仅 `P01_RECOVERY_COVERAGE_20261029.md`，自述 PG16/17 联合恢复未执行 | 未通过 |
| P02 | 真实前后端单企业闭环通过 | `P02_BUSINESS_FLOW_ACCEPTANCE.md` 结论"部分完成"：月结被 `subledger_control`、`bank_reconciliation` 阻断，锁期/发布/交付/签收未跑通 | 未通过 |
| N02/N03 门禁 | PG16/17 必跑门禁可执行 | 两版集群已拉起（16.15:25531、17.11:25532），受限角色可用；门禁本身本轮未复跑（零产品代码改动） | 可执行，未复跑 |
| 候选一致性 | 证据绑定同一候选 | 三份报告与当前 HEAD 一致，无跨候选拼接 | 通过 |

## 2. 文件归属与修改范围

| 类型 | 路径 | 说明 |
| --- | --- | --- |
| 本轮修改 | `nexus_backend/tests/test_batch_review_postgres.py` | 仅修一处跨企业用例建模（第 6.1 节），产品代码零改动 |
| 本轮新增 | `docs/P03_BATCH_ACCEPTANCE.md` | 本报告 |
| 在途/他人 | `docs/P02_BUSINESS_FLOW_ACCEPTANCE.md`、`tests/test_p02_business_flow_postgres.py`、`docs/P04_HANDOFF_ACCEPTANCE.md` | 原样保留 |
| 未触达 | `nexus_backend/app/**`、`nexus_backend/migrations/**`、`src/**` | 无迁移、无产品代码改动 |

## 3. 已有能力盘点（以当前代码为准）

### 3.1 跨企业批量作业 `app/batch_ops`

| 能力 | 入口 | 状态 |
| --- | --- | --- |
| 批量预检 | `POST /api/batch-ops/monthly-open/preview`（`router.py:12`） | 有，只读，逐企业返回 ready/skipped/blocked |
| 批量创建 | `POST /api/batch-ops/monthly-open`（`router.py:20`，202） | 有，父任务 + 逐企业子项 |
| 列表/详情 | `GET /api/batch-ops`、`GET /api/batch-ops/{id}`（`router.py:28,37`） | 有 |
| 重试 | `POST /api/batch-ops/{id}/retry`（`router.py:45`） | 有 |
| 取消 | — | **无端点** |
| 任务类型 | `service.py:69` 写死 `kind='monthly_open'`；`:342-343` 其它类型 422 | **仅批量开期** |

机制（可复用的部分）：父/子任务持久化（`batch_operations`、`batch_operation_items`）、lease + fencing（`worker_claim_batch_item` / `worker_finish_batch_item`，`migrations/postgres/098_batch_operations.sql:73-160`）、`FOR UPDATE SKIP LOCKED` 领取、attempt/max_attempts、父状态由子项计数推导（`service.py:274-305`）、审计（`batch.monthly_open.enqueue` / `batch.retry`）。

### 3.2 已有的跨账套批量复核（不在 batch_ops）

`app/accounting/review.py:378-467` `ReviewService.confirm`：按 `book_id` 分组，逐账套 `write_scope="company"` 事务（`:387-389`），逐账套回执，异常按账套隔离并聚合 `completed/partial/failed`；路由 `app/accounting/review_router.py:62-72`。**这是目前唯一"多对象批量 + 逐项结果 + 企业级锁"的成熟实现**，P03-A/B 应复用它而不是另建。

### 3.3 单笔动作真实入口与前置校验

| 动作 | 服务入口 | 关键前置 |
| --- | --- | --- |
| 草稿生成/建议 | `AccountingService.suggestions` | 来源未变、期间 open |
| 复核 | `AccountingService._approve_in_transaction`（`service.py:1217`） | reviewer 权限、制单人≠复核人、版本匹配、policy_version |
| 过账 | `PostingService._book` | 期初冻结包绑定、期间 open、借贷平衡 |
| 导出 | `AccountingService.export`（`service.py:1287-1372`） | 仅 `status='approved'`，显式 `draft_ids`，幂等键指纹比对 |
| 交付包 | `MonthlyPackageService.create/acknowledge` | 报表已发布、`contact_user_id`、manifest 哈希 |
| 关期 | `ClosingService.preflight/review/close` | 草稿全部 exported、回执一致、本人复核 |

### 3.4 队列与调度既有机制

`app/jobs`：DB 租约（180s 默认）、`attempt_count/max_attempts`、按企业互斥 + `ocr_company_schedule` 公平轮转、`RETRYABLE/ACTIVE/TERMINAL` 状态集、`_invalidate_run` 置 stale；N03 外发状态机 `sending/unknown` + `restore_epoch` 去重。**批量作业未接这些能力**（见 D1/D5）。

## 4. 缺口（按 P03-A/B/C/D 对齐）

| 编号 | 缺口 | 证据 | 影响 |
| --- | --- | --- | --- |
| D1 | 批量执行在应用层套全局写锁 | `service.py:33-38` `SELECT accounting.lock_legacy_write()`；而 SQL 函数本身用 `SKIP LOCKED`（`098:73-105`） | B05 公平调度不成立：全所批量吞吐≈1 项/次，且与记账/OCR 写互斥 |
| D2 | 重试把 `attempt` 清零并复活 `cancelled` | `service.py:159-164` | B03/B04：attempt 预算失效，可无限重试；取消语义被破坏 |
| D3 | 无批量导出、无批量交付准备 | `batch_ops` 仅 `monthly_open`；导出/交付只有单账套入口 | P03-B/C 功能不存在（非未接线） |
| D4 | 无取消端点 | `router.py` 无 cancel | P03-D"取消停止领取"用户触发不到 |
| D5 | 批量执行对 worker 不可见执行记录 | `execution/router.py:23` kind 仅 `chat|ocr|journal|intake` | 失败项无法进入人工接手（与 P04 的 G1 同一断点） |
| D6 | 批量项缺少对象/版本/来源指纹绑定 | `batch_operation_items` 仅存 `company_id/company_name/request_json` | P03-B"每项绑定对象ID/版本/来源指纹"无字段可落 |

## 5. 测试设计（本轮只设计，未执行）

| 编号 | 场景 | 断言 |
| --- | --- | --- |
| B01 | 混合权限批次 | A 可写、B 只读、C 无权；无权企业名/金额不出现在响应；领取后撤权重检失败 |
| B02 | 预检后来源/审核变化 | 执行时重算资格，旧审核不得导出新版本；人工编辑草稿不被覆盖 |
| B03 | 幂等与并发 | 同键同请求返回原批次；同键异内容 409；两进程只成功一次；旧 lease 不能覆盖 |
| B04 | 部分成功与取消 | A 成功/B 失败/C 未执行；重试只处理失败项；取消后统计与审计一致 |
| B05 | 公平与未知结果 | 慢企业不长期占满；`sending/unknown` 不换键重发；重启/租约过期行为正确 |
| B06 | 前端 | 刷新不重复提交；撤权后旧响应不复活；逐项原因与下一步可见；按钮可用性来自服务端资格 |

其中 B03（幂等冲突）、B04（重试范围）可在现有 `monthly_open` 上独立验证；B01/B02/B05/B06 依赖 D1–D6 的先修。

## 6. 测试与修复证据

### 6.1 红→绿：跨企业批量复核用例建模错误（本轮唯一改动）

```
pytest tests/test_batch_operations.py tests/test_batch_preview.py tests/test_monthly_batch.py \
       tests/test_batch_review.py tests/test_batch_review_postgres.py -q
```

修复前 PG16：`1 failed, 17 passed` —— `test_pg_cross_company_confirmation_and_stable_receipts` 期望 `approved_count == 2` 实得 `0`。临时脚本取证（已删除）显示预览已给出原因：

```
"mode": "independent", "can_review": false,
"blocked_reason": "制单人不能审核本人创建版本，请交给另一位有权限的会计"
```

用例里 `bank_draft()` 以 `case.owner` 制单、又以 `case.owner` 复核，被独立复核政策正确拒绝——**测试建模错误，非产品缺陷**（与 Q-S04 同类）。修法：为 `case.reviewer` 补第二账套 `book_access(reviewer)`，改用 reviewer 预览与确认，回执查询同身份。

### 6.2 双版本回归（同一候选、同一套件）

| 版本 | 命令 | 结果 |
| --- | --- | --- |
| PostgreSQL 16.15（25531） | 上述 5 个测试文件 | **43 passed, 1 warning in 40.76s** |
| PostgreSQL 17.11（25532） | 同上（重建库后 001–178 全量迁移） | **43 passed, 1 warning in 41.08s** |

受限角色：`p02_app_pg16/17`、`p02_wrk_pg16/17`（密码 20 位，`tmp_test/p02-pg.local.json`，gitignored）；迁移状态 `status --strict` 通过。两版均为真实 PG + 真实 FastAPI，无 skip。

### 6.3 未执行项（如实标注）

| 项目 | 原因 |
| --- | --- |
| 真实浏览器（桌面/390px） | 批量前端未提供导出/交付入口，无可操作对象；P02 亦无浏览器基线 |
| 50/200 家合成压测 | 前置未通过；且现有批量只有"开期"，压测结果不能代表 P03 目标业务容量；不编造数字 |
| 多进程并发 / 重启 / 恢复 hold | 依赖 D1 修复（全局锁掩盖真实竞争） |
| N02/N03 固定门禁复跑 | 本轮零产品代码改动，结果不构成本轮候选证据，故不引用 |

## 7. 结论与下一步

- **结论：阻断**。不得进入 P04 的批量人工接手验收，也不得对外声称"批量产品完成"。
- 本轮可复用产出：能力盘点（第 3 节）、缺口 D1–D6（含行号）、B01–B06 测试设计、双版本 43 passed 基线。
- 最小可行路径：
  1. 修 P02 F1（阻断详情结构化）与月结两处阻断，使 P02 转"通过"；
  2. 补 P01 的 PG16/17 联合恢复，产出 `P01_RECOVERY_ACCEPTANCE.md`；
  3. 复用 `ReviewService.confirm` 的逐账套模式扩展 P03-A/B，`batch_operation_items` 前向迁移补 `object_id/version/source_fingerprint/review_version`（D6）；
  4. 把 `worker_transaction()` 的全局锁改为按企业取锁（D1），补取消端点（D4）与执行记录 kind（D5）。

本轮未提交、未推送、未部署、未发送真实通知。
