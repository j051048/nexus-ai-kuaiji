# 批次 I 验收：前端基础与壳层收敛

日期：2026-11-10（本地）
状态：**本地工程通过；真机/人工无障碍/真实客户未验证**。

## 结论分栏

| 范围 | 结论 |
| --- | --- |
| 本地工程 | 通过：五岗位壳层、唯一标题、身份常显、任务前置、375/390/683/1366/1920 无横向溢出。 |
| PG16/17 | 本批不改后端/迁移；沿用 H 的 P01 与 I 相关身份/导航回归，未新增数据库写入。 |
| 桌面/手机浏览器 | Chromium 五岗位 × 五视口通过；Firefox/WebKit accountant 关键样例通过。 |
| 真机/人工无障碍 | 未验证；自动化 axe 通过不等于人工认证。 |
| 真实客户/T+ | 不适用；本批不接真实外部。 |

## 修复

- 问题：岗位权限折叠条位于主标题与任务概览之间，重复标题并在每个岗位首屏占用约 36px。
- 失败证据：修复前 `tmp_test/product-audit/04-employee.png`、`06-mobile-firm.png`；390 会计首条任务约在 790px。
- 修改：`src/WorkspaceHome.tsx` 将 `roleGuide` 统一移到工作内容之后，折叠摘要改为“岗位与权限 / 查看岗位与操作说明”，不重复 `h1`。
- 通过证据：`e2e/tax-i-shell.spec.ts` 5 passed/27.8s；断言 `guideBox.y > inboxBox.y`，修复前失败；截图 `tmp_test/tax-i/accountant-390.png` 中首条任务提前、岗位说明移至列表后。

## 逐项

| ID | 结果 |
| --- | --- |
| I01 品牌/身份/标题叠加 | 通过：每页唯一可见 h1；岗位说明在任务之后；首屏保留身份与任务。 |
| I02 单位/身份常显、多身份 | 通过：顶部身份条常显单位与主要身份；未改主岗位切换。 |
| I03 两套导航/高亮 | 复用既有唯一导航；本批未新增入口，既有导航回归通过。 |
| I04 计数未知/空/失败 | 未改；既有 H5 断言与 `qa-accessibility` 通过。 |
| I05 375/200% 遮挡 | 375/390/683 无横向溢出；683 作为 200% 等效布局；未做真实 zoom。 |
| I06 切企业/撤权迟到 | 复用 H5 既有迟到/撤权回归，未弱化。 |
| I07 token 迁移影响 | 未全量迁移散落值；未改登录/财务/交付业务布局，构建与预算不退步。 |

## 验证命令与结果

- `npx playwright test e2e/tax-i-shell.spec.ts --workers=1` → 5 passed/27.8s。
- `npx playwright test e2e/tax-i-shell.spec.ts --browser=firefox --grep accountant` → 1 passed/15.5s。
- `npx playwright test e2e/tax-i-shell.spec.ts --browser=webkit --grep accountant` → 1 passed/15.0s。
- `npx playwright test e2e/qa-accessibility.spec.ts` → 3 passed/13.7s。
- `npm run typecheck` → 0。
- `npm run lint` → 0 error（4 warning）。
- `TEMP/TMP=tmp_test/esbuild-tmp npm run build` → 成功。
- `npm run test:frontend-budget` → 6/6 passed；initial 565541/175199，home 593043/185753，CSS 99722/144373。

## 未验证

- 真实 iOS/微信、浏览器 200% zoom 的自动化、人工屏幕阅读器、真实客户多身份切换。
- 组件说明是约定文档，不代表所有旧页面已迁移到同一密度/token。

## 下一批准入

I 本地收口；J 可采用 `docs/FRONTEND_COMPONENT_USAGE_20261110.md`。M 仍未启用，禁止改依赖。
