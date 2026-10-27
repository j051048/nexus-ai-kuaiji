# Q-R02 财务正确性（本地候选，未提交）

候选：`tmp_test/qr01-integration`（worktree）  
HEAD：`3fc1ab6e11761ba36490f1858b85fcff356441cc`  
范围：只改 `nexus_backend/app/posting/{service,presentation}.py`、`nexus_backend/app/opening/service.py`，新增一个测试文件；不改迁移、不改前端。

## 一、已确认缺陷与修法

| # | 缺陷 | 位置 | 修法 |
|---|---|---|---|
| ① | 余额用科目 `current_version`，并把合计只加在叶子行上：新增未来月份子科目后，父科目自身发生额仍在行里、却从总计消失，`complete` 不受影响 | `app/posting/service.py` `_accounts` / `_balance_rows` | 新增按月解析的 `_accounts(conn, ledger_id, period)`（`effective_from<=期间` 且 `effective_to` 空或覆盖，取最高版本）+ `_accounts_for_periods`；合计改为按账簿自身 `own` 求和，与树形无关 |
| ② | 范围内没有任何已绑定期初时 `next(status=="ok")` 抛 `StopIteration` → HTTP 500 | `_range_scope`、两个 range 函数 | 无 ok 期间改为受控结果：`complete=false`、`blockers=[opening_missing]`、`rows=[]`，scope 取首个可读月份 |
| ③ | 期初/发生额只按 `account_id` 汇总，辅助对象身份变化但合计不变时发现不了；也未要求银行/子账桥接证据 | `_range_coverage` | 分组键改为 `(account_id, aux_json 规范化)`；上期存在 `bank_accounts`/`subledger_items` 而本期无已完成 `subledger_period_bridges` 时增加 `continuity_unverified` |
| ④ | 导出来源摘要只有金额/状态，科目改名或版本变化后旧 CSV 仍返回、指纹不变 | `app/posting/presentation.py` `_source_digest` | 摘要增加科目版本快照、期初行数与合计、`name_snapshot` 去重；区间导出用 `_range_digest` 合并覆盖摘要与账簿摘要 |
| ⑤ | 新增子科目时父科目新版本沿用父的 `effective_from`，把「变汇总」追溯生效 | `app/opening/service.py` `_revise_parent_leaf` | 新版本 `effective_from = max(父原值, 子科目生效月)` |

## 二、先写失败测试（新增 `nexus_backend/tests/test_qr02_financial_correctness.py`，5 例）

1. `test_history_keeps_the_parent_own_amount_after_a_child_is_added`
2. `test_history_keeps_the_posted_name_after_a_rename`
3. `test_range_without_any_bound_opening_returns_incomplete_not_an_error`
4. `test_auxiliary_identity_change_is_an_opening_mismatch`
5. `test_export_refuses_a_file_rendered_before_a_rename`

**红→绿证据**

- 把 `_accounts` 与合计逻辑临时还原为旧实现（仅这两处）：用例 1、2 **失败**（断言信息分别为“下月新增子科目不得改写已过账月份的科目、金额或层级”“历史月份仍应显示原名称”），恢复修复后 **5 passed**（19.33s）。
- ② 的旧写法独立复现：对 `{"items":[{"status":"unbound"}]}` 执行 `next(...)` → `StopIteration`；修复后为受控结果（用例 3 覆盖 `opening_missing + period_open`、`scope.book_id` 指向未绑定月份、`rows == []`）。

## 三、回归与门禁

| 项 | 结果 |
|---|---|
| 定向后端（q03/q04/q05/posting_service/posting_api/formal_books/monthly_package/review_policy×2/review_responsibility/qr02） | **131 passed / 0 failed**（453.9s + 冲销适配后 5.3s） |
| Q-R02 自身 | 5 passed（19.3s） |
| PG16（`test_review_policy_postgres.py`，隔离库 `qr01_gate`） | **28 passed**（19.8s） |
| `ruff check nexus_backend/app nexus_backend/tests scripts` | All checks passed |
| `ruff format`（改动的 5 个文件，含测试） | 已格式化 |
| 正式 runner 核心集 `node scripts/run_e2e.mjs --mode dev` | **106 passed，PASS，0 gate issues**（4.3m） |
| 正式 runner 核心集 `node scripts/run_e2e.mjs --mode preview` | **106 passed，PASS，0 gate issues**（4.2m） |

**冲销复核链路（本轮已补齐）**：`test_posting_api.py` 的手工凭证与冲销两段都改为「先提交待审版本 → 独立复核 → 过账/冲销」：
手工凭证走 `POST /posting/manual-requests → approve → post`（断言 `independent is true`）；冲销先以 `relation_kind="reversal"`、`original_voucher_id` 提交待审版本并复核，再以 `review_request_id` 调用
`POST /posting/vouchers/{id}/reverse`，与 `app/posting/service.py:598` 的契约一致。该文件 **1 passed（5.3s）**，不再有红灯。

## 四、未验证 / 边界

- 只做了 SQLite 与 PG16 的定向回归；**PG17 本候选未跑**（本轮无迁移、无 SQL 方言差异，但未实测）。前端无改动，故未重跑 vitest/tsc/build。
- 跨年整链、真实复核-关期链路下的历史稳定性仍以单元级夹具验证，未在浏览器端复现。
- 未提交、未推送；候选仍有 84 个已跟踪修改 + 68 个未跟踪文件。
