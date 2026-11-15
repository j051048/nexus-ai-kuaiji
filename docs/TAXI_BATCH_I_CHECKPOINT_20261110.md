# 批次 I 检查点（2026-11-10）

## 状态

- 工程状态：I 本地工程通过。
- 外部验收：真机/真实客户/人工屏幕阅读器未验证。
- 当前候选：source 2153 / `0d02bb67b7a26ede809196f55ad52ffa5f0d895ae6d7aa5a17c98db373f76c50`；migration 377 / `12c7e6ec4d91bc67dbc4a26b4749eda39514e0f0ca20ebf37dd3fb40d200d745`；HEAD `781fd934…`（测试/文档改动不含在 source 外）。最终候选需在全部 I 收口后重新计算。

## 已完成

1. 读取 H5/TAX23 的壳层、身份、范围与导航成果，确认已有单壳、单导航、身份常显、范围绑定和可访问抽屉，不重建。
2. `docs/FRONTEND_COMPONENT_USAGE_20261110.md`：页面/抽屉/分页/空错/金额/选择/范围/返回/状态约定。
3. `src/WorkspaceHome.tsx`：
   - 岗位与权限 `<details>` 从主标题下方移到工作内容之后，所有岗位统一；折叠条不再重复 `h1` 文案，改为“岗位与权限 / 查看岗位与操作说明”。
   - 不修改业务状态机、权限投影、路由或 API。
4. `e2e/tax-i-shell.spec.ts`：
   - 5 个身份 × 1366/1920/390/375/683，唯一可见 `h1`、身份条、无横向溢出、主行动/任务在首屏内。
   - 断言岗位说明位于任务列表之后（修复前会失败）。
   - 截图 `tmp_test/tax-i/*.png`。
5. 回归：
   - `e2e/qa-accessibility.spec.ts` 3 passed/13.7s。
   - Firefox/WebKit 各 1 passed（accountant，1366/1920/390/375/683）。
6. 质量门：
   - `npm run typecheck` 0。
   - `npm run lint` 0 error，4 个既有 unused eslint-disable warning（A–H 面板）。
   - `npm run build` 成功；预算 passed：initial 565541/175199、CSS 99722；home 593043/185753、CSS 144373。
   - `npm run test:frontend-budget` 6/6 passed。
   - `npm test -- src/tasks0712.test.ts src/monthly-stage-guide.test.ts` 13 passed。

## 未完成

- 200% 浏览器缩放只用 683px 等效布局覆盖，未做真实浏览器 zoom 自动化。
- 真机 iOS/微信、人工屏幕阅读器、真实客户多身份切换未验证。
- 共享样式中的旧散落值未全量迁移；没有新增无必要组件。
Open objectives: I 收口；J–M 随后（m22738）。
