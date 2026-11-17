# FSC14 租赁待审版本到记账、导出与归档（部分完成）

日期：2026-11-17
候选：HEAD `767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`；source 2341 / `50398a5359705a455b225e1f10ba821bfc21fc861531b51b79a35ec153da34e9`；migration 463 / `f86c74070ad99be7616646fb713783fdc3901e2f3c42de0e3008215c97482494`。

## 状态

| 项目 | 状态 |
| --- | --- |
| 本地实现 | 进行中（版本围栏/导出/复核链已实现；外部权威回执、归档包未闭环） |
| SQLite | 通过（FSC14 13 passed） |
| PG16 | 通过（版本围栏 1 passed；lease 子集 3 passed） |
| PG17 | 通过（版本围栏 1 passed；lease 子集 3 passed） |
| 浏览器 | 通过（1280/390；租户前端链、独立复核、导出、改版拒绝） |
| 非空恢复 | 未做 FSC14 专项；FSC13/FSC02 恢复已有 |
| 外部真实验证 | 未授权：真实 T+ 回执、客户合同、真实用户 |

## 已实现

- `lease_draft_bindings`（sqlite 195 / postgres 280）：草稿生成时绑定合同 ID 与版本，收入/退款草稿均绑定；历史绑定不覆盖。
- `lease_guards.assert_lease_source_current`：审核、正式记账、导出三个边界统一校验合同 active、approved_version、当前版本与绑定版本一致。
- 旧草稿围栏：
  - 合同 v2 获批后，v1 未审草稿审核 409。
  - v1 已审草稿在 v2 获批后正式记账 409。
  - v1 已审草稿导出 409。
- PG 兼容修复：`leases.amend` 指纹改用显式七字段，避免 PG `datetime` 进入 `json.dumps`。
- PG 权限修复：前向 281 授权 `DELETE lease_plan_rows`；未改历史迁移。
- 前端缺陷修复：
  - `TaxFPanel.run` 重复执行动作导致创建/确认请求发两次、提示被第二次 409 覆盖；已删除重复调用。
  - 创建合同后乐观展示并后台刷新，避免慢查询让整个面板持续 busy。
- 浏览器覆盖：合同登记→业务/收款/发票确认→生成收入草稿→独立复核→导出 CSV→登记红冲→合同改版→旧草稿导出拒绝。

## 测试证据

- SQLite：`test_tax13_leases.py + test_posting_service.py` → **13 passed/32.24s**。
- PG17：
  - `test_stale_lease_draft_is_rejected_after_amendment_on_postgres` → 1 passed/1.34s，`tmp_test/ak05-pg-runs/88de63c4098c49e886300be18a29b71d`。
  - lease 子集（含红冲）→ 3 passed/2.14s，`tmp_test/ak05-pg-runs/c04dcdc0…`。
- PG16：
  - 同上 stale 用例 → 1 passed/1.24s，`tmp_test/ak05-pg-runs/8895ca350fe5459183b374989a640878`。
  - lease 子集 → 3 passed/2.16s，`tmp_test/ak05-pg-runs/3e5d6eb0…`。
- 浏览器：`npx playwright test e2e/tax-f.spec.ts` → **2 passed/1.5m**；截图 `tmp_test/tax-f-lease-review-1280.png`、`-390.png`、`tmp_test/tax-f-*.png`。
- 类型：`npm run typecheck` 通过；`ruff check` 通过（app 文件），`git diff --check` 通过。

## 未完成 / 外部待验

- FSC14 外部权威模式：未提供“外部办理准备→回执核对→归档包”服务与 UI。
- 归档：尚未把合同/计划/事实/草稿/凭证/导出清单/回执组合为冻结归档包并校验企业/账簿/期间/版本。
- 两企业、连续两期含跨年的真实浏览器链未做（当前浏览器用合成单企业单期）。
- PG 受限角色双会话竞争、撤权后旧回执、期间锁后版本变化未做 FSC14 专项。
- 非空恢复未包含 FSC14 归档/回执（FSC13/02 恢复已通过）。
- 真实 T+ 认证、真实客户合同、真实用户/设备未验证；不宣称真实业务闭环。
- 发票红冲不自动改内部收入，合同终止不删历史（沿用既有测试）；未将红冲与 FSC02 应收核销联动。

## 结论

FSC14 的“合同版本围栏 + 内部复核/导出”已通过本地 SQLite、PG16/17 和浏览器；但 FSC14 作为 P0 完整业务包仍未通过，不能纳入 FSC12 宣称完成。
