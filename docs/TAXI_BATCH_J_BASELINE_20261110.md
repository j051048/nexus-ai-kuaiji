# 批次 J 基线（2026-11-12）

## 范围与依赖

- 依赖 I：单壳、单导航、身份条、范围绑定、岗位说明后移已通过；见 `docs/TAXI_BATCH_I_ACCEPTANCE_20261110.md`。
- 选定本地试点链：规费批次来源 → 批次草稿 → 独立复核 → 受控导出。浏览器夹具使用合成 516 行（258 个借贷对）与规费 2 行。
- 复用：`ReviewWorkbench`、`InlineReviewDrawer`、`JournalEntryGrid`、`ReviewPackageWorkbench`、`useReviewFilters`、`useScopedQuery`、复核/原件 API。

## 起始事实

- HEAD：`781fd93499dcba7bb76056f0febe19e372202af2`。
- 起始候选（I）：source `2153 / 0d02bb67b7a26ede809196f55ad52ffa5f0d895ae6d7aa5a17c98db373f76c50`；migration `377 / 12c7e6ec4d91bc67dbc4a26b4749eda39514e0f0ca20ebf37dd3fb40d200d745`。
- 工作区：批次 A–I 及本轮前端改动均未提交；不 reset/clean/stash，不覆盖其他任务修改。

## 已有能力（保留）

- `JournalEntryGrid` 已有受控大凭证分页：每页 50 行、上一页/下一页、绝对行号、定位行。
- `InlineReviewDrawer` 已在宽屏提供原件与凭证双列；`inline-review.css` 在窄屏堆叠。
- `ReviewWorkbench` 使用稳定 draft id 打开/返回，不按数组下标。
- 来源/版本/授权由既有 API 校验；前端不重算金额、不补零、不自动重试 unknown。

## 本批未从零重建的内容

- 未新增第二套 reviewer、未替换表格库、未引入 UI 依赖。
- 未改后端金额、复核、导出、权限或数据库迁移。

## 既有失败/未覆盖（起始）

- 无自动化断言证明宽屏抽屉“原件与分录同时可见”，也无 516 行真实浏览器分页证据。
- 未系统覆盖 Firefox/WebKit、IME 组合态、手机底栏遮挡。
- 本批不宣称 J 全量完成；仅按实际执行结果更新验收。
