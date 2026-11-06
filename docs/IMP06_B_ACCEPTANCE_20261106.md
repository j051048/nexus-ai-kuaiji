# IMP06-B 验收记录：关键页面可读性与设计规范收口

- 日期：2026-11-06
- 工程状态：**本地通过（关键页面范围）**
- 外部验收：真机、200% 浏览器缩放人工确认、屏幕阅读器人工验证 **pending**
- 候选：HEAD `143d4aea7154b1905b96ab757ecd14865354b641`，source 1916 / `e058e8418fcfaee9f4d1b9cbc714a2d6ddf1cd48c4a5288763fd8f4bb833961b`，migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`

## 范围

本轮只治理 IMP02–IMP04 涉及的关键页面：员工首页/录入、财务核验、会计同屏复核、客户工作台记录、任务列表与身份条。未做全仓字号/颜色/断点替换。

## 失败测试与证据

新增 `e2e/imp06-readability.spec.ts` / `playwright.imp06.config.ts`。

1. **06-T1 可操作文字过小**
   - 首次运行 `.list-tabs [role=tab]` 计算字号 **10px**、`.empty-state p` **10px**、`.workspace-footer` **8px**；断言 `>=12` 失败。
   - 这是实际可读性问题，不是 antialiasing/选择器误差；`getComputedStyle` 直接读取渲染值。
2. **06-T2 长金额被裁切**
   - 1366×900 下 `¥-123,456,789,012.34` 的 `.workspace-task-amount` `scrollWidth-clientWidth = 25px`，被 115px 金额列裁切。
   - 390×844 下移动金额条与页面横向溢出同时断言；修复前该用例在桌面断言处失败。
3. **06-T3/T4/T6 现有能力复验**
   - 原生 `<details>` 帮助可用键盘 Enter 展开；axe（wcag2a/wcag2aa/wcag21aa）对带任务列表的财务页 **0 violations**。
   - 现有 `e2e/qa-accessibility.spec.ts` 对 employee/finance/accountant 三岗位继续通过。
   - 未发现需要关闭 axe 规则的新问题。

## 修改

- `src/styles.css`
  - `.list-tabs [role='tab']` 字号 10px → `var(--font-size-xs)`（12px），颜色从低对比 `#9aa68e` 调到 `#566856`。
  - `.empty-state p` 10px → 12px，颜色 `#a9b69a` → `#5f705f`。
  - `.workspace-footer` 8px → 12px，颜色 `#b0bba2` → `#647461`。
- `src/workspace.css`
  - 任务金额列 `115px` → `minmax(115px,150px)`；1120px 断点 `95px` → `minmax(140px,150px)`。
  - 取消移动/平板金额的 `overflow:hidden;text-overflow:ellipsis`，最大宽度 150→180px；金额仍 `white-space:nowrap`、等宽数字、右对齐。
- 新增 `e2e/imp06-readability.spec.ts`、`playwright.imp06.config.ts`。

## 命令与结果

| 检查 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| IMP06 浏览器 | `npx playwright test --config=playwright.imp06.config.ts --reporter=line` | **4 passed / 11.9s** | `tmp_test/imp/imp-20261106-a/imp06-browser.json`、`imp06-results/` |
| 现有 a11y | `npx playwright test e2e/qa-accessibility.spec.ts --config=playwright.config.ts` | **3 passed / 12.1s** | 终端输出 |
| IMP02 回归 | `npx playwright test --config=playwright.imp02.config.ts` | **8 passed / 53.7s** | 终端输出 |
| IMP03 回归 | `npx playwright test --config=playwright.imp03.config.ts` | **2 passed / 5.6s** | 终端输出 |
| IMP04 回归 | `npx playwright test --config=playwright.imp04.config.ts` | **6 passed / 6.4s** | 终端输出 |
| 类型 | `npm run typecheck` | exit 0 | `tmp_test/imp/imp-20261106-a/` |
| 构建 | `TMP=tmp_test/tmp TEMP=tmp_test/tmp npm run build` | exit 0 | `dist/` |
| 体积 | `node scripts/frontend_budget.mjs dist` | **passed**；initial 565,907B/gzip 175,000；home 593,430B/gzip 185,573；CSS 144,373B | `imp06-budget.json` |

## 关键测量

- 金额文本：`¥-123,456,789,012.34`，负号、币种、千分位完整。
- 桌面金额溢出：修复前 **25px** → 修复后 **0**。
- 390×844：页面横向溢出 0，移动金额条完整；683×450（1366 窗口 200% 缩放的有效布局宽度近似）仍无横向溢出，金额完整。
- 任务帮助：`summary` 计算字号 14px；键盘 Enter 展开后说明可见。
- 状态：`.workspace-state-pill` 文本非空，状态不只靠颜色。
- 类型/构建/预算均通过，CSS 较 IMP04 增加 50B。

## 例外（有用途，非关键文本）

- `.eyebrow` 8px、`.review-step` 7px、`.list-tabs [role=tab] > span` 8px 仍是装饰性英文/编号，不承载金额、状态或操作；本轮未改，避免全仓字号替换。
- 这些例外不影响关键正文/表单的 14px、辅助 12px、手机输入 16px 下限。

## 未治理/未验证

- 未全仓治理：`LegacyMigrationWorkspace`、管理后台旧页、部分报表/交付旧样式不在本轮关键页范围。
- 200% 缩放用 683px 有效布局宽度近似，未做真实浏览器缩放人工确认；真机/屏幕阅读器仍 pending。
- 本轮 Chromium 单引擎；跨引擎由现有 IMP02/07 套件覆盖，不把本包 4 项浏览器结果外推为全引擎。
- 未改权限、审批、账套、历史 SQL。
