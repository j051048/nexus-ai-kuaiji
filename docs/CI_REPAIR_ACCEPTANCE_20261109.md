# 剩余 CI 红项本地修复验收（2026-11-09）

结论：格式、N02/N03 清单及原 33 项浏览器失败已修复。本地默认配置四分片最终为 120 + 111 + 115 + 115 = **461 passed**；PG16/17、核心 dev/preview、任务 dev/preview 和跨浏览器门禁通过。保留第 4 分片前几次运行出现的偶发失败记录，尚未确定其根因；不宣称远端 CI 已通过或长期稳定性已证明。

## 范围与候选

本轮处理格式、N02/N03 专项门禁归属，以及远端报告的 33 个浏览器失败。开始时工作区干净，HEAD 为 `0c8ac11830b858d54f380f842cb2b41dcd648a73`；本轮修改未提交、未推送。没有访问生产、真实模型或真实通知；没有修改历史迁移、checksum、C01 实现或其他智能体修改。

代码候选（`scripts/release_candidate.py`）：

- 2016 个源文件，SHA-256：`43f71363605056aff3f48ed21c291543da5f07b6e38cda7d92adeeeeb89dc2aa`
- 349 个迁移相关文件，SHA-256：`5ac57554c2b7e68f0cbc126f4a54b3c7481db18b208d8be114a0edf71729024f`
- 本轮全量回归期间代码候选保持不变。本文和伴随 JSON 是验收结果记录，不属于上述代码指纹范围。

## 修复与失败证据

1. **格式**：先复现 Prettier 的 11 个文件失败，再按仓库配置格式化。未提高体积预算，也未修改格式门禁范围。
2. **N02/N03 清单**：`test_task_journey_postgres.py` 已由工作流交给独立 `tasks` 门禁执行，但测试中的专项目录未登记，造成假红。先得到 1 failed / 10 passed，再修正归属并新增 PG16/17、准确用例清单、禁止忽略失败的契约断言，12 项通过。没有删除任何必验用例。
3. **真实产品缺陷：交付待办只有前 8 项入口**。新增有界、稳定排序的服务端分页和前端上一页/下一页，沿用当前身份与逐项授权校验；办理返回保留分页。新增 SQLite、前端和 PG16/17 测试，覆盖后续账套、同名账套、他企隔离、受限复核人、撤权。新增 PG 用例加入 tasks 必验清单。
4. **真实产品缺陷：保存状态没有独立可访问名称**。录入页同时存在多个 status，旧测试选中多个节点。给草稿保存状态增加名称，保留 live/atomic，先写失败渲染测试再修复；浏览器按明确名称定位，没有用 `.first()` 掩盖歧义。
5. **测试配置遗漏**：标准隔离 E2E 未打开确定性只读调查开关，专项 harness 却已打开。统一隔离测试开关，并用 dev/preview 两个失败测试证明；模型端点和凭据仍清空，生产默认值未变。
6. **过时断言/夹具**：旧角色说明、税务说明、手机已收起的次操作、缺少授权范围的可观测性模拟响应，均改为断言现有明确业务语义。保留 401/403 清空、跨企业拒绝、独立复核、未审核不得导出、手机单一主操作等边界。
7. **测试操作遗漏**：多企业录入必须显式选择授权企业，不能恢复“默认猜企业”；复用一个断言明确企业名称的选择辅助方法。
8. **测试数据污染**：助手进度用例启动真实本地计时后未结束，污染后续试点计时用例并得到 409。改为在 finally 中先关闭本页心跳，再按所属身份、账套、最新版本结束自己创建的计时；保留并行计时拒绝规则。

原 33 项逐个按文件及完整标题与实际运行结果对照，全部有通过记录，没有缺失、换名替代或跳过。明细见伴随 JSON 的 `original_remote_failures`。

原 33 项分类：角色说明 1、保存状态 7、企业选择 7、调查测试开关 6、税务说明 2、手机主操作 2、观测响应夹具 4、计时污染 2、交付分页 2。

红—绿记录根目录：`tmp_test/ci-repair/20261109/`。保留 `n02-contract-red.xml`、`unit-red.json`、`feed-red.xml`、`feed-ui-red.json` 和 `red-intake` / `red-business` / `red-workspace` / `red-timer`；对应 green 记录及最终回归均保留，没有删除失败现场。

## 已完成回归

以下套件有交集，不能相加当作独立用例总数。

| 范围 | 结果 |
| --- | --- |
| 前端全量单测 | 1354 passed，0 skipped |
| 门禁配置与候选契约 | 174 passed |
| 交付待办、OCR 边界、费用与 attempt 相关后端 | 49 passed |
| PG16 / PG17 N02 + N03 | 每版 45 + 56 = 101 passed |
| PG16 / PG17 tasks | 每版 36 passed，含新增分页隔离及撤权用例 |
| PG16 / PG17 C01 fresh | 每版 34 passed |
| 核心浏览器 dev / preview | 每版 106 passed，准确用例清单验证通过 |
| 任务浏览器 dev / preview | 每版 20 passed，准确用例清单验证通过 |
| QA 浏览器 | 96 passed，Chromium / Firefox / WebKit / mobile-webkit |
| 完整浏览器默认四分片 | 461 passed；第 4 分片有此前偶发失败历史，见下文 |
| 原失败相关浏览器定向回归 | 33 + 32 + 18 passed |
| 格式 / lint / TypeScript / Ruff / diff | 通过 |
| 构建与体积门槛 | 通过，预算未变 |

两版 PostgreSQL 使用本地隔离新库、迁移及受限角色；门禁核验主版本、持久性配置、准确用例和无跳过，全部绑定上述候选。未使用 SQLite 结果替代 PG 授权与并发证据。PG16 为 160015，PG17 为 170011。四个由本轮启动的隔离 PG 集群已停止，测试数据保留用于排查，见 `environment-stop.json`。

核心浏览器报告：`test-reports/e2e-core/65a7468f-0ca1-471a-ba76-30f6ea04faea/reports/`。该旧 harness 使用含文档的全仓指纹 `31cf2ecaa0a09837fd33a7af20f7a60fbb03c6d40af2d1001c82b6a1a170ef2d`，dev/preview 一致；本文随后新增不改应用代码。

任务浏览器报告：`test-reports/e2e-tasks/27d688c8-41d2-4f04-a898-663b3e06a9f3/reports/`。桌面/手机交付待办截图见本地证据目录。

构建首屏预算：JS 565434 / 570000 字节；gzip 175143 / 176000 字节；CSS 99722 / 110000 字节。产物指纹 `05cbc5277832c3d44050145d236ce9fe3d247c13f7937551dd9c26cf7981a559`。常规 Vite 大 chunk 提示仍存在，不能把预算通过解释为无限性能余量。

## 全量浏览器运行记录与剩余界限

首次四分片：120、111、115、113 passed；第 4 分片有 2 个新增偶发失败。之后该分片两次完整执行分别为 114 passed / 1 failed，且失败落在不同用例。原 33 项在首次四分片中已全部通过。

新增偶发问题涉及：只读账簿选择、一次本地连接失败、导航夹具请求本地 API 超时、修订后返回详情为空。没有把它们删掉或标成可忽略；没有增加自动重试、超时、skip 或 continue-on-error。只读账簿追加桌面/手机各 5 次共 10 次通过；修订返回追加 12 次通过。部分失败 trace 在保存时超时，文件不完整；无法据此认定产品无竞态，也不能仅凭怀疑归因 Windows。

关闭 trace 的完整第 4 分片对照为 115 passed。随后恢复默认 `retain-on-failure` 再执行，`full-shard4-final` 为 **115 passed / 0 skipped / 0 unexpected**，耗时 4.6 分钟。最终使用 first-run 的第 1–3 分片及该默认配置完整第 4 分片，合计 461 passed；应用候选未变。**这不是首次四分片一次全绿，也不是对个别失败用例拼接通过**。关闭 trace 的运行只作诊断；没有修改仓库 trace 配置或据此认定故障原因。

全部运行统计、失败用例标题和报告摘要哈希见 `CI_REPAIR_ACCEPTANCE_20261109.json`。遗留风险是本地新增偶发失败的根因未确认；下一次远端执行仍应保持严格失败门槛，复现时继续定位请求、导航状态和追踪保存，不能改成自动重试掩盖。

## 复跑入口

运行前使用仓库虚拟环境，并由既有本地环境工具启动本轮隔离 PG 配置；不要替换为生产 DSN。每次指定新的输出目录，保留旧失败证据。

```text
npm run format:check
python -m ruff format --check nexus_backend/app services/api_gateway scripts
npm run lint
npm run typecheck
npm test -- --reporter=json --outputFile=<新输出目录>/frontend.json
npm run build
npm run test:frontend-budget
```

浏览器默认四分片分别执行 `npm run test:e2e -- --shard=1/4` 至 `4/4`。本轮在本机为同时启动的 harness 分配不同 `E2E_API_PORT` / `E2E_WEB_PORT`；数据库、原件目录仍由 runtime 每次随机独占。核心入口为 `npm run test:e2e:core`；任务入口为 `node scripts/run_e2e.mjs --mode both --suite tasks`；跨浏览器入口为 `npx playwright test --config playwright.qa.config.ts`。

对两个版本分别执行：

```text
python scripts/n02_n03_postgres_gate.py --config <隔离配置> --major <16或17> --out <新输出目录>
python scripts/dcp_reliability_gate.py --suite tasks --config <隔离配置> --major <16或17> --out <新输出目录>
python scripts/c01_postgres_gate.py --config <隔离配置> --out <新输出目录>
```

这些门禁自身会拒绝跳过或替换必验用例，C01 入口建立自己的新库。环境配置和凭据仅留在忽略目录，不纳入提交或公开报告。

## 发布边界

这是本地修复及回归记录，不是远端 CI 全绿或生产验收。未推送，远端尚未运行本轮候选；只有将来获准推送并完成远端工作流后才能更新远端结论。生产服务、数据库、模型和通知均未操作，企业隔离及会计复核后导出规则保持。
