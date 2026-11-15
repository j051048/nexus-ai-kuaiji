# 前端 I–L 交接说明（2026-11-12）

## 可复用组件与约束

- 壳层：`WorkspaceHome` 唯一 h1、身份条、岗位/权限说明置于工作内容之后（`details`），参数化测试 `e2e/tax-i-shell.spec.ts`。
- 表格：`JournalEntryGrid` 每页 50、绝对行号、定位行；行身份使用 draft id，禁止按数组下标。
- 抽屉：`InlineReviewDrawer` 宽屏原件/分录并排、窄屏堆叠；焦点保存/恢复；关闭/返回不清空已保存草稿。
- 输入法：`src/keyboard.ts::isCompositionKey`，所有 Enter/快捷键入口应复用它。
- 复核：独立 reviewer 身份；未复核不导出；自审由服务端拒绝；unknown 不自动重试。
- 状态：`role=status`/`alert`、unknown/stale/撤权分离；不得把加载或未知写成 0。

## 运行命令

```bash
npm run typecheck
npm test -- src/keyboard.test.ts src/tasks0712.test.ts src/monthly-stage-guide.test.ts
npm run lint
TEMP=tmp_test/esbuild-tmp TMP=tmp_test/esbuild-tmp npm run build
npm run test:frontend-budget
npx playwright test e2e/tax-i-shell.spec.ts --workers=1
npx playwright test --config playwright.tax05.config.ts
npx playwright test e2e/qa-accessibility.spec.ts
npx playwright test e2e/qa-accessibility.spec.ts --browser=firefox
npx playwright test e2e/qa-accessibility.spec.ts --browser=webkit
```

## 候选与证据

- source `2156 / 9394785cfa30af65ce230ab0a0735beb72b7ccd2a3ac0436698e8a4e3c5d39ff`
- migration `377 / 12c7e6ec4d91bc67dbc4a26b4749eda39514e0f0ca20ebf37dd3fb40d200d745`
- 截图：`tmp_test/tax-i/*.png`、`tmp_test/tax05-large-voucher-*.png`、`tmp_test/tasks0712-20261107/screenshots/monitor-*.png`
- 预算 artifact：`ebff672da9b41c6eb635c96c0b310492fd911b07c2bb172b297688f63581b8f8`

## 未交出的工作

- K 财务交接链已通过（测试选择器缺陷修复后 3 passed）；J/K/L 仍不能宣称全部收口。
- 未接真实 T+/客户/模型/通知；未跑远端 CI；未提交、推送、部署。
- M 未启用，依赖仍为 React 18.3.1。

## 下一负责人的第一步

1. 补 J 的跨页冻结选择、列宽密度与大 PDF；补 K 的 OA 只补材料与 PG 子集；补 L02–L06。
2. 完成后做 J/K/L 的同候选 PG16/17 与真机项。
3. 只有明确“现在执行 M 批”才允许改 React 依赖。
