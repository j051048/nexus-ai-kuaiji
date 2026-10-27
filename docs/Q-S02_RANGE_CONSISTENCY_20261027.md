# Q-S02 连续账簿与导出一致性 — 本地验收记录（2026-10-27）

- 候选目录：`tmp_test/qr01-integration`；基线 HEAD `32e3042d`；未提交、未推送
- 范围：`nexus_backend/app/posting/service.py`、`nexus_backend/app/posting/presentation.py`（只复核，未改）、`nexus_backend/app/opening/service.py`（只复核，未改）
- 方法：先写负向测试，用「撤掉修复」证明测试会红，再恢复并跑回归

## 1. 三个待测项的真实状态

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 查询窗口规避上期 | **真缺陷，本轮修复** | 范围从 2026-11 开始时，`_range_coverage` 只在范围内相邻月份比对，10 月→11 月的断点完全不可见，`complete` 仍为 `true` |
| 辅助身份互换（同科目内 A/B 换额、合计不变） | Q-R02 已按 `(account_id, aux_key)` 分组，本轮补负向测试 | `test_range_detects_an_auxiliary_identity_swap_with_equal_account_totals` |
| 页面→导出竞争（渲染期间改科目名称/版本） | Q-R02 的 `_range_digest` 已含科目版本快照，本轮补竞争测试 | `test_range_export_rejects_an_account_revision_during_render` |

## 2. 修复（`nexus_backend/app/posting/service.py`）

新增：

- `_opening_map(conn, package_version_id)`：按 `(account_id, aux_json)` 汇总 `opening_lines`；
- `_movement_map(conn, book_id)`：按 `(account_id, aux_json)` 汇总 `voucher_lines`；
- `_closing_map(opening, movement)`：期初 + 借 − 贷，逐键计算；
- `_boundary_blockers(conn, source_book_id, opening, movement, carried, target_book_id, hint="")`：返回 `opening_mismatch` 与 `continuity_unverified` 两类 blocker；
- `_prior_boundary(conn, actor, ledger_id, start, first, capability)`：取范围起点之前最近一个 `ledger_periods` 月份，若能读到且已绑定期初，就用 `_boundary_blockers` 比对（blocker 的 `period` 记在范围内首月，文案后缀「（上一期间 YYYY-MM）」）；无权或未绑定则静默跳过。

重构：月份循环改用上述 helper；相邻月份比对改为调用 `_boundary_blockers`；`_range_coverage` 在相邻循环之前先执行 `_prior_boundary`。

## 3. 测试（`nexus_backend/tests/test_qs02_range_consistency.py`，4 例）

1. `test_range_window_does_not_hide_a_broken_prior_month`：10 月期末 10000.30，11 月期初 10000.00，只查 11 月 → `complete=False`，唯一 blocker 为 `('opening_mismatch','2026-11')`；
2. `test_range_with_a_matching_prior_month_stays_complete`：期初衔接时仍 `complete=True`、blockers 为空；
3. `test_range_detects_an_auxiliary_identity_swap_with_equal_account_totals`：科目合计不变但辅助对象换额 → 判 `opening_mismatch`；
4. `test_range_export_rejects_an_account_revision_during_render`：monkeypatch `presentation._render_csv`，渲染途中 `OpeningService.revise_account` 改名 → 409 且 detail 含「已变化」。

夹具：`period_fixtures.restore_period_state(db, book_id, 'closed')` 关账；`test_q03_continuous_books.second_month` 的 `opening_lines` 扩展为支持 `(code, amount, aux_dict)` 三元组（二元组仍兼容）。

## 4. 红绿证据

```
# 撤掉 service.py 的修复
git stash push -- nexus_backend/app/posting/service.py
pytest nexus_backend/tests/test_qs02_range_consistency.py -q
→ 1 failed, 3 passed
   E  assert True is False   # complete 仍为 True，窗口把断点吞掉

git stash pop
pytest nexus_backend/tests/test_qs02_range_consistency.py -q
→ 4 passed in 23.91s
```

另两项在修复前即通过——它们是 Q-R02 已实现能力的**回归护栏**，不是本轮新修。

## 5. 回归

```
PYTHONPATH=nexus_backend .venv/Scripts/python.exe -m pytest \
  nexus_backend/tests/test_q03_continuous_books.py \
  nexus_backend/tests/test_q05_export_consistency.py \
  nexus_backend/tests/test_qr02_financial_correctness.py -q
→ 27 passed in 135.18s
```

## 6. 未做 / 边界

- 只在 SQLite 临时库验证；未在 PG16/17 重跑本组用例。
- 上期不可见或未绑定时静默跳过——这是**避免泄露无权月份**的取舍，代价是无法证明那段衔接；`BLOCKER_TEXT` 未新增文案。
- 前端尚未展示「上一期间 …」后缀文案（属 Q-S03A 范围）。

## 7. 本轮改动文件

- 修改：`nexus_backend/app/posting/service.py`、`nexus_backend/tests/test_q03_continuous_books.py`（夹具扩展）
- 新增：`nexus_backend/tests/test_qs02_range_consistency.py`、本文件
