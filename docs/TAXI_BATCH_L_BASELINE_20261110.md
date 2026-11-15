# 批次 L 基线（2026-11-12）

## 依赖

- I：壳层/岗位说明后移/首屏；J：分页、并排原件、手机堆叠；K：员工链通过、财务交接链部分失败。
- 复用现有 dialog、focus 恢复、`prefers-reduced-motion`、`InlineReviewDrawer`、I/J 测试与预算门禁；未引入新 UI 库。

## 已有且通过

- 原生 dialog 焦点进入/恢复：`InlineReviewDrawer` 保存触发点并恢复。
- reduced-motion：`styles.css`、`workspace.css`、`agent-shell.css` 等已有 `prefers-reduced-motion` 分支。
- 状态/错误/unknown：既有 `role=status`/`alert` 与复核状态。
- 跨浏览器自动无障碍：Chromium/Firefox/WebKit 各 3 passed。

## 已有但不足

- L01 IME：此前定位行/助手仅直接判断 `isComposing`，没有共享低层判断；本轮已抽 `isCompositionKey`。
- L02 多层弹窗：既有原生 dialog 处理，但未新增嵌套专项。
- L03 unknown/重复点击：已有后端幂等/前端忙态，未全量重测。
- L06 助手事实边界：既有只读校验，未新增真实模型流式测试。
- J/K 未收口，L3 不能称完整同候选集成通过。

## 本轮改动

- `src/keyboard.ts`：`isCompositionKey` 统一 `nativeEvent.isComposing || keyCode===229`。
- `JournalEntryGrid`、`GlobalAccountingAssistant`、`AccountingContextAssistant` 改用共享判断。
- `src/keyboard.test.ts` 2 tests。
- 无布局/颜色/动效 token 改动。
