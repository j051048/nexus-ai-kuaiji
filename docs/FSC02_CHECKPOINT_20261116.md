# FSC02 检查点（2026-11-16）

## 状态

FSC01 已完成本地、PG16/17 与浏览器验收；FSC02 进行中，仅完成“规费分录辅助核算使用 canonical name，ID-only 视为失效”这一最小修复。

## 本轮修改

- `nexus_backend/app/tax/fee_guard.py`
  - `TRANSPORT_FIELDS` 的 counterparty/project 增加 `name`。
  - 审核/导出时辅助核算必须等于 `{counterparty|project: canonical_name}`；旧的实体 ID 快照视为不一致，阻止复核。
- `nexus_backend/app/tax/fees.py`
  - 规费草稿辅助值从运输主数据实体 ID 改为 canonical name。
- `nexus_backend/tests/test_tax06_fees.py`
  - 新增 canonical name 断言（张师傅/李师傅、京A1/京A2 的“核算主体”）。
  - 新增旧 ID-only 辅助值被 `fee_guard` 拒绝、独立复核失败的用例。
- 新增运输主数据停用/版本变化后 `fee_guard` 报告“运输主体或辅助核算映射已变化”、独立复核失败的用例。

## 验证

```bash
cd nexus_backend
python -m pytest tests/test_tax06_fees.py -q --disable-warnings
# 15 passed
```

- 覆盖：正向复核、重复/并发、来源升级、人工改稿、零额/负额/未知映射、canonical aux、legacy id 拒绝；**16 passed/26.51s**。
- 未跑：FSC02 的 PG16/17（当前隔离 PG 集群已停）；未重跑 P01。

## 候选

- HEAD `767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`
- source `2222 / 30f97d75125442d2ceb68dfbedaf8a4eae770b22586485b24fa22c96309dedbb`
- migration `394 / b5ea389468e7f86768607f8201507dd8606b75316f0870118ce6d870f93dd5cc`

## 未完成（FSC02）

- 驾驶员/车辆辅助映射有效期与版本在草稿生成、普通审核、批次审核、导出、下载、回执全链复核。
- 规费应收按驾驶员真实债权/行贡献建立核销对象，不能整批当作单一交易对方。
- 部分/多次/合并/多银行/跨期/代收/预收/退款/撤销专项。
- 策略绑定企业审核政策与来源，禁止客户端自报版本。
- 原件分页/验证下载、坏行阻断、逐行回执。
- FSC02 的 PG、浏览器、恢复验收与文档证据。

## 2026-11-16 原件/行可达性
- 来源文件列表每页 5 个、来源行每页 10 行，均显示总数和当前区间；不再截断到 5 个文件/12 行。
- 浏览器 `e2e/tax04-sources.spec.ts` 1280/390 **4 passed/45.9s**：6 个来源翻页、17 行翻页到 L17、排除并持久化。
- 原件下载已有 `download_original` 路由，按 SHA/大小校验，未匹配返回 409。

## 2026-11-16 驾驶员应收只读列表
- 后端 `GET /fee-receivables` 把已复核 adopted 规费行按驾驶员拆分，返回金额、车辆、来源行、settled=unsettled。
- 前端规费面板增加“驾驶员应收明细（只读）”，明确未接入银行到账核销，不表示已收款。
- 浏览器 1280/390 2 passed；截图 `tax06-fees-1280.png`、`-390.png`。
- 仍是读模型：尚未创建 settlement_items，也未实现银行到账分配/撤销/退款。

## 2026-11-16 规费辅助名称 PG 守卫
- 新增 postgres 248：`taxi_transport_draft_guard` 的辅助值期望改为 canonical name，旧 id 值或名称变化在数据库层也拒绝。
- PG17/16 `test_tax06_fees_postgres.py` 各 **4 passed/5.49s、5.68s**。
- 迁移编号：sqlite 163、postgres 247/248；候选 source 2225/`cb18d95b61f8567d1df426b0f7ae27ed7f5cc4e1bf976159d7939d9a4f917139`、migration 397/`bb4319266eae330cf4ac950f41912b9dd836888526cdafebdcccfd34d6539e38`。

## 2026-11-16 规费应收对象与 PG 守卫
- `fee_receivable_items` 在规费复核事务内落库；`batch_row_id` 唯一，状态 open/settled。
- PG 248 将运输辅助核算触发器期望值改为 canonical name。
- SQLite 16 passed/27.05s；PG17/16 各 4 passed/5.49s、5.68s。
- 候选 source 2225/`cb18d95b61f8567d1df426b0f7ae27ed7f5cc4e1bf976159d7939d9a4f917139`、migration 397/`bb4319266eae330cf4ac950f41912b9dd836888526cdafebdcccfd34d6539e38`。

## 银行到账分配（2026-11-16）

- 新增 sqlite 164 / postgres 249 `fee_receivable_allocations`：银行来源、应收对象、金额、银行草稿版本；同一幂等键异内容 409。
- 服务仅允许同一账套、收入方向、未审核银行草稿；核销金额同时受应收余额与银行到账余额约束；支持部分/多次分配，结清后拒绝继续分配。
- `GET /fee-bank-sources` 返回可分配银行流水及余额；前端“驾驶员应收与到账核销”可登记分配。
- 后端 `test_tax06_fees.py` 17 passed/35.92s（含部分 4000 + 补足 5000、超分配拒绝、2 条分配记录）。
- 浏览器 tax06 1280/390 2 passed/31.8s，实际登记到账并显示“已结清”。
- 候选 source 2227/`9aa76fb0…`、migration 399/`d06868e2…`。

## 到账核销撤销
- sqlite 165 / postgres 250 增加 reversed_at/by/reason；`reverse_allocation` 只写撤销字段，不删除分配历史；应收余额重算后恢复。
- 浏览器 tax06-fees 1280/390 通过（登记→撤销→记录“已撤销/银行退回”）。
- `test_tax06_fees.py` 17 passed，覆盖部分/补足、重复撤销 409、银行剩余可分配恢复。

## 审核政策版本绑定
- 规费批次生成时将 `ReviewPolicyService.policy_in_transaction(conn, book_id)["version"]` 写入 `policy_json.review_policy_version`；复核前重读当前版本，变化返回 409，必须重新生成。
- 复核草稿时把冻结版本传给 `_approve_in_transaction`，避免内部再次要求“刷新”。
- `test_tax06_fees.py::test_fee_batch_is_bound_to_review_policy_version` 通过：生成→配置政策 v1→旧批次 409→新批次复核通过。
- 全量 `test_tax06_fees.py` 18 passed/40.40s；`test_tax06_fees.py + test_tax04_sources.py` 34 passed。
- PG 未跑：当前无 `ACCOUNTING_TEST_POSTGRES_URL`，4 skipped。

## 退款（区别于撤销）
- 新增 sqlite 166 / postgres 251 `fee_receivable_refunds`：应收对象、支出方向银行来源、退款金额>0、银行草稿版本、原因 3–500 字、幂等键/请求指纹；PG RLS `company_visible`。
- `TaxFeeService.refund_receivable` 校验复核权限、支出方向、未审核草稿、开放期、退款≤已核销净额、退款≤银行付款余额；幂等同内容返回、异内容 409；原收款/分配记录不修改。
- `_net_collected` = 有效到账分配 − 退款；退款后按净额恢复 open/settled；`list_bank_sources(direction='expense')` 返回付款余额。
- 撤销到账核销前检查净额，退款未撤销时不允许把净额撤成负数（409）。
- `GET /fee-refunds`、`GET /fee-bank-sources?direction=expense`；前端“驾驶员应收与到账核销”增加“登记退款”、退款记录表。
- `test_tax06_fees.py` **19 passed/44.39s**：足额核销→退款 3000→open→重复/异内容/错方向/超额/退款后撤销被拒→银行剩余 6000→再收 3000→settled。
- 浏览器 tax06 1280/390 **2 passed/45.8s**：登记到账→退款→退款记录→撤销另一笔到账，全部通过。
- PG 退款用例已加入 `test_tax06_fees_postgres.py`（含 RLS 计数），但本机无隔离 DSN，**5 skipped**，不能算 PG 通过。
- 候选：source **2234 / `81c6abe6bd47da6a0c496d80a8cc70aa0874912a2d8634be1a021643bee31109`**；migration **403 / `529cdf2c517fe9951001cd8b3c7d395e3d7a23e415b3f67e60f7e8188377055f`**。
