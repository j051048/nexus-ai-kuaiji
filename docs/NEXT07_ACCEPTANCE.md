# NEXT07 验收记录：弱网、移动端与纠错

- 工程状态：**本地验收通过（浏览器 25/25）**；真机、微信、屏幕阅读器人工验证未执行。
- 外部验收：未验证（无真机/辅助技术环境）。
- 候选：head `0a0c6cab…`，source `1821`/`57bb7b7061c1b30d02ae4a825e411fc2fcf7e0c265e08a4337264b6be701c46a`，migration `328`/`8bbd6f3f…`（57bb 最终候选；含并发任务的税务 UI 修改）。
- 环境：Playwright preview 构建，API 18000 + Vite 18080，fresh SQLite；Chromium/Firefox/WebKit + iPhone 13 视口；未调用真实模型/通知。

## 实际缺陷与修复

**迟到读取覆盖人工修订（真实产品缺陷）**

- 现象：WebKit 上 `stage10-revisions.spec.ts` 中修订金额已填 `80.00`，但“提交独立复核”一直 disabled，页面快照金额回到 `100.00`、“本次修改 0 个字段”。
- 定位：`e2e/stage10-revisions.spec.ts:90` 增加 `toHaveValue('80.00')` 后通过，证明填写成功后被异步重置；`DEBUG=pw:api` 与 trace 显示同事件 `openEvent` 被初始加载、手动“读取原事实”和 session 刷新重复触发，迟到响应无条件 `setPayload(data.payload)`。
- 修复：`src/RevisionWorkbench.tsx:57-66` 增加 `payloadRef/contextRef/reasonRef`；响应到达时若同一 `event_id` 且表单已有改动或依据，保留当前表单并提示“已保留当前修订内容，重复读取结果未覆盖表单。”，不应用迟到数据。保留 `openEpoch` 和 `openedEvent` 防重复。
- 未放宽断言、未跳过、未删除负向用例；测试新增的金额断言先红后绿。

## 命令与结果

| 套件 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| 跨引擎全套（57bb） | `E2E_FRONTEND_MODE=preview npx playwright test --config playwright.next07.config.ts` | **25 passed, 1.4m，exit 0** | `tmp_test/next/next07-browser/report.json`、`results/`（8475 时为 1.3m，同 25 项） |
| V07 恢复单跑 | 同上，`e2e/v07-context-recovery.spec.ts` | **6 passed, 32.3s** | 上述 report/results |
| 修订 WebKit 单跑 | 同上，`--project=webkit e2e/stage10-revisions.spec.ts` | **1 passed, 29.1s** | 上述 results |

覆盖：V07 首次 503 后真实重读授权（无写请求、无刷新列表空操作、手机无横向溢出）6；axe + 岗位首屏键盘可达 12；手机品牌/设置/导航文字可读 1；修订“财务提议→另一会计独立复核→旧草稿 100/新草稿 80→历史保留”3；其余为同一批次的引擎/视口组合。25 是执行项，不是 25 个互不重复的业务用例。

## 覆盖与缺口

- 已覆盖：3 引擎 + 手机视口的首屏/键盘/axe、V07 5xx 恢复、修订迟到响应不覆盖人工修改。
- 未覆盖：真实断网/高延迟注入、上传中断、提交后响应丢失的浏览器级核对、冲销/重新开期 UI、屏幕阅读器人工走查、微信内置浏览器、真机。
- 并发负载下曾出现 23/25（stage10 超时），已单独复跑并修复真实竞态；旧证据保留在 `tmp_test/next/next07-browser-report-under-load.json`，不作为最终通过。

结论：本地浏览器工程验收通过；NEXT07 的真机/辅助技术/真实弱网部分列为外部门槛，不能宣称全面通过。
