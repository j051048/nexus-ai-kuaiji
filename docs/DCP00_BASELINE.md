# DCP00 基线：候选、修改归属与可复跑证据

- 执行日期：2026-10-31
- 性质：只读核对 + 最小回归。未修改任何产品代码、未提交、未推送、未接触线上。

## 1. 候选与归属

| 项 | 值 |
|---|---|
| HEAD | `2e811d9ebd8020980c0d6475782790fc10440ad6` |
| origin/main | `2e811d9e…`（与 HEAD 一致，已推送） |
| 分支 | `main` |
| 工作区 | 2 处修改 + 6 处未跟踪 |

工作区全部属于**受保护的 191 历史兼容性修复**（他人基线），本次未改动、未提交：

| 路径 | SHA-256 |
|---|---|
| `.github/workflows/accounting.yml` | `db3d2fbcd411af9c…` |
| `scripts/migrate_accounting.py` | `813d473403198143…` |
| `nexus_backend/migrations/postgres/191_historical_compatibility_labels.sql` | `3f96bb697b7deb88…` |
| `nexus_backend/tests/test_historical_compatibility_postgres.py` | `b75a23bb0d359cbc…` |
| `nexus_backend/tests/test_historical_compatibility_repair.py` | `0032553f2b8ab433…` |
| `docs/HISTORICAL_COMPATIBILITY_REPAIR_20261031.md` | `826aca103e27cdf4…` |
| `docs/HISTORICAL_COMPATIBILITY_REPAIR_20261031.json` | `a029618ca28be4f0…` |

另有 `docs/DEEPSEEK_NEXT_EXECUTION_PLAYBOOK_20261031.md` 为本轮手册副本，非产品代码。

**归属结论**：上述 7 个受保护路径的归属为 `unknown`（并行执行 AI 的在途工作，内容与手册基线清单逐条吻合，未凭风格猜测作者）。未发现与本轮工作重叠的第三方改动。

## 2. 能力表（已有已接线 / 已有未接线 / 需新增 / 未验证）

| 能力 | 状态 | 证据 |
|---|---|---|
| 银行对账 API | 已有已接线 | `app/subledger/router.py:177`、`app/subledger/service.py:987-991` |
| 控制科目适用性/配置 | 已有已接线 | `app/subledger/service.py:121-129`（applicability）、`:228`（accounts） |
| 银行/子账前端办理页 | 已有未接线 | 存在 `src/BankDuplicatePanel.tsx`；覆盖原件提交与独立核验入口未从月结侧可达（见 §5 发现 1） |
| 正式月结引擎 | 已有已接线 | `app/b04_close/service.py:81-127` 七项检查；`:128-169` 每项带 `owner_role`/`next_action`/`action_target` |
| 月结阻断页 | **已有未接线** | `src/FormalClosePanel.tsx:607-608` 仅渲染 `owner_role`/`next_action` 文本，**不消费 `action_target`**（无跳转） |
| 账簿/试算平衡 | 已有已接线 | `b04_close` 的 `trial_balance` 项；`app/ledgers`、`app/accounting` |
| 客户简报 | **已有但口径错误** | `app/monthly/briefing.py:47,79` 直接 `JOIN draft_lines / drafts`，不是已发布正式报表（DCP03 前置成立） |
| 月度包 | 已有已接线 | `app/delivery/router.py:57-108` 生成/打开/下载/签收；`app/delivery/monthly_package.py` |
| 客户签收 | 已有已接线 | `delivery/router.py:98` acknowledge；`monthly_delivery_opens` 留痕 |
| 通知通道 | 仅有替身 | `app/delivery/followups.py:53` `DisabledChannel`、`:62` `LocalSinkChannel` |
| 批量开期 | 已有已接线 | `app/batch_ops/router.py:12,20,28,37,45`（preview/create/list/detail/retry，共 5 个） |
| 批量导出/交付/取消/跳过 | **需新增** | 上述 5 个端点之外不存在 |
| 人工接手 | 已有已接线 | `app/execution/handoff.py:153` preview、`:193` create（kind 白名单） |
| 税务/交付/批量的接手入口 | 未验证 | 需在 DCP05 实证 |
| 权限（范围/部门/复核/期限） | 已有已接线 | `app/auth/access_policy.py` + 迁移 181–190 |
| 助手 | 单轮、无工具 | `app/assistant/provider.py` 无 `tools` 字段（grep 0 命中） |

## 3. 迁移与门槛

- PG 迁移文件 **179 个**，最大版本 `191`；SQLite **120 个**，最大 `120`。数量按文件计，不按最大号推断。
- `191_historical_compatibility_labels.sql` 标记 breaking，属受保护未提交工作，本地已验收、未上线。
- 现有 gate 脚本可直接复用：`scripts/c01_postgres_gate.py`、`scripts/access_policy_postgres_gate.py`、`scripts/ak05_postgres_gate.py`、`scripts/assistant_delivery_postgres_gate.py`、`scripts/release_candidate.py`。

## 4. 环境

| 项 | 值 |
|---|---|
| WSL 发行版 | `Ubuntu-22.04`（默认；手册写的 `Ubuntu` 不存在） |
| PG16 | 集群 `p02pg16`，端口 `25531`，online |
| PG17 | 集群 `p02pg17`，端口 `25532`，online |
| 监听 | `listen_addresses='*'`，Windows 侧 `Test-NetConnection 127.0.0.1:25531` = True |
| 测试运行器 | `tmp_test/run_p02_pg16.sh`（读 `tmp_test/p02-pg.local.json` 取受限角色 URL） |

**已知环境坑**：WSL 冷启动后首次连接可能报 `psycopg.errors.ConnectionTimeout`，等待转发就绪后重跑即通。首次失败样本保留在 `tmp_test/dcp00/run-1/`，不作为产品红测。

## 5. 基线运行证据（同一候选）

| 门槛 | 命令 | 结果 | 证据 |
|---|---|---|---|
| PG16 真实链路 | `bash tmp_test/run_p02_pg16.sh nexus_backend/tests/test_p02_business_flow_postgres.py` | **1 passed / 9.56s** | `tmp_test/dcp00/run-1/p02-chain.xml` |
| SQLite 回归子集 | `pytest test_p0_close_dependencies.py test_p02_close_diagnostics.py test_legacy_review_recheck.py` | **54 passed / 145.08s** | `tmp_test/dcp00/run-1/sqlite-subset.xml` |

首次 PG 尝试（131.82s）失败于 `ConnectionTimeout`，为环境未就绪；重跑通过。两次记录的候选相同（工作区未变）。

### 本次核对确认的两处硬缺口

1. **月结阻断无法直达办理**：`b04_close/service.py:168` 已产出 `action_target={"kind","book_id","period"}`，但 `FormalClosePanel.tsx:607-608` 只显示岗位与文案，整仓 `src/**/*.tsx` 对 `action_target` **零消费**——DCP01 的 A1 红测成立。
2. **客户简报口径错位**：`monthly/briefing.py:47,79` 从 `draft_lines` 取数，与"已发布冻结报表"脱节——DCP03 的前置判断成立。

## 6. 后续包入口与重叠

| 包 | 主入口文件 | 重叠风险 |
|---|---|---|
| DCP01 | `src/FormalClosePanel.tsx`、`app/b04_close/*`、`app/subledger/*` | `b04_close/service.py` 与 DCP05 共用 |
| DCP02 | `src/FirmOnboarding*`/`AdminOnboarding.tsx`、`app/onboarding/*` | 与 DCP01 共用账套/期间上下文 |
| DCP03 | `app/monthly/briefing.py`、`src/MonthlyBriefing*` | 与 DCP06 共用交付包投影 |
| DCP04 | `app/batch_ops/*`、批次详情页 | 与 DCP06 共用 batch_ops |
| DCP05 | `app/execution/*`、`app/cases/*`、`app/workspace/*` | 与 DCP01/04/08 都可能改 execution |
| DCP06 | `app/batch_ops/*`、`app/delivery/router.py`（exports） | 与 DCP04、DCP07 共用 delivery |
| DCP07 | `app/delivery/followups.py`、`notification_dispatch.py` | 与 DCP06 共用 delivery |
| DCP08 | `app/tax/*`、`src/TaxWorkpaperPanel.tsx` | 与 DCP05 共用接手 |
| DCP09 | `app/workspace/*`、`app/assistant/*` | 与 DCP11 共用 assistant |
| DCP11 | `app/assistant/*`、`app/tools/*` | 与 DCP09 共用 |

约束：DCP01/03/04 不得同时改 `app/delivery/router.py`；DCP04 与 DCP06 改 `batch_ops` 需串行。

## 7. 结论

- 候选可复跑、指纹明确、受保护改动未被触碰。
- 已有能力清单与手册描述一致，未发现"整条链路不存在"的情况；缺口集中在**接线**（action_target、简报取数、批量动作）与**新增**（批量导出/取消、通知适配器、取证循环）。
- **允许进入 DCP01**；DCP04/DCP09 亦可提前独立开工（无 02/03 依赖）。
