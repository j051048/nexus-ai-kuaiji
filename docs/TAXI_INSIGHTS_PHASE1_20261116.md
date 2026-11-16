# 经营看板第一期（2026-11-16）

## 目标与边界

- 面向事务所负责人/管理员，入口在左侧“事务所管理 → 经营看板”，路由 `/workspace/insights`。
- 第一期是全所总览：交付进度、异常、已复核报表财务汇总、风险、人员负载；不做单企业资产/负债/权益明细，不做多企业对比/甘特。
- 只读投影：不创建凭证、不关账、不复核、不发送消息，不推断原因。
- 财务数字只汇总最新 **reviewed/published** 报表快照。没有报表的账套计入 `missing_reports`，不按 0 元计入。利润不是全所净利润，未复核账套不含。
- 系统耗时/队列年龄不是人工工时；不做人效考核结论。

## 实现

- 后端 `nexus_backend/app/observability/insights.py::FirmInsightsService.overview`。
  - `GET /api/firm/observability/insights?period=YYYY-MM`；manager 权限；只读事务。
  - 账套、资料验收、草稿、税费核定、异常补件、服务单负责人/到期、报表版本、近 6 月关账、AI 成本、通知 unknown。
  - 输出 KPI、财务汇总、六阶段、异常分布、6 月趋势、风险（最多 12）、人员负载（最多 12）。
  - 阶段是投影：`closed→archive`；`published report→delivery`；有草稿/税费核定→`review`；资料未验收→`intake`；否则 `prepare`。
- 前端 `src/FirmInsights.tsx` + `src/firm-insights.css`。
  - 8 张 KPI、已复核报表财务汇总（资产/负债/权益/收入/费用/利润/现金/未分类现金；无覆盖显示“未覆盖”，收入/利润显示“暂无”）、交付阶段堆叠条、异常环形图、近 6 月趋势 SVG、风险表/手机卡片、人员负载条。
  - 不引入图表库；趋势下有数据表 fallback。
  - 默认选 `/workbench` 中最新账套期间，避免当月无账套时展示假空。
- 导航：`insights` 加入 `workspace-views`、导航定义与 `firm_owner/admin` 主菜单。

## 验证

- 后端：`cd nexus_backend && python -m pytest tests/test_firm_insights.py -q` → **3 passed/8.98s**。
  - 空月份不生成账套/报表/趋势；manager 403 员工拒绝。
  - 人工写入一张 reviewed 报表后，`known_reports`/资产/利润/未分类现金只来自该报表；`known+missing=books`。
- 浏览器：`npx playwright test e2e/firm-insights.spec.ts --workers=1` → **2 passed/10.4s**（财务汇总与手机风险卡加入后复跑）。
  - 1366/390；标题、导航（桌面）、KPI、阶段、趋势、风险、人员负载、只读说明；无横向溢出。
  - 截图 `tmp_test/firm-insights-1366.png`、`tmp_test/firm-insights-390.png`。
- 类型检查：`npm run typecheck` → 0；`npm run lint` 0 error/4 既有 warning。
- 构建：`TEMP/TMP=tmp_test/esbuild-tmp npm run build` 成功；index JS 566.13 kB/gzip 175.41，CSS 99.95 kB；`FirmInsights` 12.6 kB/gzip 3.8 kB（JS）、6.6 kB（CSS）。
- 导航单测：`npm test -- src/navigation-settings.test.ts` → 24 passed。
- 候选：HEAD `12951a26`；source 2205/`49f822f674bd940e528f993ba3b6d5fe4ef11807bb829127a04baa152800ca59`；migration 388/`0f667fde…`。P01 未针对本候选重跑。

## 明确未完成

- 240 企业 p95<500ms 压测未做；当前仅 3 套种子账套。
- 单企业财务画像（资产/负债/权益/现金/应收应付/账龄）为第二期。
- 企业对比、甘特、同环比、导出为第三期。
- 报表财务覆盖为 0 的种子数据只证明“未知不写 0”，未证明真实客户报表口径。
- 真实 T+、客户数据、生产性能、真机未验证。
