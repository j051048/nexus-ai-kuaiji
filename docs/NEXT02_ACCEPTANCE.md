# NEXT02 验收记录：真实浏览器主链

- 工程状态：进行中（浏览器单期主链本地通过；连续两期、跨年、多银行浏览器场景未完成）。
- 外部验收：不适用（全部本地）。

## 本轮实际完成

同一候选、同一 fresh fixture、同一真实本地环境，一次 Playwright 运行 **8 passed / 0 failed / 0 skipped，27.3s**：

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

覆盖两家不同企业（desktop 书 `1049419ca5e74d1c83437c904f805269`、mobile 书 `6f68ff83687341b58c17e67dc8f4f98b`，期间均为 `2026-10`）、独立 maker/reviewer/client/foreign 身份、桌面与手机视口。

真实链路：GoTrue v2.197.0 登录 → `/api/me` 授权 → 真实 PG17 账簿读取 → 银行原件下载 → 银行/子账核对 → 独立复核报表 → 月结预检阻断 → 确认锁期 → 报表发布 → 月度包生成 → 客户查看与签收。业务成功响应未使用 `route.fulfill`；仅把合成 auth 域名转发到本机 GoTrue。

负向断言：
- 桌面复核员读取 mobile 企业账簿/报表/月度包均 404，页面显示错误告警，不渲染正式余额表、不出现 `9,731.90`。
- client 身份复核报表、锁定期间、批量复核导出均被拒绝（403/404），页面无“独立复核报表”按钮、无签收成功状态。
- 撤权后迟到写不复活数据；初始 403 清空 loading 且允许显式重读。

数据库结果（同一 fixture）：
- `accounting.report_runs`：两家企业各 `reviewed v1` + `published v2`（`e73a8275…`、`443c68b9…`）。
- `accounting.internal_close_runs`：两家企业各 `preflight_failed v1/v2` + `published v3`。
- `accounting.monthly_delivery_packages`：两家企业各 1 个包，manifest `8df7cc028cf1…`、`bdbc360f826c…`。
- `accounting.monthly_delivery_acknowledgements`：2 条，manifest 与包一致；`monthly_delivery_opens` 2 条。

## 候选与环境

- HEAD：`df3fface95d1f69c28e859e89015465955c517b4`。
- 候选：`source_files=1794`，`source_sha256=585f345bcee2aa9a2179647f340cd7bac3471dec656eaedddbfe175836c81272`，迁移 316 / `f07ba1cd…`。
- 最终候选说明：上述 8 passed 是历史候选 1794 的证据，**未在最终候选 1816/`dddef1a8…` 重跑**；最终候选未重接 P02 GoTrue/PG17 浏览器环境。不得把这 8 项算作最终候选联合验收通过。
- 环境：WSL Ubuntu-22.04；PG16/17 隔离集群端口 25496–25499；GoTrue 25511；API 18100；前端构建产物 preview 18180。
- 运行目录：`tmp_test/next02-env/runs/74199e4ac77d49a9b6fd37bb8341986b`；fixture `tmp_test/next/next02-browser-final`；日志 `tmp_test/next/next02-final-run.log`；摘要 `tmp_test/next/next02-browser-final/evidence-summary.json`。
- 命令：`P02_BROWSER_FIXTURE=tmp_test/next/next02-browser-final/fixture-private.json npx playwright test --config=playwright.p02.config.ts --reporter=line`，退出码 0。

## 本轮修改

- 新增 `e2e/next02-isolation.spec.ts`（跨企业不可见、client 不能复核/锁期/批量导出）。
- `playwright.p02.config.ts` 的 `testMatch` 纳入该 spec。
- `vite.config.ts` 在 `E2E_LOCAL_ISOLATION=1` 时额外构建 `e2e/fixtures/p02-close-harness.html`，使边界用例在 preview 产物下可运行；普通生产构建仍不包含测试夹具。
- 构建时 Windows 临时目录删除被拒，使用项目内 `TEMP/TMP=tmp_test/esbuild-tmp` 后通过；属环境问题，未改产品代码。

## 截图与包

- `tmp_test/next/next02-browser-final` 同级 Playwright 输出在 `.codex/p02-r1-browser/results/`：`p02-published-desktop.png`、`p02-published-mobile.png`、`p02-client-desktop.png`、`p02-client-mobile.png`、`p02-blocked-desktop.png`、`p02-blocked-mobile.png`、`next02-foreign-book-denied.png`、`next02-client-readonly.png`、`monthly-package-desktop.zip`、`monthly-package-mobile.zip`。

## 未完成／缺口（不得写成通过）

1. 连续两期：夹具每企业只种 `2026-10`，本轮浏览器只在单期完成主链；下一期开账/结转/再月结未在浏览器验证。
2. 跨年：仅 `nexus_backend/tests/test_p02_formal_read_postgres.py` 有 HTTP/PG 层用例，本轮未在该候选复跑，也未在浏览器验证。
3. 多银行：夹具每企业只建 1 个银行账户；多银行覆盖未在浏览器验证。
4. 真机、微信、真实屏幕阅读器未验证。

## 下一步

- 先推进 NEXT03；若时间允许，回到 NEXT02 增加第二期间与第二银行账户的同候选浏览器用例，或明确降级为后续任务。
