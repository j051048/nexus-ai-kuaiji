# IMP04 验收记录：手机录入与核验操作重排

- 日期：2026-11-06
- 工程状态：**本地通过**（前端重排 + Chromium 375/390/1366 浏览器；未做真机）
- 外部验收：真机、iOS/微信、屏幕阅读器人工验证 **pending**
- 候选：HEAD `143d4aea7154b1905b96ab757ecd14865354b641`，source 1914 / `eefe22711efe3cee94337604448170e648549e31200acbc4bcb9906d6afbf018`，migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`

## 已有能力（复验，不重建）

- `IntakeWizard` 已有模板折叠、草稿恢复、退回补件、票据识别、附件保留、字段校验和提交幂等键。
- `intakeGuidance` 只做展示；必填、审批、账套和复核仍由后端决定。
- `BusinessPreparation` 只读交接准备；不改变账套开立/权限。

## 缺口与失败证据

1. **390×844 首屏看不到金额/日期**：`e2e/imp04-mobile.spec.ts` 首次运行 `事项金额 y=1218.58`（期望 <844），`.intake-fields` 起点 >1000px。根因是引导说明、交接准备、上传 dropzone 占高，字段被推到首屏外。
2. **多企业默认第一家企业**：`src/IntakeWizard.tsx:80` 原为 `companies[0]?.id`，员工可提交多家时会在不知情下把票据记到第一家企业。新增红测 `IMP04 employee with several companies must choose one explicitly`，首次 `Expected "" Received "48176dce…"`。
3. **未选择企业时错误提示“当前没有可提交的企业”**：修默认企业后，`intakeGuidance` 把“未选择”误判为“无权限”，会误导员工联系管理员。新增单测覆盖。

## 修改

- `src/IntakeWizard.tsx`
  - 初始企业改为 `initialEvent?.company_id ?? (companies.length === 1 ? companies[0].id : '')`；多企业时必须显式选择。
  - 企业 select 增加空选项“请选择客户企业”。
  - 引导状态 next 包 `<span className="intake-status-next">`，移动端隐藏长句但保留状态和“查看录入步骤与说明”。
- `src/intake-guidance.ts` / `.test.ts`
  - `IntakeGuideInput` 增加 `companiesAvailable?: boolean`。
  - 有企业但未选择时状态“请先选择客户企业，尚未提交”，步骤 1 current；无任何可提交企业才显示“当前没有可提交的企业”并标记 blocked。
- `src/intake.css`
  - 移动端隐藏 header eyebrow、压缩引导/模板/企业/交接准备/上传区；上传区保留“选择附件/拍照上传”按钮，不再显示大段说明和 AI 提示。
  - 底部操作条压缩，保留隐私说明、暂存和提交。
  - 焦点滚动：`.intake-scroll { scroll-padding-bottom:96px }`、`.intake-fields input/select/textarea { scroll-margin-bottom:96px }`，避免键盘/短视口遮住字段。
- `e2e/imp04-mobile.spec.ts`、`e2e/imp04-behavior.spec.ts`、`playwright.imp04.config.ts`
  - T1 首屏、375/1366 可用性、短视口焦点、字段变更使确认失效、多企业必须选择。
- `e2e/employee-flow.spec.ts`
  - 票据预填用例先显式选择企业，再上传；不改变业务断言。

## 命令与结果

| 检查 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| 单测 | `npx vitest run src/intake-guidance.test.ts src/workspace-home-focus-render.test.ts src/task-presentation.test.ts src/account-identity-render.test.ts` | **100 passed** | 终端输出；`tmp_test/imp/imp-20261106-a/` |
| 浏览器 | `npx playwright test --config=playwright.imp04.config.ts --reporter=line` | **6 passed / 7.5s**（Chromium；390×844、375×812、1366×900、短视口） | `imp04-browser.json`、`imp04-results/` |
| 既有员工流 | `npx playwright test e2e/employee-flow.spec.ts --config=playwright.config.ts` | **9 passed / 41.2s** | 终端输出 |
| 类型 | `npm run typecheck` | exit 0 | `tmp_test/imp/imp-20261106-a/` |
| 构建 | `TMP=tmp_test/tmp TEMP=tmp_test/tmp npm run build` | exit 0 | `dist/` |
| 体积 | `node scripts/frontend_budget.mjs dist` | **passed**；initial 565,907B/gzip 175,004；home 593,430B/gzip 185,575；CSS 144,323B | `imp04-budget.json` |

## 关键测量（390×844）

- 改前：金额 y=**1218.58**，字段区起点 >1000。
- 改后：日期 y=**588.09**，金额 y=**719.28**，金额底 765.28 < 吸底条 top **767**；`.intake-fields` top **491.91**；`.intake-upload` 高 **54**；横向溢出 0。
- 首屏可见：企业 select、附件/拍照按钮、日期、金额、摘要起点；无需先滚动。
- 多企业时企业值为空，附件/提交在选企业前禁用。

## 限制与未验证

- 仅 Chromium 虚拟视口；iOS Safari、Android 真机、微信内置浏览器、软键盘与 200% 缩放未做，均 pending。
- 上传中断、草稿跨刷新恢复、提交结果未知的浏览器级复验未在本轮取得（尝试时本地直传/权限夹具返回 403，未把环境失败当产品缺陷）；后端幂等与尝试围栏仍由既有 NEXT08/IMP05 证据覆盖，但 IMP04 不宣称这些 UI 路径已通过。
- 375px 只跑可用性，不重复测量首屏像素；桌面 1366px 只跑可用性。
- 没有改审批政策、复核、账套或历史 SQL。
