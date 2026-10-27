# Q-S03C 复核资格接线 — 本地验收记录（2026-10-27）

- 候选：`tmp_test/qr01-integration`；基线 `32e3042d`；未提交、未推送
- 范围：`src/review-authorization.ts`（新）、`src/review-authorization.test.ts`（新）、`src/ReviewWorkbench.tsx`
- 后端未改：`can_review`、制单责任、政策版本仍由 `nexus_backend/app/accounting/review_policy.py` 判定

## 1. 修复前的问题

`src/review-eligibility.ts` 早就存在，但**没有任何组件调用它**（只有它自己的测试）。真正的复核按钮由 `ReviewWorkbench.tsx` 的 `reviewPolicyReady` 控制，而它只检查「每条 ready 项的 `can_review` 与制单人是否等于自己」，不看：

- 预览是否已过期（`preview.expires_at` 只显示、不生效）；
- 授权来源是否被误当成「列表读到了」（`review-eligibility.ts` 的入参 `authorization` 由调用方口头约定）；
- 单人办理是否需要用独立提示（只在填写理由时被服务端 422 拦）。

## 2. 修改

**新增 `src/review-authorization.ts`**

| 函数 | 作用 |
| --- | --- |
| `reviewAuthorization(items, userId)` | 只读服务端返回的 `review_responsibility`：任一 ready 项 `can_review=false` 或 `state='unattributed'` → `denied`；全部为真 → `granted`；缺数据/无登录 → `unknown`（**不**按「能读到」推断） |
| `previewExpired(expiresAt, now)` | 缺失或不可解析一律视为过期 |
| `singleOperatorNote(items)` | 单人办理需要理由，并说明「不属于独立复核」 |
| `responsibilitySummary(value)` | 展示用摘要，区分未落定 / 已独立复核 / 已复核但单人办理 |

**`ReviewWorkbench.tsx` 接线**：`reviewEligibility` 现在被真实调用，`authorization` 来自上面的服务端投影，`previewExpired` 由 15s ticker 驱动（预览到期后按钮自行收回），`canConfirm` 同时约束按钮的 `disabled` 与 `confirm()` 的处理函数；政策未通过、资格未知、预览过期分别给出不同文案；单人办理时显式提示非独立复核。

## 3. 测试

`src/review-authorization.test.ts`（11 例，全绿）：

1. 服务端说不能复核 → `denied`；
2. 本人制单在独立复核下被拒 → `denied`；
3. 制单责任人未落定 → `denied`；
4. 缺制单责任数据 / 空列表 / 无登录 → `unknown`（不当作有权限）；
5. 全部可审项为真才 `granted`；任一为假即 `denied`；
6. 未通过检查的项不参与授权判定；
7. 预览失效时间为空/不可解析 → 过期；到点即过期、未到点有效；
8. 单人办理要求理由且提示非独立复核；独立复核不要求；
9. 摘要区分未落定 / 已独立复核 / 已复核但单人办理。

## 4. 门禁

```
npx vitest run                                    → 98 files / 986 tests passed
npx tsc -p tsconfig.app.json --noEmit             → 0 错
npx eslint src/ReviewWorkbench.tsx src/review-authorization.ts → 通过
npm run format:check                              → 通过
npm run build                                     → ✓ 3.16s（主包 605.81 kB）
```

## 5. 未完成：浏览器级「点击真实组件」验证被阻断

工作令要求「测试须点击真实组件并检查实际请求」。本轮尝试后**放弃**并把未经验证的改动回滚，原因有据：

- 侧栏入口在 `src/AgentNavigation.tsx:245-252` 的折叠组「会计工作区」内，且整组受 `capabilities.accounting` 控制；打开后 `跨企业批量复核` 仍不可点（DOM 见 `test-results/.../error-context.md` 快照）。
- 直接打开 `/review` 路由可渲染工作台（`src/workspace-route.ts:44` 的 `workspaceUsesBook` 已把 `review` 列为不依赖账套），但「复核企业」下拉的数据来自待审列表（`src/ReviewWorkbench.tsx:475` 的 `list.companies`），新建企业 + `POST /suggestions` 之后仍不在列表里，未能稳定复现「生成预览 → 确认」链路。
- 既有 `e2e/stage11-review-packages.spec.ts` 不在核心清单（`scripts/e2e_core_cases.json` 只含 `stage12-inline-review`、`v03-entry-review`），且在本候选上**已因同一导航折叠而失败**——这是既有红灯，不是我这一轮引入的。
- 因此：`e2e/stage11-review-packages.spec.ts` 的临时断言与新增 `e2e/qs03c-review-authorization.spec.ts` 均已回滚/删除，避免留下未验证的测试。

## 6. 核心 e2e（正式 runner）

```
node scripts/run_e2e.mjs --mode dev     → 106 passed / PASS / 0 gate issues（4.5m）
node scripts/run_e2e.mjs --mode preview → 106 passed / PASS / 0 gate issues（4.4m）
```

第一次 dev 跑出 `SOURCE_CHANGED`（我在跑动中写了本报告，指纹在开始时与结束时不一致），未改任何代码后重跑即 PASS——这是流水线按设计拒绝「运行期间源码变动」，不是产品缺陷。

## 7. 结论与剩余

- 「复核资格来自服务端、且真正约束按钮与处理函数」已实现，纯函数与接线单测覆盖；核心 106 项 e2e 全绿说明**既有**复核链路未被破坏。
- **浏览器级「确认复核按钮」点击证据仍缺**：入口在折叠组内且企业下拉依赖待审列表，未能稳定构造可点击场景（见第 5 节）。这条不能算完成，需在 Q-S04 里连同入口可达性一起处理。
- 后端未改，故 PG16/17 未重跑。
