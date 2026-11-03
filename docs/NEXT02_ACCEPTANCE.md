# NEXT02 验收记录：真实浏览器主链

- 工程状态：**最终候选单期主链通过**；连续两期、跨年、多银行浏览器场景仍未完成。
- 外部验收：不适用（全部本地）。
- 最终候选：1821/`57bb7b70…`（HEAD `0a0c6cab…`、迁移 328/`8bbd6f3f…`）。2026-11-03 在同一候选、fresh fixture、真实本地 GoTrue＋PG17＋preview 上重跑 **8 passed / 0 failed / 0 skipped，28.8s**。

## 本轮实际完成

同一候选、同一 fresh fixture、同一真实本地环境，一次 Playwright 运行 **8 passed / 0 failed / 0 skipped，28.8s**：

| 用例 | 视口 | 结果 |
| --- | --- | --- |
| P02 real PG signed identity month-close desktop | 1440×1000 | passed |
| P02 real PG signed identity month-close mobile | 390×844 | passed |
| delivery and acknowledgement desktop | 1440×1000 | passed |
| delivery and acknowledgement mobile | 390×844 | passed |
| initial 403 clears loading and permits explicit reread | 默认 | passed |
| revoked late write cannot restore data or success | 默认 | passed |
| foreign company book and reports stay invisible | 1440×1000 | passed |
| client identity cannot review, lock or batch export | 1440×1000 | passed |

覆盖两家不同企业（fixture 内 desktop/mobile 两账套，期间均为 `2026-10`）、独立 maker/reviewer/client/foreign 身份、桌面与手机视口。

真实链路：GoTrue v2.197.0 登录 → `/api/me` 授权 → 真实 PG17 账簿读取 → 银行原件下载 → 银行/子账核对 → 独立复核报表 → 月结预检阻断 → 确认锁期 → 报表发布 → 月度包生成 → 客户查看与签收。业务成功响应未使用 `route.fulfill`；仅把合成 auth 域名转发到本机 GoTrue。

负向断言：
- 桌面复核员读取 mobile 企业账簿/报表/月度包均 404，页面显示错误告警，不渲染正式余额表、不出现 `9,731.90`。
- client 身份复核报表、锁定期间、批量复核导出均被拒绝（403/404），页面无“独立复核报表”按钮、无签收成功状态。
- 撤权后迟到写不复活数据；初始 403 清空 loading 且允许显式重读。

数据库结果（57bb fixture；与下方最终候选重跑同一轮）：
- `accounting.report_runs`：两家企业各 `reviewed v1` + `published v2`。
- `accounting.internal_close_runs`：两家企业各 `preflight_failed v1/v2` + `published v3`。
- `accounting.monthly_delivery_packages`：两家企业各 1 个包，manifest `73da4b7c…`、`0efbb8b2…`。
- `accounting.monthly_delivery_acknowledgements`：2 条，manifest 与包一致；`monthly_delivery_opens` 2 条。

## 最终候选重跑（2026-11-03）

- HEAD：`0a0c6cab4ba18d2dfe5ce695b6844e01895c204b`；source 1821 / `57bb7b7061c1b30d02ae4a825e411fc2fcf7e0c265e08a4337264b6be701c46a`；migration 328 / `8bbd6f3f…`。
- 环境：WSL Ubuntu-22.04；PG17 隔离集群 25498；GoTrue 25511；API 18100；`ACCOUNTING_API_PROXY=http://127.0.0.1:18100` 的 preview 18180；配置 run `tmp_test/next02-env/runs/4fb7aceea75d4232a2aaeb99c00705c9`（`P01_CONFIG_DIR` 指向同一 run）。
- fixture：`tmp_test/next/next02-browser-57bb/fixture-private.json`（fresh；两企业，期间 `2026-10`）。
- 命令：`P02_BROWSER_FIXTURE=… npx playwright test --config=playwright.p02.config.ts --reporter=line`，**exit 0；8 passed (28.8s)**。
- 终端输出：line reporter 8 passed (28.8s)；fixture `tmp_test/next/next02-browser-57bb/fixture-private.json`。
- 截图：`.codex/p02-r1-browser/results/` 下 `p02-blocked-*.png`、`p02-published-*.png`、`p02-package-*.png`、`p02-client-*.png`、`next02-client-readonly.png`、`next02-foreign-book-denied.png`；另存 `bank-original-{desktop,mobile}.csv`。

数据库结果（同一 fixture）：
- `report_runs`：两企业各 `reviewed v1` + `published v2`。
- `internal_close_runs`：两企业各 `preflight_failed v1/v2` + `published v3`。
- `monthly_delivery_packages`：2 个包，manifest `73da4b7c…`、`0efbb8b2…`；`acknowledgements` 各 1 条，包 ID 与 manifest 一致。
- 业务成功 API 未用 `route.fulfill`；仅把合成 auth 域名转发到本机 GoTrue。

## 本轮修改

- 新增 `e2e/next02-isolation.spec.ts`（跨企业不可见、client 不能复核/锁期/批量导出）。
- `playwright.p02.config.ts` 的 `testMatch` 纳入该 spec。
- `vite.config.ts` 在 `E2E_LOCAL_ISOLATION=1` 时额外构建 `e2e/fixtures/p02-close-harness.html`，使边界用例在 preview 产物下可运行；普通生产构建仍不包含测试夹具。
- 构建时 Windows 临时目录删除被拒，使用项目内 `TEMP/TMP=tmp_test/esbuild-tmp` 后通过；属环境问题，未改产品代码。

## 截图与包

- `tmp_test/next/next02-browser-final` 同级 Playwright 输出在 `.codex/p02-r1-browser/results/`：`p02-published-desktop.png`、`p02-published-mobile.png`、`p02-client-desktop.png`、`p02-client-mobile.png`、`p02-blocked-desktop.png`、`p02-blocked-mobile.png`、`next02-foreign-book-denied.png`、`next02-client-readonly.png`、`monthly-package-desktop.zip`、`monthly-package-mobile.zip`。

## 未完成／缺口（不得写成通过）

1. 连续两期：夹具每企业只种 `2026-10`，最终候选浏览器只在单期完成主链；下一期开账/结转/再月结未在浏览器验证。
2. 跨年：`nexus_backend/tests/test_p02_formal_read_postgres.py` 已在 57bb 候选补跑，PG17 `6 passed/55.72s`、PG16 `6 passed/54.35s`（`tmp_test/ak05-pg-runs/513feb5e…`、`020d465c…`）；覆盖跨年报表/未来科目/授权读写，但**未在浏览器验证**。
3. 多银行：夹具每企业只建 1 个银行账户；多银行覆盖未在浏览器验证。
4. 真机、微信、真实屏幕阅读器未验证。

## 下一步

- 最终候选 8 项单期主链已补；连续两期、跨年、多银行需新增夹具/用例，会再次改变候选，必须在下一候选统一重跑，不能把本轮 57bb 证据移用。
