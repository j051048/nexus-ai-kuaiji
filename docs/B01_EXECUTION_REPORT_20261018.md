# B01 现有风险修复：执行报告

日期：2026-10-18
基线：`f7bd7b12`（执行前核对；PostgreSQL 迁移已到 `101`）
范围：仅 B01.1–B01.4。未开始总账（B02+），未修改生产环境，未部署，未执行真实数据外发。
性质：代码与测试已改；PostgreSQL 触发器因本机无专用测试 DSN，标记为未验证。

---

## 0. 结果摘要

| 子项 | 状态 | 本地验证 |
|---|---|---|
| B01.1 简报金额与来源口径 | 已修复 | 4 个用例通过 |
| B01.2 完整原件备份与恢复 | 已修复 | 9 个用例通过 |
| B01.3 审核边界与数据库防绕过 | 代码与测试已加；PostgreSQL 未跑 | SQLite 相关 65 通过；PG 用例 skip |
| B01.4 外部接入先封边界 | 已封边界；完整消费链后置 | 5 个用例通过 |

**结论**：B01 可在本地证明的三项已通过；B01.3 的数据库触发器必须在有 `ACCOUNTING_TEST_POSTGRES_URL` / `ACCOUNTING_TEST_DATABASE_URL` 的环境重跑后才能宣称完成。

---

## 1. B01.1 简报金额与来源口径

### 复现证据

- `nexus_backend/app/monthly/briefing.py` 原第 105 行计算 `net = income - expense`，第 120 行用 `cents_to_money(net)`；该函数只接受非负数，净流出月份直接 500。
- 银行来源汇总只按 `source_kind='bank'` 聚合，未排除 `bank_source_dispositions`（已隔离重复）和 `fact_revision_lineage`（替代前来源）。
- 费用排名使用 `ABS(...)` 并格式化绝对值，费用冲回会显示成正数。

### 修改

| 文件 | 改动 |
|---|---|
| `nexus_backend/app/accounting/invariants.py` | 新增 `signed_cents_to_money`；不放宽 `cents_to_money` 的非负约束 |
| `nexus_backend/app/closing/queries.py` | 新增 `active_source_sql`，与关期预检共享“有效来源”口径 |
| `nexus_backend/app/monthly/briefing.py` | 银行流水、费用排名排除已隔离/替代前来源；净额、余额、税额、费用金额改用有符号格式化；更新基础说明 |

### 测试

`test_monthly_briefing.py` 新增：

1. `test_briefing_negative_net_flow_is_signed_not_a_server_error`：只有 300 元支出时，返回 `net=-300.00`，不再 500。
2. `test_briefing_excludes_isolated_duplicate_sources`：两份 0.30 元重复流水隔离后，简报只计一次。

结果：`4 passed`。

### 验收对应

- 净流出月份：通过。
- 缴费/退款/费用冲回：有符号金额与 `active_source_sql` 已覆盖；退款/冲回的具体样本可在 B04 报表样本中继续补。
- 正常贷方应付余额：`signed_cents_to_money` 支持负数。
- 父级与辅助明细：费用排名按科目汇总，不重复计数；辅助维度仍由余额表/明细账承担（B04）。
- 重复来源隔离：通过。

---

## 2. B01.2 完整原件备份与恢复

### 复现证据

`scripts/backup_accounting.py` 原备份与恢复各维护一份附件表清单，只含：

`business_attachments`、`case_attachments`、`intake_attachments`、`bank_import_files`、`migration_files`。

漏掉：

- `material_attachments`（月度资料原件）
- `invoice_import_batches`（发票导入原始表）
- `upload_intents`（已存储但未消费的两段式上传原件）

### 修改

| 文件 | 改动 |
|---|---|
| `scripts/backup_accounting.py` | 新增统一 `ORIGINAL_TABLES` 登记表（8 类）；备份、校验、恢复共用 `original_rows`；清单增加 `originals_policy_version`、`database_schema_version`、`original_tables` |
| `nexus_backend/tests/test_stage10_backup_recovery.py` | 新增“全部登记表”备份/恢复用例；缺失原件阻断 |

### 兼容性

- `format_version` 仍为 1；旧备份可继续 `verify`。
- 新清单多出的字段对旧恢复路径是可选信息；恢复以数据库当前实际引用为准。
- `upload_intents` 只备份 `stored/consuming/consumed`，不备份 `pending/discarded`。
- 未把身份系统、角色映射、平台配置和密钥纳入包内；报告与文档需继续明确“灾难恢复包”与“企业资料导出包”的区别。

### 测试

`test_stage10_backup_recovery.py`：`9 passed`。

新增用例：

- 为 8 类原件各建一行与一个文件，备份清单包含 8 个原件 + 1 个数据库；恢复后逐项字节一致。
- 删除其中一个原件后，备份必须失败，且不得留下 `manifest.json`。

### 未验证

- 真实 Supabase 对象存储的完整备份/恢复未在本机执行；使用 `SyntheticStorage` 的既有用例通过。
- 身份系统与对象存储平台配置的恢复边界仍需在 B07 演练。

---

## 3. B01.3 审核边界与数据库防绕过

### 复现证据

- `AccountingService._approve_in_transaction` 的主要防线在 Python 服务层。
- `test_catalog_postgres.py` 原用例可直接 `UPDATE drafts SET status='approved'`，证明测试 SQL 可以绕过服务层把不平衡分录改成已审核；该证据不等于普通 HTTP 用户可越权，但说明数据库层缺少必要防线。

### 修改

新增 `nexus_backend/migrations/postgres/102_draft_review_boundary.sql`：

- `BEFORE UPDATE ON accounting.drafts` 触发器。
- 进入 `approved/exported` 时检查：
  - 期间必须 `open`；
  - 来源必须存在；
  - 来源不得是已隔离重复或替代前来源；
  - 借贷必须平衡且金额 > 0；
  - 所有科目必须存在且启用；
  - 未审核不得直接导出；
  - 已审核/已导出不得直接退回草稿。
- 不改变现有服务层校验；不新增“第三位过账人”。

新增 `nexus_backend/tests/test_review_boundary_postgres.py`：

- 直接 SQL 不平衡审核 → 拒绝；
- 关闭期间直接审核 → 拒绝；
- 未审核直接导出 → 拒绝；
- 跨企业直接审核 → 0 行，状态不变。

调整 `test_catalog_postgres.py` 的辅助核算冻结用例，改为平衡分录，避免与新的数据库边界冲突。

### 验证状态

- SQLite 相关回归：`65 passed, 13 skipped`（含 `test_accounting`、`test_stage10_dispositions`、`test_stage10_fact_revisions`、`test_intelligence_ledger`）。
- PostgreSQL 新用例：**未运行**，因本机没有 `ACCOUNTING_TEST_POSTGRES_URL` / `ACCOUNTING_TEST_DATABASE_URL`，且无 `psql`/Docker。CI 具备 PostgreSQL 服务时应执行。
- 未验证部分不得宣称 B01.3 完成；需在专用测试库重跑后再签收。

### 残余边界

- 触发器只挡“不平衡、关闭期间、失效来源、停用科目、未审先导、审核后回退”；不替代 Python 对证据、辅助核算、来源金额、审核人分离的校验。
- 服务层仍是权威；B03 引入正式记账命令后，应再增加命令上下文/幂等边界，而不是把全部规则塞进触发器。
- 直接 SQL 仍可由具备 `accounting_app` 权限的写者执行“平衡且合规”的审核；这属于数据库角色权限边界，B03 的正式记账命令会进一步收紧。

---

## 4. B01.4 外部接入先封边界

### 复现证据

- `ingestion/router.py` 原路由为 `/api/integrations/webhook/{token}`，令牌进入 URL 与访问日志。
- `receive_webhook` 未检查企业/事务所关系状态，未保存原始事件，未做事件幂等，也无请求体上限。
- 202 只表示 `received`，没有消费回执。

### 修改

| 文件 | 改动 |
|---|---|
| `nexus_backend/app/ingestion/service.py` | 默认关闭；header 令牌；企业/事务所关系状态检查；256 KiB 上限；`event_key` 幂等；保存 `raw_event_json`、`request_hash`；写审计；202 只返回 `received` |
| `nexus_backend/app/ingestion/router.py` | 路径改为 `/api/integrations/webhook`；令牌走 `X-Ingestion-Token`，事件键走 `X-Event-Id` |
| `nexus_backend/app/ingestion/middleware.py` | 在 JSON 解析前限制 256 KiB |
| `nexus_backend/app/main.py` | 注册中间件；按 `ACCOUNTING_INGESTION_ENABLED` 决定是否启用 |
| `nexus_backend/migrations/postgres/103_ingestion_receive_boundary.sql`、`sqlite/078_ingestion_receive_boundary.sql` | `ingestion_runs` 增加 `event_key/raw_event_json/request_hash` 与事件唯一索引；PostgreSQL 给 `accounting_system` 仅追加 `audit_events` 的 INSERT 权限 |
| `.env.accounting.example`、`deploy/.env.api.example` | 增加默认关闭开关 |

### 测试

`test_ingestion_service.py`：`5 passed`。

覆盖：

- 正常接收、原始事件保存、令牌轮换后旧令牌失效；
- 默认关闭返回 404；
- 企业停用返回 409；
- 重复事件只生成一条 run，返回 `duplicate=True`；
- 无事件键时用内容哈希幂等；
- 未知令牌 404、超 256 KiB 413。

### 明确后置

- 消费闭环、失败重放、消费回执、业务幂等落库尚未实现；当前 `ingestion_runs` 只到 `received`。
- 因此外部接入默认关闭，不能把 202 展示为“已导入/已记账”。
- 若试点确需接入，应按实际调用方补签名/头部凭据、消费 worker、重放与对账；不阻塞总账主线。

---

## 5. 数据库与迁移影响

| 迁移 | 内容 | 兼容性 |
|---|---|---|
| `102_draft_review_boundary.sql` | 审核边界触发器 | 新增，不改表；可 `DROP TRIGGER/FUNCTION` 回退 |
| `103_ingestion_receive_boundary.sql` | `ingestion_runs` 三列 + 事件唯一索引 | 新增列有默认值；旧数据 `event_key=''` 不参与唯一索引 |
| `sqlite/078_ingestion_receive_boundary.sql` | 同上（SQLite） | 本地演示库新增列 |

未修改已应用迁移；未预占固定编号；未批量设置兼容标记。

**回退边界**：应用回退到旧版本可继续读取新增列（列有默认值）；触发器需显式删除才能恢复“直接 SQL 审核”的旧行为。生产回退前应先备份并评估在途审核。

---

## 6. 测试命令与结果

```powershell
$env:PYTEST_PLUGINS='tmp_test.pytest_plugin'
python -m pytest nexus_backend/tests/test_monthly_briefing.py -q --basetemp=tmp_test/run -p no:cacheprovider
# 4 passed

python -m pytest nexus_backend/tests/test_stage10_backup_recovery.py -q --basetemp=tmp_test/run -p no:cacheprovider
# 9 passed

python -m pytest nexus_backend/tests/test_ingestion_service.py -q --basetemp=tmp_test/run -p no:cacheprovider
# 5 passed

python -m pytest nexus_backend/tests/test_accounting.py nexus_backend/tests/test_stage10_dispositions.py \
  nexus_backend/tests/test_stage10_fact_revisions.py nexus_backend/tests/test_intelligence_ledger.py \
  -q --basetemp=tmp_test/run -p no:cacheprovider
# 65 passed, 13 skipped

python -m pytest nexus_backend/tests/test_stage20_home.py nexus_backend/tests/test_catalog.py \
  nexus_backend/tests/test_business.py nexus_backend/tests/test_stage9_delivery.py \
  -q --basetemp=tmp_test/run -p no:cacheprovider
# 106 passed

python -m pytest nexus_backend/tests/test_review_boundary_postgres.py -q ...
# 4 skipped（无 PostgreSQL 测试 DSN）
```

全量 `nexus_backend/tests` 在本机沙箱运行时间过长，已停止；上面是 B01 相关定向回归，不把“全量通过”写成结论。**第二轮补全后的最新计数见第 10 节。**

说明：`tmp_test/pytest_plugin.py` 只是本机沙箱的测试权限补丁（pytest 以 0o700 建临时目录后沙箱无法列举），不属于产品代码；CI 不需要该补丁。本机 `tmp_test/`、`.pytest-tmp/` 因沙箱 ACL 无法彻底删除，已加入 `.git/info/exclude`，不会进入提交。

静态检查：

- `ruff check` 相关 app/scripts 文件：通过。
- `ruff format --check` 相关 app/scripts 文件：通过。
- `compileall`：通过。

---

## 7. 未验证与风险

| 项 | 状态 | 处理 |
|---|---|---|
| PostgreSQL 审核触发器 | 未跑 | 需专用 DSN 重跑 `test_review_boundary_postgres.py` |
| 真实对象存储备份恢复 | 未跑 | B07 演练；当前用合成存储验证逻辑 |
| HTTP header 路由 | 服务层已验证，路由层未单独 HTTP 测试 | 补一条 TestClient 用例或用部署冒烟验证 |
| 消费闭环/重放 | 未实现 | 外部接入默认关闭；实际集成批次再开 |
| 身份/角色/密钥恢复 | 未纳入包 | B07 明确 RPO/RTO 与身份恢复 |
| 生产迁移 | 未执行 | 按用户授权单独执行 |

---

## 8. 下一批依赖（B02）

B01 完成后，B02 可以开始，但必须先定：

1. 首批准则与行业范围（不能按“小规模纳税人”自动选小企业准则）。
2. 账本归属：外部正式账本 / 内部影子 / 内部正式，一期只能有一个权威。
3. 科目稳定 ID、父子层级、末级限制、辅助要求、版本与生效期。
4. 正式期初：年初/年中启用、本年累计、辅助余额、未结事项。
5. 历史迁移边界：归档与正式期初分开，不把 `exported` 自动回填为 `posted`。

B01.3 的触发器是“防绕过”，不是正式记账命令；B03 应在此之上增加正式凭证、编号、红冲和命令幂等，不要把 `drafts` 原位改成凭证表。

---

## 9. 变更文件

**应用代码**

- `nexus_backend/app/accounting/invariants.py`
- `nexus_backend/app/closing/queries.py`
- `nexus_backend/app/monthly/briefing.py`
- `nexus_backend/app/ingestion/service.py`
- `nexus_backend/app/ingestion/router.py`
- `nexus_backend/app/ingestion/middleware.py`（新增）
- `nexus_backend/app/jobs/restore_hold.py`（新增）
- `nexus_backend/app/jobs/worker.py`
- `nexus_backend/app/unified_worker.py`
- `nexus_backend/app/accounting/service.py`
- `nexus_backend/app/main.py`
- `scripts/backup_accounting.py`
- `scripts/run_accounting_assistant.py`
- `.env.accounting.example`
- `deploy/.env.api.example`
- `docs/BACKUP_RECOVERY.md`

**迁移**

- `nexus_backend/migrations/postgres/102_draft_review_boundary.sql`（新增）
- `nexus_backend/migrations/postgres/103_ingestion_receive_boundary.sql`（新增）
- `nexus_backend/migrations/sqlite/078_ingestion_receive_boundary.sql`（新增）

**测试**

- `nexus_backend/tests/test_monthly_briefing.py`
- `nexus_backend/tests/test_stage10_backup_recovery.py`
- `nexus_backend/tests/test_accounting.py`
- `nexus_backend/tests/test_ingestion_service.py`
- `nexus_backend/tests/test_catalog_postgres.py`
- `nexus_backend/tests/test_review_boundary_postgres.py`（新增）
- `nexus_backend/tests/test_restore_hold.py`（新增）

---

## 10. R1–R4 补全记录（2026-10-18 第二轮）

基线 `cc39a916`，按补充执行词补全。**过程说明**：执行期间外部进程自动创建了 5 个提交（`7191e33b…a9daee5b`），把本轮 B01 补全与 B02 一并提交；本代理未主动提交，也未回退。当前唯一未提交改动是恢复的两条 ingest HTTP 测试。

### R1 B01.1 证据

新增/补强：

- 净流出、重复来源隔离、**费用冲回负号**、**退款负号**、**缴税负号**、**跨企业 A/B 拒绝**。
- 父级/辅助重复计数：当前 `accounts` 无父子层级，不新增体系；费用排名按 `account_code` 分组，不对父级汇总。该项标记为“当前模型不适用”。

`test_monthly_briefing.py`：**8 passed**。

### R2 B01.2 清单与恢复

- `ORIGINAL_TABLES` 登记 8 类原件，并记录 `tables`、`scope`（`firm_id/company_id` 或 `firm_id/user_id`）、`object_version`、`sha256`、`size_bytes`。
- 清单增加 `object_version_supported=false`、`consistency_note`；明确当前存储适配器不暴露对象版本，不宣称原子快照。
- 新增注册表扫描测试：PostgreSQL 迁移中所有含 `storage_key` 的表必须登记；漏登即失败。
- `restore-verification.json` 增加 `outbound_disabled/worker_autostart/in_flight_jobs_hold`。
- 新增 `app/jobs/restore_hold.py`：设置 `ACCOUNTING_RESTORE_HOLD_FILE` 指向恢复核验文件时，队列/统一/助手 worker 拒绝启动。
- `BACKUP_RECOVERY.md` 增加 accounting 数据包 / 企业资料交付包 / 完整系统灾难恢复三者边界。

`test_stage10_backup_recovery.py` + `test_restore_hold.py`：**12 passed**。

未完成：真实对象存储恢复、恢复后跨企业访问、无模型/邮件调用仍标未验证；企业资料交付包未实现。

### R3 B01.3 审核边界

- `AccountingService` 审批审计增加 `review_policy_version=b01.review.2026-10-18`；不新增第二/第三复核人。
- 新增 HTTP 测试：企业员工不能复核；会计复核成功。
- 新增撤权顺序测试：撤销 `book_access` 后不能审核，状态不变。
- 新增拒绝后内容/证据保持测试：缺少证据的审批失败后，状态、版本、分录行不变。
- 既有申请人≠复核人、乐观版本、并发审批、关闭期间规则继续保留。

`test_accounting.py`：**45 passed**；`test_review_boundary_postgres.py`：**4 skipped**（本机无 PostgreSQL DSN）。

仍未验收：PostgreSQL 触发器、HTTP 与受限 PG 同组案例、撤权与审核真正并发、最后实质修改人字段（列 B02）。

### R4 B01.4 代码级封禁

- `main.py` 不再读取 `ACCOUNTING_INGESTION_ENABLED`，`IngestionService(enabled=False)` 为代码级默认；环境变量无法开启。
- 新增 HTTP 测试：设置该环境变量后，`app.state.ingestion.enabled` 仍为 false；旧 `/api/integrations/webhook/fake-token` 返回 404，响应/日志不含令牌，`ingestion_runs` 零新增。
- 新增 HTTP 202 测试：代码内临时启用后，`X-Ingestion-Token` + `X-Event-Id` 返回 202，且只表示 `received`。
- 旧路径不再携带令牌；新路径仅 header 凭据。

`test_ingestion_service.py`：**7 passed**。

未完成：真实网关/反向代理访问日志验证（本机 TestClient 只覆盖应用层）；完整消费/重放链仍未实现。

### R5 回归

- B01 定向合计：**72 passed, 4 skipped**。
- 相关域回归（home/catalog/business/delivery/firm）：**109 passed**。
- `ruff check`、`ruff format --check`（app/scripts）：通过。
- 全量 `nexus_backend/tests` 未完成，不得写“全量通过”。

### 当前状态

| 子项 | 状态 |
|---|---|
| B01.1 | 已修复并验证（父级/辅助为当前模型不适用） |
| B01.2 | 部分达标；清单/注册表/恢复守卫已验证，真实对象存储与跨企业恢复未验证 |
| B01.3 | 未验收；PostgreSQL 与真正并发未验证 |
| B01.4 | 已隔离；HTTP/代码级封禁已验证，网关日志与消费链未验证 |

**进入 B02 条件**：B01.3 的 PostgreSQL 验收、B01.2 的真实恢复演练至少有一份可复现记录；否则 B01 只能标“部分完成”。
