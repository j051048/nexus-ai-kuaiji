# 经营看板第二、三期（2026-11-16）

## 范围

第二期：单企业财务画像。

- 入口：经营看板 → 企业画像，企业选择器按 `/workbench` 当前期间账套。
- 展示已复核报表的资产/负债/权益/收入/费用/利润、应收/应付、账龄、资产负债结构、近 6 个月收入/利润。
- 无已复核报表时显示“暂无/未覆盖”，不按 0 元计入；账龄无未核销明细时显示“—”，不把报表应收当账龄。
- 账龄只取 `settlement_items` 中 1122/1221 未核销金额，按创建日分 0–30/31–60/61–90/90+；不等同于报表应收。

第三期：企业对比、交付时间线、CSV 导出。

- 企业对比：同期间全部账套，已复核企业显示资产/负债/权益/收入/费用/利润/应收/应付；未复核企业保留行但显示“—”。
- CSV 导出：`firm-comparison-YYYY-MM.csv`，带 BOM，金额为整数分。
- 交付时间线：按账套期间与到期日展示；无到期日只显示“待安排”，不推断日期。
- 多企业同比/环比：趋势仍由第一期近 6 月数据提供，本期未增加复杂同环比计算器。

## 接口

- `GET /api/firm/observability/insights/company?book_id=...`
- `GET /api/firm/observability/insights/companies?period=YYYY-MM`
- `GET /api/firm/observability/insights/timeline?period=YYYY-MM`

全部只读、服务端 manager 权限、事务所隔离；不创建/复核/关账。

## 代码

- `nexus_backend/app/observability/insights_extra.py`
- `nexus_backend/app/observability/router.py`
- `nexus_backend/app/main.py`
- `src/FirmInsightsViews.tsx`
- `src/FirmInsights.tsx`
- `src/firm-insights.css`

## 验证

- 后端：`cd nexus_backend && python -m pytest tests/test_firm_insights.py tests/test_firm_insights_capacity.py -q` → **5 passed/14.76s**。
  - 企业画像只取已复核报表；应收/应付按科目前缀；员工 403。
  - 企业对比、时间线、企业画像均从真实 HTTP 路由读取。
- 浏览器：`npx playwright test e2e/firm-insights.spec.ts --workers=1` → **4 passed/14.2s**。
  - 1366/390 总览；1366/390 企业画像、企业对比、交付时间线；CSV 下载；无横向溢出。
  - 截图 `tmp_test/firm-insights-company-1366.png`、`-company-390.png`、`firm-insights-compare-1366.png`、`firm-insights-timeline-1366.png`。
- `npm run typecheck` → 0；`npm run lint` → 0 error/4 既有 warning。
- 构建：index JS 566.13 kB/gzip 175.42，CSS 99.95/20.32；`FirmInsights` 22.75/5.97，CSS 10.21/2.23。

## 候选

- HEAD `12951a26df05373a32d0122268eb66a88c437e99`。
- source `2205 / 49f822f674bd940e528f993ba3b6d5fe4ef11807bb829127a04baa152800ca59`。
- migration `388 / 0f667fde3e71bcdac9a3ade8bae3a40361d2f81f104953d60d22c7375e8c8390`。
- 未提交、未推送、未部署；未针对本候选重跑 P01。

## 未完成

- ~~240 企业 p95<500ms 压测~~：`test_firm_insights_capacity.py` 240 账套 p50 32.0ms、p95 62.3ms（SQLite 本地，不是生产网络/多进程）。
- 同比/环比复杂计算器、多企业甘特拖拽、Excel（仅 CSV）。
- 真实客户报表覆盖；当前种子报表覆盖为 0。
- 真机、真实 T+、客户数据未验证。
