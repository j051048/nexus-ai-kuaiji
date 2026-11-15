# 批次 I 基线：前端壳层与岗位首屏（2026-11-10）

## 归属与前置

- 仓库：`C:\Users\Fei\Desktop\AI应用\nexus-ai-kuaiji`
- HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`
- 前置：H 本地/PG/浏览器/P01 已通过（`docs/TAXI_BATCH_H_ACCEPTANCE_20261110.md`，状态 partial）。A–H 改动未提交，本批只做前端增量，不重置/清理/整仓格式化。
- 本批前候选：source 2152 / `0d0aea3a…`、migration 377 / `12c7e6ec…`。

## 采样身份与视口

- 身份：accountant、employee、department、finance、reviewer（`/api/session?persona=...`，本地 GoTrue/演示身份）。
- 视口：1366×768、1920×1080、390×844、375×812；683×768 作为 1366@200% 的等效布局宽度（不是浏览器缩放 API 的替代声明）。
- 浏览器：Chromium；壳层关键样例在 Firefox、WebKit 复跑。

## 修复前可观察到的问题

1. 非会计/非员工岗位的“岗位说明与权限”折叠条位于主标题和任务概览之间，重复标题（如“事务所管理”），在每个岗位首屏多占约 36px。
2. 390px 会计首屏中第一条待办约从 790px 开始，任务可见但接近首屏下沿；岗位说明条应先让位于任务。
3. 员工首屏已有主行动、任务列表和折叠权限说明，未发现横向溢出。
4. 五岗位在多视口均只有一个可见 `h1`；单位/身份常显；没有发现横向溢出。

## 基线证据

- 旧截图（修复前）：`tmp_test/product-audit/01-entry.png`、`04-employee.png`、`06-mobile-firm.png`、`11-approver.png`、`17-handoff-options.png`。
- 修复后截图：`tmp_test/tax-i/accountant-1366.png`、`employee-1366.png`、`department-1366.png`、`finance-1366.png`、`reviewer-1366.png`，以及各岗位 `-390.png`、`-683.png`。
- 壳层回归：`e2e/tax-i-shell.spec.ts` 5 passed/27.8s；`e2e/qa-accessibility.spec.ts` 3 passed/13.7s；Firefox/WebKit 各 1 passed（accountant）。

## 组件与 token 入口

见 `docs/FRONTEND_COMPONENT_USAGE_20261110.md`。本批不新增 UI 库、不升级 React、不引入远程字体/CDN。
