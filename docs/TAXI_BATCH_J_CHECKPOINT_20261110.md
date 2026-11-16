# 批次 J 检查点（2026-11-12）

## 工程状态

- 本地实现：进行中／部分通过。
- 外部验收：不适用（仅本地虚构数据）。
- 当前未提交、未推送、未部署。

## 已完成

1. J1/J8 分页与稳定行号：`JournalEntryGrid` 每页 50，绝对行号，定位行；516 行实测 11 页。
2. J2/J07 宽屏并排与手机堆叠：`InlineReviewDrawer` 原件/分录双列，390 堆叠；新增几何断言。
3. 真实浏览器链：516 行草稿生成、打开、定位第 516 行、独立复核通过；截图落盘。
4. 岗位摘要去重：I 批改动保留；B06 角色首页回归通过。
5. 中文组合态：定位行与助手 Ctrl/Cmd+Enter 均忽略 `isComposing`。

## 失败与处理

- J02 绝对“当前页合计/全量”标签：未新增独立标签；当前页只显示行范围，不显示合计。若后续加合计，必须同时显示“当前页/筛选范围/全量”。
- J03 稳定 ID：打开草稿使用 draft id；跨页选择集合的服务端冻结未在本批扩展，未宣称支持跨页全选。
- J04 换企业/撤权清空旧选择：沿用既有 `useScopedQuery`/范围键；未新增专项浏览器断言。
- J05 旧版本/混合资格：服务端拒绝由既有 API 覆盖；本批未重跑全部负向。
- J09 批量部分成功：既有 batch operation 测试通过；本批未重写。
- J10 手机底栏遮挡：516 行抽屉在 390 下可滚动，未测软键盘弹起。

## 最近命令与结果

- `npx playwright test --config playwright.tax05.config.ts` → 2 passed/18.0s。
- `npx playwright test e2e/tax-i-shell.spec.ts --workers=1` → 5 passed/27.8s。
- `npx playwright test e2e/qa-accessibility.spec.ts` Chromium → 3 passed/13.7s。
- `npx playwright test e2e/employee-flow.spec.ts e2e/b06-role-home.spec.ts --workers=1` → 10 passed（employee 9 + B06 1；修复 sr-only 岗位标签后）。

## 未完 / 下一批

- 未建立 J 的 PG16/17 专项浏览器链；已复核导出由既有测试覆盖。
- 列宽拖拽/密度保存、跨页客户端选择、6MB PDF 绑定已补；真实 PDF 内容渲染与服务端冻结集合未做。
- 进入 K 前不宣称 J 全部完成；K 的既有员工链已回归。
