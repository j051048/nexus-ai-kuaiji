# Q-S03A 前端范围 / 撤权 / 迟到响应 — 本地验收记录（2026-10-27）

- 候选：`tmp_test/qr01-integration`；基线 HEAD `32e3042d`；未提交、未推送
- 范围：`src/FormalBooksRangePanel.tsx`（接线）、新增 `src/range-read.ts` + `src/range-read.test.ts`
- 背景：仓库没有 jsdom / testing-library，面板无法在 vitest 里挂载；本轮把读生命周期抽成纯 reducer，组件只负责派发与渲染，行为由 7 条用例固定

## 1. 三条行为与修复

| 行为 | 旧实现 | 本轮 |
| --- | --- | --- |
| 切范围 | 只清 `balance`，`ledger`（明细）与提示保留，旧明细会挂在新日期下 | `switch` 立刻清空数据并置 `loading`；同时派发 `reset` 清掉明细 |
| 迟到响应 | 靠 `FormalBooksRequestGuard` 的 stamp + scope；A→B→A 快速切换时语义分散在组件里 | reducer 每条事件都校验 `key + ticket`；旧 ticket 一律丢弃 |
| 撤权（401/403） | 清 `balance`，但保留 `ledger`，且提示是通用「无权查看」 | `fail` 带 `status`，401/403 触发清空并给出「权限已变更…重新登录或联系事务所负责人」；scope 变化时派发 `revoke` 并清 `accountId` |
| 导出 | 只判断 `!balance`，范围变了但页面未刷新时仍可点 | `canExport(state, key)` 要求 `status==='ready'` 且 key 与当前范围/科目一致；不满足时给受控提示 |

## 2. 测试（`src/range-read.test.ts`，7 例）

1. 切范围立刻清空旧余额，且新旧 key 都不能导出；
2. 旧范围的迟到 `ok` 被丢弃（`state` 不变）；
3. A→B→A 时，第一次 A 的迟到响应不得覆盖，只有当前 ticket 的 A 响应生效；
4. 403 触发清空 + 撤销文案，禁止导出；
5. 普通 500 保留原错误文案，同样清空数据、禁止导出；
6. 切范围丢弃已下钻的明细（`reset`）；
7. `revoke` 后迟到响应被丢弃、不可导出，文案含「权限已变更」。

## 3. 红绿证据

把 reducer 换回旧语义（`switch` 保留 `data`、403 保留 `data`）后：

```
npx vitest run src/range-read.test.ts
→ 5 failed | 1 passed（6 例时）
```

恢复实现后（当前 7 例）：

```
npx vitest run src/range-read.test.ts      → 7 passed
npx vitest run src/range-read.test.ts src/formal-books → 50 passed
npx vitest run                              → 96 files / 967 tests passed
npx tsc -p tsconfig.app.json --noEmit       → 0 错
npx eslint src/FormalBooksRangePanel.tsx src/range-read.ts src/range-read.test.ts → 通过
npx prettier --check                        → 通过
npm run build                               → ✓ built in 6.06s
```

## 4. 未做 / 边界

- 真实浏览器里的迟到响应、撤权与 A→B→A 仍要在 Q-S04 的核心 e2e 里跑；本轮只固定逻辑层。
- 撤权后是否要自动跳转登录页，取决于既有会话恢复流程（`src/api.ts` 的 `accounting:unauthorized` 事件），本轮不新增跳转。
- 明细被清空后需要重新点击科目行，未做「自动重取同一科目」。

## 5. 改动文件

- 新增：`src/range-read.ts`、`src/range-read.test.ts`、本文件
- 修改：`src/FormalBooksRangePanel.tsx`
