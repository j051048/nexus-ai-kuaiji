# DCP03 验收（第一刀）：简报来源契约与「未发布不展示金额」

日期：2026-11-01　基线：`981e7648`（DCP02 已推送）

## 1. 解决的问题

旧月度简报只要求 `periods.status='closed'`，金额来自 `drafts/sources/人工余额` 聚合，与「已发布冻结报表」「资料包版本」完全脱节：客户在报表还没复核发布时就能看到一套金额，且与包内 PDF 可能不是同一版本。

本刀引入只读来源契约 `nexus_backend/app/monthly/briefing_source.py`，把「客户可见成果」收敛为**唯一权威**：已发布且与锁期一致的冻结报表。

## 2. 契约

`resolve_source(conn, book, period, dialect)` 返回：

| 字段 | 含义 |
| --- | --- |
| `state` | `published` / `legacy_close` / `pending_report` / `inconsistent` |
| `reason` | `no_published_report`、`close_not_locked`、`close_report_mismatch`、`package_report_mismatch`、`bad_report_hash`、`bad_report_fingerprint`、`bad_report_version`、`bad_report_content`、`bad_report_amounts` 等 |
| `report` | 已发布报表的 id/version/模板版本/`content_hash`/`inputs_fingerprint`/发布时间/被替代前版本 id |
| `close` | 锁期运行 id/状态/关联报表/锁定时间 |
| `package` | 交付包 id/version/关联报表版本/`manifest_sha256` |
| `amounts` | 仅在 `state=published` 时存在，取自报表 `content_json` |
| `axes` | 五条独立轴线：`internal_close`、`external_receipt`、`tax_filing`、`notification`、`client_sign` |
| `client_visible` | 仅 `state=published` 为 true |

规则：

1. 报表 `content_hash` / `inputs_fingerprint` 必须是 64 位十六进制；版本必须为正整数；内容必须是 JSON 对象；`profit_statement` 三个金额必须是整数——任一不满足即 `inconsistent`，金额置空。
2. 已发布报表必须由状态为 `locked/published` 的锁期运行消费，且 `report_run_id` 指向同一张报表；包必须与报表同版本（`report_version`）且 `manifest_sha256` 合法。
3. 五轴互不替代：`tax_filing` 现在恒为未完成，因为没有申报回执台账，只能用已复核底稿作为内部准备证据。
4. 旧流程（只有 `periods.status='closed'`、没有报表运行）降级为 `legacy_close`：仍返回旧口径数字（内部使用），但 `client_visible=false`，前端显示「历史账期口径：待正式报表发布」。

## 3. 交付清单

- `nexus_backend/app/monthly/briefing_source.py`（新增，只读）
- `nexus_backend/app/monthly/briefing.py`：`_pending` / `_published` 两个投影分支，旧口径保留但标记 `client_visible=false`
- `nexus_backend/tests/test_briefing_source.py`（新增，14 例）
- `src/MonthlyBriefing.tsx`：`BriefingPending` 组件与 pending 分支；`flows` 可空、`tax.change` 可空
- `src/monthly.css`：`.briefing-pending` / `.briefing-axes`（桌面两列、移动单列）
- `src/monthly-briefing-pending.render.test.ts`（新增，2 例）
- `e2e/monthly-briefing.spec.ts`：锁期后断言改为 pending 口径（本刀的真实行为）

## 4. 证据

| 项 | 命令 | 结果 |
| --- | --- | --- |
| 契约单测 | `pytest nexus_backend/tests/test_briefing_source.py` | 14 passed |
| Ruff | `ruff check`（两个后端文件 + 测试） | 通过 |
| 类型 | `npx tsc --noEmit` | 无输出 |
| 组件 | `vitest run src/monthly-briefing-pending.render.test.ts` | 2 passed |
| 相邻前端 | `vitest run` 四个 monthly 测试 | 21 passed |
| 浏览器 | `npx playwright test e2e/monthly-briefing.spec.ts` | 1 passed（2.4m） |
| 回归 | `e2e/dcp01-close-actions.spec.ts` + 简报 spec | 3 passed（2.9m） |

浏览器证据展示的是**未发布态**（该账套只有旧口径锁期、没有报表运行）：徽标与标题都写「历史账期口径：待正式报表发布」，五条轴线逐条列出，页面不出现任何金额控件。截图：

- `artifacts/monthly-briefing.png`（1280×1908，桌面）
- `artifacts/monthly-briefing-mobile.png`（390×2662，移动，单列无横向溢出）

## 5. 基线缺陷（既有，与本刀无关，已复现）

1. `nexus_backend/tests/test_monthly_briefing.py` 在 HEAD 上 **8/8 全红**：`app/accounting/review_policy.py:213` 抛「制单人不能独立复核自己创建的版本」，夹具里只有 `LOCAL_ACTOR` 一个复核人。根因与 DCP01 发现的访问策略回归同源——本地缺少第二个事务所复核人。
2. `e2e/monthly-briefing.spec.ts` 原在 `e2e/ui-navigation.ts:90` 超时（`openAccounting` 找不到「会计工作台」）。本包已修：`openAccounting` 先等导航菜单项出现再判断主入口；spec 也同步到当前界面——导航名改为「月度交付」、账簿内简报是嵌入视图（没有「返回账套」）、锁期按钮因「复核人=当前人」的旧快照被禁用故改用同一份已复核快照调用真实 API 完成锁期。
3. 三个符号用例（冲回 / 退款 / 缴税）原先直接插入 `status='exported'` 的草稿，新版 `rr_draft_insert_guard` 已禁止「新建草稿自带审批」，因此改为在已发布报表投影层验证符号原样传递；草稿层的符号计算由 `reports` 的既有测试覆盖。

## 6. 判定

契约、「未发布不展示金额」与未发布态的桌面/手机浏览器证据均已落地；DCP01 回归集同时通过。仍缺**已发布态**的浏览器证据与修订版本/旧链接行为，留待下一刀（需要把正式报表发布与交付包链接进 e2e 夹具，且要先解决第 5 节第 1 条的第二个复核人夹具）。
