# Q-S03B 月度包身份 / 查看恢复 / 深链接 — 本地验收记录（2026-10-27）

- 候选：`tmp_test/qr01-integration`；基线 HEAD `32e3042d`；未提交、未推送
- 范围：`src/MonthlyPackagePanel.tsx`、新增 `src/monthly-package-read.ts` + `src/monthly-package-read.test.ts`
- 边界：只改前端接纳与呈现；服务端对联系人、企业、版本的判定未动

## 1. 修复前的真实行为

| 场景 | 旧实现 | 风险 |
| --- | --- | --- |
| `POST .../open` 返回另一个包 / 另一企业的包 | 先 `setContents({id, files})`，再用 `validatePackage` 决定是否更新状态 | 文件清单会先被替换；校验失败时清单与包状态不一致 |
| 服务端 503 | `catch {}` 静默吞掉 | 客户不知道查看记录没写成功，也没有重试入口 |
| 深链接带旧包 ID | 没有读取链接参数，永远落到「最新未废止版本」 | 客户打开的是别的版本，属于静默替换 |

## 2. 新增契约（`src/monthly-package-read.ts`）

- `acceptPackage(updated, expected)`：先过 `validatePackage`（企业/账套/期间/哈希格式），再要求 `id` 与请求一致；调用方给出 `manifestSha256` 时还要求哈希一致。任一条不满足返回 `null`。
- `selectPackage(packages, wantedId)`：返回 `exact` / `superseded` / `missing`。缺失时**不回落**到最新版，文案写「不在当前企业或月份可见范围内，可能已被撤回」。
- `packageFromSearch(search)`：只接受 `?package=` 且形如 8–64 位十六进制/连字符，其他一律空串（不猜）。
- `readRecovery(status)`：503 → 可重试且「签收状态没有改变」；401/403 → 不可重试、需重新授权；404 → 不在可见范围。

## 3. 接线

- `track()` 改为「先接纳、后提交」：`acceptPackage` 通过后才同时写 `contents` 与 `state`；不通过则抛错并由 `readRecovery` 渲染，屏幕保持原样。
- 新增 `trackError` 状态与「重试读取」按钮；重试只重发读取（GET）或幂等的 `open`，不触发签收。
- `load()` 优先用 `focusPackageId` 或 `?package=` 选择目标版本；缺失时直接显示错误而不是回落；被取代的版本照常展示并提示「已被更新版本取代」。
- 404/403 的错误文案统一走 `readRecovery`，不再吞掉。

## 4. 测试（`src/monthly-package-read.test.ts`，7 例）

1. 只接受被请求的那个包（`id` 不同即拒）；
2. 其他企业、其他期间的响应被拒；
3. 期望哈希不一致被拒；不传哈希时按 `id` 接纳；
4. 旧链接找不到时返回 `missing` 且**不**回落最新版；
5. 被取代版本仍可选中并提示；
6. 503 可重试且文案声明「签收状态没有改变」；403 不可重试；
7. 深链接只接受形如包 ID 的参数。

## 5. 门禁

```
npx vitest run src/monthly-package-read.test.ts   → 7 passed
npx vitest run                                    → 975 passed
npx tsc -p tsconfig.app.json --noEmit             → 0 错
npx eslint src/MonthlyPackagePanel.tsx src/monthly-package-read.ts → 通过
npx prettier --check                              → 通过
```

## 6. 未做 / 边界

- 未做真实浏览器的「深链接 → 刷新 → 后退」验证（Q-S04 的核心 e2e 覆盖）。
- 服务端 `POST .../open` 仍是幂等写一条；前端重试不会产生第二条回执，但本轮没有新增服务端用例。
- 未改 `ClientPortalShell` 的路由参数：深链接从 `window.location.search` 读取，避免与共享文件冲突。
