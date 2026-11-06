# IMP03 验收记录：待办列表与业务文案减负

- 日期：2026-11-06
- 状态：本地通过；浏览器通过（Chromium 桌面/手机视口）；真人/真机 pending。
- 依赖：IMP02 本地通过；IMP05/06 基线保留。
- 候选：HEAD `143d4aea7154b1905b96ab757ecd14865354b641`；source 1911 / `9c8cb0c557b5b90eb101910fe8246ecde079bd52e51326864be1cb143fc44b68`；migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`。
- 运行目录：`tmp_test/imp/imp-20261106-a/`。

## 已有能力与缺口

已有：服务端任务投影、短清单/分页、返回恢复、未知计数“查看清单”而非 0、任务类型/状态文案、`independent_reviewer` 等权限夹具。

缺口（红测确认）：

1. 非员工清单每行都渲染完整“下一步”长句，列表被重复说明撑高。
2. 空标题或纯 UUID 标题没有可读兜底。
3. 财务首屏的主卡三个大按钮纵向堆叠，1366×768 下第一条真实待办落在 720px 以下，首屏看不到任务。

## 失败测试与证据

`src/workspace-home-focus-render.test.ts` 新增 3 项：

- `keeps ordinary row guidance in one shared disclosure instead of a long inline paragraph`
  - 修复前失败：行内实际为 `<p class="workspace-task-guidance"><span>下一步</span>打开事项，核对金额...`，无 `处理说明`/`workspace-task-help`。
- `keeps the reason inline for a blocked row`（修复前通过，作为不回归保护）
- `falls back only for empty or opaque technical titles and preserves real references`
  - 修复前失败：`TypeError: taskTitle is not a function`。

浏览器红测（`e2e/imp03-lists.spec.ts`，真实页面 + 仅任务列表响应夹具）：

- 修复首轮失败：第一条财务待办 `getBoundingClientRect().top` 约 720，`expect(top).toBeLessThan(700)` 失败；手机视口任务行也落在 844 以下。
- 断言覆盖：普通行默认折叠、blocked 行 inline 原因、空标题兜底、UUID-only 兜底但业务摘要含 UUID 时保留、计数 2 不补零、桌面/手机无横向溢出、主动作仍为“核验资料”。

## 最小修改

- `src/task-presentation.ts`
  - 新增 `taskTitle(task, summary)`：仅当标题为空、`undefined/null/未命名`、纯 UUID 或 24–64 位十六进制时，用 `kindLabel · company · amount` 兜底；含 UUID 的正常业务标题原样保留。
- `src/WorkspaceHome.tsx`
  - `<h3>` 改用 `taskTitle`。
  - 仅 `blocked` 行保留可见 `workspace-task-guidance`；普通 mine/waiting/done/running 收进 `<details class="workspace-task-help"><summary>处理说明</summary>`，员工/非员工一致。
- `src/workspace.css`
  - 财务主卡动作改为横向排列、按钮 44px 高，减少卡片高度。
  - 手机首屏隐藏财务主卡副说明与次级动作，只保留“核验下一项”；资料清单仍可从底部导航/页签进入。
- 未改后端任务状态、授权、分页、action label、独立复核或任务详情接口。

## 验证

| 命令 | 结果 | 证据 |
| --- | --- | --- |
| `npx vitest run src/workspace-home-focus-render.test.ts src/task-presentation.test.ts` | **78 passed in 0.41s** | 终端输出 |
| `npx playwright test --config=playwright.imp03.config.ts` | **2 passed in 5.7s** | `tmp_test/imp/imp-20261106-a/imp03-browser.json`、`imp03-results/` |
| `npm run typecheck` | exit 0 | 终端输出 |
| `TMP=tmp_test/tmp TEMP=tmp_test/tmp npm run build` | exit 0 | `dist/` |
| `node scripts/frontend_budget.mjs dist` | passed；initial 565907 B / gzip 175010；home 593430 B / gzip 185573；CSS 99676/144323 | `tmp_test/imp/imp-20261106-a/imp03-budget.json` |
| 桌面 1366×768 | 第一条真实待办 top **634.17px**；普通行高 **123.97px**，blocked 行 **146.17px**；无横向溢出 | `imp03-desktop.png`、`imp03-desktop-metrics.json` |
| 手机 390×844 | 第一条待办 top < 800px；主动作可见；无横向溢出 | `imp03-mobile.png` |

说明：任务列表响应夹具只用于稳定呈现两条不同状态的 UI 行；页面登录、权限、任务详情读取仍走真实本地 API，未伪造业务成功状态。

## 限制

- 只验证 Chromium 桌面/手机视口；Firefox/WebKit、真机、真实网络、屏幕阅读器人工验证 pending。
- 尚未做真实多企业数据量下的长列表滚动/返回恢复人工试跑；分页/筛选恢复依赖既有测试。
- 未提交、未推送、未部署；候选未冻结。
