# P04 异常与人工接手验收报告

- 日期：2026-10-29
- HEAD：`8a21224d test: add recovery inventory coverage and baseline docs`
- 结论：**阻断**。P01 报告缺失、P02"部分完成"、P03"阻断"，最终集成验收要求"基于 P03 通过后的候选，包含批量失败项"，条件不满足。按边界"P03 尚在修改共享文件时只做只读盘点或互不冲突的测试准备"，本轮**零产品代码改动**，交付接线盘点、缺口与验收设计。

## 1. 前置核验

| 前置 | 要求 | 实际 | 判定 |
| --- | --- | --- | --- |
| P01 | `docs/P01_RECOVERY_ACCEPTANCE.md` | 文件不存在，仅 `P01_RECOVERY_COVERAGE_20261029.md` | 未通过 |
| P02 | 业务闭环报告 | 存在，结论"部分完成"（月结 `subledger_control`、`bank_reconciliation` 阻断） | 未通过 |
| P03 | 批量验收通过，含失败项 | 存在，结论"阻断"（D1 全局锁、D3 无批量导出/交付、D5 批量项不进执行记录） | 未通过 |
| 共享文件冲突 | P03 是否在改共享文件 | 否：P03 仅改 `tests/test_batch_review_postgres.py`，产品代码零改动，可与本包并行 | 通过 |

## 2. 修改归属

本轮只读：未改 `app/**`、`src/**`、`migrations/**`、`tests/**`。工作区沿用 P02/P03 产物：

- `M nexus_backend/tests/test_batch_review_postgres.py`（P03 用例建模修复）
- `?? docs/P02_BUSINESS_FLOW_ACCEPTANCE.md`、`docs/P03_BATCH_ACCEPTANCE.md`、`docs/P04_HANDOFF_ACCEPTANCE.md`、`docs/P04_EXCEPTION_HANDOFF_ACCEPTANCE.md`、`tests/test_p02_business_flow_postgres.py`

## 3. 已有接线（核实到代码，不新建 trace/case/todo）

| 来源对象 | 执行记录 | 异常/待办 | 负责人 | 操作入口 | 处理结果 |
| --- | --- | --- | --- | --- | --- |
| 原件/来源 `sources` | 无独立记录（只有草稿侧） | 补件 `cases(kind='supplement')` | `assignee_id` / `recipient_id`（`cases/service.py:72,387`） | `/api/cases/{id}`、`/respond` | 补件提交 → 重新核验 |
| OCR 任务 `ocr_jobs` | `GET /api/executions/ocr/{run_id}`（`execution/router.py:22`） | 失败分类 `CATEGORIES`（`records.py:27`） | `workspace` 待办标注"账套经办人"（`workspace/service.py:645-668`） | 待办 → 执行记录 | 人工接手 → 建 case（`handoff.py:31`） |
| 分录建议 `proposal_jobs` | `GET /api/executions/journal/{run_id}` | `bucket='action'`（`workspace/service.py:1027-1033`） | 同上 | 待办 → 草稿 | 人工改稿 / 转 case |
| 对话 `assistant_turns` | `GET /api/executions/chat/{run_id}` | 事实校验 `FACT_CODES`（`records.py:15`） | 提问人 | 助手抽屉 | 只读解释，不授权 |
| 员工事项 `business_events` | 无 | 退回/待审 | 当前阶段审批人（`business/service.py:741,772`） | 事项详情 | 审批/退回/交接 |
| 税务底稿 `tax_workpapers` | **无** | `status=draft/reviewed` | 无显式负责人 | `/api/tax/workpapers` | 复核（制单人≠复核人） |
| 交付包 `monthly_delivery_packages` | **无** | 待发送/待签收（N03 状态机） | `contact_user_id`（本版） | `/monthly-package/{id}` | 打开/签收（非创建人） |
| 批量项 `batch_operation_items` | **无**（kind 仅 4 类） | `error_code/error_detail` | 批次创建人 | `/api/batch-ops/{id}` | 重试（attempt 被清零） |
| 运维告警 `operations.alerts` | — | 告警 | 运维角色 | `/operations/...` | acknowledge |

## 4. 缺口（P04 要补的最小部分）

| 编号 | 缺口 | 证据 | 对应工作令 |
| --- | --- | --- | --- |
| E1 | 执行记录没有 `owner`/`next_action`/期限字段，前端靠 `src/review-eligibility.ts` 从 issues 推导 | `records.py:186,207` | P04-B |
| E2 | 批量失败项不可达执行记录 | `execution/router.py:23` kind 白名单 | P04-A、P03-D |
| E3 | 税务底稿、交付包无执行记录与人工接手入口 | 无 kind、无 handoff | P04-A |
| E4 | 交付/签收错误只返回文案，不带 `package_id/version/manifest_sha256` | `app/delivery/monthly_models.py` 的 409 文案 | P04-B |
| E5 | 三处分诊各自为政：`cases`（补件/异常）、`workspace`（action/waiting）、`operations`（运维） | 第 3 节表 | P04-A |
| E6 | `unknown` 外发没有"调查/核对"动作契约，只有状态 | N03 `sending/unknown` | P04-C/T04 |
| E7 | 指标口径分散：`operations/efficiency.py` 的 `credited_seconds` 与"节省时间"类叙述容易混用 | `app/operations/efficiency.py` | P04-D/T06 |

## 5. 角色 × 异常 × 动作矩阵（设计，未执行）

| 异常 | 员工 | 部门审批 | 财务 | 制单会计 | 复核人 | 客户联系人 | 事务所负责人 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| OCR 失败 | 看不到技术细节，只见"资料待补充" | 可见待办 | 可见分类+重试 | 可见重试 | 只读 | 不可见 | 可见汇总 |
| 缺件 | 提交补件 | 催办 | 核验 | — | — | 提交补件 | 指派 |
| 复核阻断 | 不可见 | 不可见 | 可见原因 | 改稿 | 可见+处置 | 不可见 | 指派 |
| 关期/来源过期 | 提示重新提交 | 提示 | 处置 | 处置 | 只读 | 不可见 | 汇总 |
| 交付哈希不符 | 不可见 | 不可见 | 可见 | — | 可见 | 只见"版本需重新获取" | 处置 |
| unknown 外发 | 不可见 | 不可见 | 调查/核对 | — | 只读 | 不可见 | 处置 |

## 6. 指标定义（P04-D）

- 分母固定为"当期该企业已登记的业务对象数"，失败/未知/缺失**单列**，不从分母消失。
- 时间只用系统记录（`created_at/finished_at`、attempt 时间），**不生成"节省 X 分钟"**；如需估算，必须标注估算口径与样本量。
- 人工修正入样例前须满足：已审核、非撤销、非隔离、非 holdout、非同一来源泄漏（沿用 `app/learning` 现有约束）。

## 7. 未执行与原因

| 项目 | 原因 |
| --- | --- |
| T01–T06 失败测试 | 按边界只做盘点；且 E1–E7 未修，写出的红测会长期挂红 |
| 真实 API / 浏览器（桌面、390px） | P03 未通过，无批量失败项可验证；P02 亦无浏览器基线 |
| PG16/17 专项 | 本包无持久化改动，复跑不构成本轮候选证据 |
| 指标样本 | 无真实数据，不编造 |

## 8. 结论与下一阶段条件

- **阻断**，不宣布 P04 完成。
- 进入条件（按依赖顺序）：
  1. P02 转"通过"（先修 F1：`b04` 预检 `subledger_control.detail` 结构化，这正是"卡在哪里"的最小切口）；
  2. P03 转"通过"（D1 按企业取锁、D3 批量导出/交付、D5 批量项接执行记录）；
  3. 再补 E1（owner/next_action 契约）与 E4（交付错误带版本），P04 才能在真实候选上跑 T01–T06。

本轮未提交、未推送、未部署、未接真实模型或通知。
