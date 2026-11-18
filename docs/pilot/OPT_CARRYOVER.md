# OPT 遗留盘点（PLT01-A）

只读盘点 `docs/fsc_opt/OPT_STATUS.md`（1274 行，2026-11-18 工作区）与当前 `src/`。未改代码。
同一模块存在多节时，表中取最后一节，并在“重复/历史节”列注明。`OPT11` 在状态文件中没有记录。

## 模块状态

| 模块 | 最后记录状态 | 缺什么 | 缺口类型 | 重复/历史节 |
| --- | --- | --- | --- | --- |
| OPT01-A | 完成 | 无 | — | 无 |
| OPT01-B | 已被修复无需改动 | 无；162 已由 173 前向修正 | — | 无 |
| OPT01-C | 部分完成 | `-k tax` 仍 6 个非取整失败（Starlette 4 个已在 .venv 对齐后消失）；未跑 PG 税额 | 代码/测试/PG | 2026-11-17 两段复核，仍部分 |
| OPT02-A | 完成 | CI/e2e/P01 未本地完整重放，仅记录 | 人工/CI | 无 |
| OPT02-B | 完成 | 无 | — | 无 |
| OPT02-C | 部分完成 | backend 全量受旧权限契约测试阻断；未取得全绿 | 代码/测试 | 两段“对齐 CI 依赖复核”，仍部分 |
| OPT02-D | 完成 | 无 | — | 无 |
| OPT02-E | 部分完成（本地可执行部分完成） | 推送后四工作流、分支保护、P01 runner 未验 | 人工/CI | 无 |
| OPT03-A | 完成 | 无 | — | 无 |
| OPT03-B | 完成 | 无 | — | 无 |
| OPT03-C | 完成 | 月包/模板审计故障注入未单独做；报表无独立下载路由 | 测试/范围 | 无 |
| OPT04-A | 完成 | 5 张全局表使用 `USING(true)` 保留既有语义；非租户隔离 | 设计边界 | 无 |
| OPT04-B | 完成 | 无 | — | **重复两节**（行 431、478，内容相同） |
| OPT04-C | 完成 | `tax/fees.generate` 保留全局锁 | 边界 | 无 |
| OPT04-D | 完成 | 应用层同源并发 409 未用完整草稿链复现 | 测试 | 无 |
| OPT05-A | 完成 | 真实 compose batch 容器端到端未验 | 环境 | 无 |
| OPT05-B | 完成 | 真实双 worker 争抢、云端 DSN/存储未验 | 环境 | 无 |
| OPT05-C | 完成 | 真实 compose 消费旧入口批次至终态未验 | 环境 | 无 |
| OPT05-D | 完成 | 真实 compose scheduler、双进程争抢未验 | 环境 | 无 |
| OPT05-E | 部分完成 | PG16/17 同链幂等发布证据缺失；真实响应丢失未验 | PG/测试 | 无 |
| OPT06-A | 完成 | 真实 MFA 供应商/设备、PG 服务层 403 重试未验 | 环境/测试 | 无 |
| OPT06-B | 完成 | manager 合法 draft 链的 PG 复核分支未单独复现 | PG | 无 |
| OPT06-C | 完成 | 前端申请/批准界面、真实请假流程未做 | 代码/人工 | 无 |
| OPT06-D | 部分完成（`###` 节） | `-k "close or posting"` 仍 5 个旧失败；未跑全量 backend/浏览器 | 测试/环境 | 无正式 `##` 状态标题 |
| OPT07-A | 只读完成 | 仅盘点；配置化分散在 D–F | 盘点 | 无 |
| OPT07-B | 部分完成 | 模板配置 UI、企业模板、逐行公式数据化、跨模板回放未做 | 代码/测试 | 无 |
| OPT07-C | 部分完成 | 现金流量表、逐行用例、企业账套 PG、模板 UI、种子接开户未做 | 代码/测试/PG | 无 |
| OPT07-D | 完成 | 带既有 allocation 的 203 升级路径未验 | PG | 多段历史：行 1089/1104 被行 1114/1133 取代 |
| OPT07-E | 部分完成 | 多税率按票/业务归集、附加税分项、企业所得税预缴、印花税、前端、两企业负向未做 | 代码/测试/前端 | 无 |
| OPT07-F | 后端本地通过，前端待做（整体部分完成） | 客商别名前端、两企业/并发 PG 负向、真实导入消费者未做 | 前端/测试 | 无 |
| OPT08 | 本地验收通过；跨浏览器/人工 IME 未验 | Firefox/WebKit、真实 IME、屏幕阅读器未验 | 人工/跨浏览器 | A–E 合并为一节 |
| OPT09 | 本地验收通过；跨浏览器/顶栏切换未专项 | 跨浏览器、真实年度规模、顶栏切换迟到响应未验 | 人工/跨浏览器 | A–E 合并为一节 |
| OPT10 | 本地大部分通过 | PG16 产品 restore 受本机 PG17 客户端限制；多进程限流、跨浏览器未验 | 环境/代码 | A–C 合并为一节 |
| OPT11 | 未开始（状态文件无记录） | 整个模块无记录 | 未开始 | 无 |

## OPT08 A–E 代码核对

| 子项 | 代码是否存在 | 核对 |
| --- | --- | --- |
| A 金额精确化 | 是 | `src/money.ts` 存在；`parseYuanToCents`/`formatCents`/`sumCents`/`centsToApiString`/`balanceDirection`；`src/csv.ts`、`src/formal-books-types.ts` 委托 |
| B MoneyCell | 是 | `src/MoneyCell.tsx` 存在；`FormalBooksWorkbench` 方向列与期末绝对值、`JournalEntryGrid` 合计、`InlineReviewDrawer` 差额已接入 |
| C AccountPicker | 是 | `src/AccountPicker.tsx`、`src/account-picker.ts` 存在；末级识别、过滤、父组禁用、方向键/Enter/Escape、fixed 菜单 |
| D 键盘导航 | 是 | 已核对 `src/VoucherReviewer.tsx:364`、`src/InlineReviewDrawer.tsx:868` 接线 `onAddLine`；`src/JournalEntryGrid.tsx:205-213` 处理 Alt+Enter/Ctrl+Enter；删除后回焦在 `removeLine` |
| E 行摘要核实 | 是 | `e2e/stage7.spec.ts` 3 passed；`e2e/tax05-large-voucher.spec.ts` 6 passed，516 行本页/全量合计 |

结论：OPT08 的 A–E 不是“只在文档里”；本地代码与浏览器用例存在。未验的只有 Firefox/WebKit、真实输入法/屏幕阅读器和人工可访问性。

## 仍需处理的“部分完成”优先级

1. **PLT01-B 可本地完成**：OPT01-C、OPT05-E、OPT07-E、OPT07-F 前端、OPT08 守卫；PLT01 说明明确列出的五项。
2. **PLT01-B 需先判定**：OPT02-C 的全量 backend 受既有权限契约测试阻断；不能通过删断言/放宽 reviewer 规则解决。
3. **PLT01-C 收尾**：OPT09/OPT10 尚未完成的专项；OPT11 未开始。
4. **只能人工/外部**：OPT02-E CI/runner、OPT05 compose/云端、OPT06 真实 MFA、OPT08/OPT09/OPT10 跨浏览器、PG16 产品 restore 等，汇总进 PLT01-D。
