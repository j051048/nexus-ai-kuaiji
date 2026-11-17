# FSC02 验收（2026-11-16）

## 状态

- 工程状态：本地 SQLite/浏览器/PG16/PG17 通过；数据库恢复通过；代收独立依据与来源逐行回执通过；同候选 P01 未跑。
- 外部验收：未授权/缺客户规则；真实客户规费规则、真实银行、真实 T+ 未验证。
- 本轮不宣布 FSC02 最终发布完成：尚缺同候选 P01 与真实客户/银行/T+ 外部验收。

## 已验收能力

1. 规费辅助核算使用 canonical name；旧 ID 值/停用/版本变化使旧批次复核失效。
2. 规费批次复用来源台账、独立复核、政策版本绑定；复核时政策变化 409。
3. 复核落库 `fee_receivable_items`，按驾驶员形成稳定应收对象，状态 open/settled。
4. 银行到账分配：收入方向、未审核草稿、开放期、应收/银行双重余额约束、部分/多次分配、幂等。
5. 到账核销撤销：只写撤销字段、不删历史、重复撤销 409、净额恢复；退款未撤销时不得撤成负净额。
6. 退款：支出方向、原因、幂等、退款≤净额/银行付款余额；原收款不修改；退款记录独立。
7. 前端可完成登记到账、登记退款、撤销核销；1280/390 无横向溢出。

## 证据

- `nexus_backend/tests/test_tax06_fees.py` **20 passed/45.86s**；`test_tax06_fees.py + test_tax04_sources.py` **36 passed/72.03s**。
- `npx playwright test e2e/tax06-fees.spec.ts` **2 passed/45.8s**；截图 `tmp_test/tax06-fees-1280.png`、`tmp_test/tax06-fees-390.png`。
- `npm run typecheck` 0。
- 候选：HEAD `767f7cdf…`；source 2236/`81e31898…`；migration 404/`8fd6eaab…`。
- PG：`test_tax06_fees_postgres.py` 在自建隔离库 **PG16 6 passed/9.37s**、**PG17 6 passed/8.77s**，覆盖独立复核、来源升级、RLS、车队指标、退款净额/撤销守卫、多银行拆分/一银行多应收/跨期拒绝。

## 未通过/未做

- 退款/撤销/多银行并发：PG 双会话核销已串行化；退款并发未专项。
- 代收独立依据/逐行回执；跨期/多银行/一银行多应收已测。
- FSC02 完整 PG 全链、同候选 P01；SQLite 恢复已覆盖应收/分配/退款/原件。
- 真实客户规费规则、真实银行回单、真实 T+ 回执。

## 多银行、合并与跨期（2026-11-16）
- PG 专项新增 `test_pg_fee_allocations_span_banks_and_reject_cross_period`；运行中修复两个真实缺陷：`reverse_allocation` 使用只读事务、PG 249 未授予 `UPDATE`（新增 252 授权迁移）。
- 一笔应收可拆到多家银行到账（银行余额分别扣减），一笔银行到账可跨多个驾驶员应收，直至双方余额为零。
- 已撤销分配不再占用银行到账余额；撤销后同一银行可重新分配。
- 银行到账日期月份必须等于应收期间，跨期分配 409，不按草稿期间放行。
- `test_tax06_fees.py::test_fee_allocations_span_banks_and_one_bank_spans_receivables` 覆盖上述路径；全量 20 passed。
- 仍未做：付款人独立代收依据、逐行回执、同候选 P01。

## 2026-11-16 增补：PG 双会话与恢复

- PG16/17 `test_pg_concurrent_allocations_do_not_double_settle_receivable`：两个线程同时全额核销同一应收，恰好一个 `ok`、一个 409；只落一条分配，应收 settled。
- 实现：PG 事务内对 `fee-receivable:{id}` 与 `fee-bank:{id}` 使用 `pg_advisory_xact_lock`；不使用 `FOR UPDATE`，避免要求 `sources` UPDATE 权限。
- `test_tax04_recovery.py`：复核→足额核销→退款 3000→备份/verify/恢复后，应收 open、分配未撤销、退款保留、来源原件 SHA 一致。
- 证据：`tmp_test/ak05-pg-runs/9de96672b7c54f60872e9c69aafd8509`（PG17 7 passed/10.46s）、`bbbbad8dce13413bb915c358e1c7865c`（PG16 7 passed/13.10s）、SQLite `test_tax06_fees.py` 20 passed/51.68s、`test_tax04_recovery.py` 2 passed/7.61s。
- 候选：source 2236/`258af3132eff954bbc2d1a2185d02973936822b99a4e6f7935892ecd7ef3d8db`；migration 404/`8fd6eaab39b60fe2d271c0dce07598d416cee67932aa1d01efea7563748ec0b2`。


## 2026-11-16 增补：代收依据、逐行回执与最终候选

- 付款人与驾驶员不一致时，分配必须提供不少于 5 字的代收核定依据；缺依据 409，依据随分配保存并纳入幂等指纹。路由/UI 均支持“付款人/代收依据”，到账记录表展示两列。
- 来源行逐行回执：`GET /api/tax/books/{book_id}/sources/{source_id}/rows/{row_number}/receipt` 返回原文件 SHA、版本、行号、行值、当前核定状态与 `receipt_fingerprint`；源行不存在 404，核定变化后指纹变化。UI 行操作可下载 JSON 回执。
- SQLite 组合回归：`test_tax06_fees + test_tax04_sources + test_tax04_recovery + test_fsc03_organization + test_fsc01_profiles` **42 passed/79.21s**。
- PG16/17 `test_tax06_fees_postgres.py` 各 **8 passed**：PG16 11.90s `tmp_test/ak05-pg-runs/fae9a918b9d54614991998a9e9fd3760`；PG17 10.72s `tmp_test/ak05-pg-runs/d73f5ff5e3534e188e3916756ad17fd6`。
- 浏览器：`tax06-fees` 1280/390 **2 passed/45.4s**；`tax04-sources` 1280/390 **4 passed/52.0s**（含逐行回执 JSON 下载与 64 位指纹）。
- 最终候选：HEAD `767f7cdf7c717c98d3516a0e31b53bd8cd9ae1a9`；source **2238 / `22886fd7f96b5aa0f58df0798e145c67ab87b9661cd1df2eadca483b96e00f47`**；migration **406 / `53c3da09c53f89c6dbb465514287f67d407b822e3405c7773fb84f322dacc5aa`**。
- 仍未做：同候选 P01；真实客户规费规则、真实银行回单、真实 T+ 回执。
