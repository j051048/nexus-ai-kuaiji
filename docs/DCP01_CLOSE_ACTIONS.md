# DCP01 · 月结阻断直达办理（验收报告）

日期：2026-10-31　范围：DCP01 工作包　状态：**实现完成，浏览器证据已取得；另发现 1 项 P0 回归并已修复其夹具**

## 1. 目标与本包边界

月结预检返回的每个阻断项，此前只有「处理岗位 / 下一步」两行文字，没有任何办理入口；用户必须自己猜去哪个页面、带哪个账套和月份。DCP01 要求：

- 由**服务端**给出目的地（`action_target`），前端只做白名单路由，**未知目标绝不猜测**；
- 目的地绑定 `company / book / period`，跨账套或跨月份一律拒绝执行；
- 无权限的人只看到「负责岗位」，不出现写入按钮；
- 子账控制科目、银行账户映射、覆盖证据三类办理就地展开（复用月结折叠面板，不新增大导航）；
- 返回后保留账套、月份与展开状态；切换企业或撤权时清理旧明细。

本包**不改访问政策**。政策由 `0c06a38c` 引入，本包只在其约束下接线。

## 2. 服务端契约（已存在，本包未新增写口径）

`nexus_backend/app/b04_close/service.py:128-169` 的 `guidance` 为 7 个检查码分别给出 `owner / next_action / target`，并在每条检查上写入：

```python
check["action_target"] = {"kind": target, "book_id": book["id"], "period": period}
```

`kind` 取值：`periodic`、`review`、`manual_vouchers`、`subledger`、`bank_reconciliation`、`formal_books`、`reports`。源码注释写明：「Guidance describes a responsible role, never confers authorization」。因此前端拿到的只是**建议去向**，能不能办仍由每个写接口自己鉴权。

## 3. 前端交付

| 文件 | 作用 |
| --- | --- |
| `src/close-action-target.ts` | 7 项目的地白名单 + 范围校验 + 权限门控，纯函数、可单测 |
| `src/CloseCheckAction.tsx` | 渲染三种模式：`inline`（就地展开面板）、`link`（站内跳转）、`manual`（人工说明 + 负责岗位） |
| `src/SubledgerSetupPanel.tsx` | 子账/银行两类配置与证据面板（控制科目配置、本月不适用声明、银行账户映射、覆盖登记） |
| `src/FormalClosePanel.tsx` | 阻断项插入 `CloseCheckAction`；挂载面板；`revoke` 时清空面板；`id="formal-close"` 锚点 |
| `src/formal-close-types.ts` | `CloseCheck.action_target` 类型 + `parseActionTarget` 防御式解析（kind ≤ 40、book_id ≤ 100、period 必须 `^20\d{2}-(0[1-9]|1[0-2])$`） |

解析规则（`resolveCloseAction`）：

1. 无 `action_target`（历史预检）→ 人工说明「请按下一步处理后重新预检」，不给链接；
2. `kind` 不在白名单 → 「不在支持范围内；系统不会猜测页面」；
3. 缺 `book_id` / `period`，或与当前页不一致 → 「属于其他账套或月份；本页不会跨账套操作」；
4. `subledger` / `bank_reconciliation` 需要当前账套的 `canReview`，否则只给「请联系有复核权限的同事」；
5. 其余按类型映射到 `#formal-close-report`、`/workspace/formal-books?...#formal-close`、`/workspace/review?review_book=...`。

面板的两个易错点已经处理：**加载中不渲染写入表单**（否则用户可能在未知版本上提交），写成功后静默重读而不闪烁。

## 4. 后端新增（只读）

`GET /api/subledger/bank-batches?book_id=` —— 列「已确认的银行导入批次」，供覆盖登记选择原件/预览/SHA-256。实现 `SubledgerService.list_bank_batches`：`bank_import_confirmations` → `previews` → `files`，按账套限 50 条，返回 `preview_id / file_id / sha256 / filename / confirmed_at`。

覆盖登记本身仍走既有 `declare_coverage`：强制携带 `source_file_id / preview_id / sha256` 且必须命中已确认批次（`nexus_backend/app/subledger/bank_evidence.py:34-45`），所以「随便填个哈希」无法造出覆盖证据。

## 5. 证据

| 项 | 结果 |
| --- | --- |
| `npx vitest run`（4 文件） | **31 passed**：resolver 8、action row 5、setup panel 3、close types 15 |
| `npx tsc --noEmit` | 无输出 |
| `./.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_b04_api.py` | **2 passed**（含新增 `test_bank_batches_lists_only_confirmed_batches_for_the_authorized_book`） |
| `ruff check` | 通过 |
| `npx playwright test e2e/dcp01-close-actions.spec.ts` | **2 passed（2.9 分钟）**，桌面 1440×900 与移动 390×844 |
| `npx playwright test e2e/formal-books-monthly-package.spec.ts` | **11 passed（4.2 分钟）**，夹具修复后恢复全绿 |

端到端用例走的是真实接口：建公司/账套 → 期初包 frozen → opening-bindings → 一张真实手工凭证（制单/独立复核/记账三步）→ 报表编制与独立复核 → 预检失败。断言点：

- `subledger_control` 与 `bank_reconciliation` 均为 `blocked`，且带 `action_target`（`kind/book_id/period` 与当前范围一致）；
- 页面上同时出现「去配置控制科目」「去补银行账户与覆盖」两个按钮；
- 点开后分别出现「控制科目配置」「银行账户与覆盖证据」两块面板，面板明确写着「配置本身不会通过检查」；
- 页面无横向溢出（`scrollWidth <= innerWidth + 1`）；
- 重新进入同一 URL 仍停在 2026-10 账套。

截图由 `testInfo.outputPath` 输出，位于 `test-results/dcp01-close-actions-*/close-action-{desktop,mobile}.png`。

> 夹具说明：零业务月份下银行与子账检查会「真空通过」，所以夹具必须先记一张 100.00 的银行付款凭证，才有真实发生额可查。这不是绕过，而是让检查有东西可查。

## 6. P0 发现：新访问策略打断了既有本地链路（已定位，夹具侧已修）

**现象**：`e2e/formal-books-monthly-package.spec.ts` 在策略提交后 **11 个用例全部失败**，错误一致：

```
Error: {"detail":"正式复核仅可授予事务所复核人员；客户财务请使用经办或只读"}
```

**根因**（`nexus_backend/app/accounting/directory.py:380-381`）：账套 `reviewer` 只授予 `firm_role ∈ FIRM_REVIEWERS` 的成员；客户财务（`company_role=finance`）不再能拿正式复核权。而本地种子只有三个身份：事务所 owner（`local-accountant`）、客户财务（`local-business-finance`）、客户员工（`local-business-employee`）——**没有第二个事务所复核人**。偏偏正式报表与月结预检都要求 `review` 能力（`reports/service.py:29-36`、`b04_close/service.py:33-34`），且编制人与复核人必须分离（`reports/service.py:411-412`、`b04_close/service.py:262-263`）。于是本地演示与既有 e2e 无法再走通「编报 → 复核」。

**为什么不在本包改政策**：让客户财务兼任事务所复核，正是审计里点名的「一人分饰两角」反模式；生产环境靠真实邀请第二个事务所人员解决，本地是夹具缺口。

**本包做的**：只在 e2e 夹具里先把演示财务提升为事务所复核人（`PUT /api/firm/members`），再加注释说明这是本地夹具权宜，不进入产品路径。`e2e/formal-books-monthly-package.spec.ts:173-188` 与 `e2e/dcp01-close-actions.spec.ts` 的夹具均已处理。

**修复被夹具自己的端到端断言抓出第二个问题**：提升后 `local-business-finance` 同时是「事务所复核人」和「客户联系人」，客户签收段一进页面就带出了事务所侧统计区（用例断言 `事务所账务概览` 必须为 0，实测为 1）。这说明**夹具身份必须是单一职责**。最终做法：签收段开始前先撤销事务所成员身份，再以「企业财务 + 账套只读」重新授权，让客户身份只剩客户侧能力；两个视口重新跑通。

**真正待办的 P1**：本地种子补一个「事务所复核人」身份，并把上述夹具改回真实双人；否则本地永远只能靠提升客户财务来演示复核。

## 7. 已知缺口（不在本包范围，已记录）

1. 子账控制科目、银行账户映射等**配置写入没有锁期校验**（只有覆盖写入经 `bank_evidence.py:13-21` 校验 `period_status` 与 `internal_period_locks`）。锁期后仍可能改配置，属于 P1 风险。
2. 面板没有「只读重新检查这一项」：当前只能回月结面板重新预检，粒度过粗。
3. 客户财务（编辑权）看不到子账配置按钮，这是**设计如此**：配置改变勾稽口径，属复核职责。
4. 阻断项的行动入口依赖预检时写入的 `action_target`；历史预检记录没有该字段，只能人工说明（符合「不猜」原则）。
