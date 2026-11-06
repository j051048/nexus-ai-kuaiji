# IMP 执行检查点

- 更新时间：2026-11-06
- 当前包：IMP01-B（已完成本地组合；同候选 P01 已通过）
- 当前 HEAD：`143d4aea7154b1905b96ab757ecd14865354b641`
- P01：1927 同候选 passed（183/2/4）
- 当前候选：source 1927 / `e6a947031fe9e3b4bd23b522b29ef46bbfddd89232a5c9d989b70a170f44baa5`；migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`；冻结 `tmp_test/imp/imp-20261106-a/candidate-final.json`
- 工作区：56 项 IMP 代码/测试/文档与输入文档；未提交、未推送

## 已完成

1. IMP01-A：候选/门禁基线，69 passed；影响矩阵、状态台账、验收记录。
2. IMP05-A：`scripts/imp05_trial_summary.py`、6 项契约测试、试用包、空记录返回 `incomplete/no_records`；真人 pending。
3. IMP06-A：员工/财务/会计/手机关键页计算样式、截图、布局基线；预算重建后 passed。
4. IMP02：删除装饰英文/重复“当前工作台” → 13 单测 + 8 浏览器（5 身份 × 4 视口）通过；员工 heading 72→44px。
5. IMP03：行内说明折叠、空/UUID 标题兜底、财务首屏压缩；78 单测 + 2 浏览器通过；桌面首条待办 top 634px。
6. IMP04：手机录入首屏、短视口焦点、显式企业选择；100 单测 + 6 浏览器（375/390/1366）通过；employee-flow 9 passed；金额 y 由 1218.58→719.28，底 765.28 < 吸底条 767。
7. IMP06-B：12px 可操作文字、金额列扩宽、长/负金额不裁切；IMP06 4 + axe 3 + IMP02–04 回归 16 passed。
8. IMP05-B：36 项协同/版本浏览器 passed（85.16s）；旧包 409/错版本不升级、未知通知不重发、独立复核；真人 pending。
9. IMP07：30 来源/120 变体合成困难票；7 契约测试；good `accepted_offline`、bad `rejected`、unknown `incomplete`；真实模型 pending。
10. IMP08：费用跨期与票据凭证差异只读分析；8 契约测试；UI 1 浏览器（0 写请求）；PG16/17 各 1 smoke；真实模型未调用；正向票据差异仅 SQLite。
11. IMP10：通知展示/DB 事件边界只读复核；无代码/迁移；SQLite 3 + PG16/17 各 5 passed；历史 checksum 未变。
12. IMP09：PG17/PG16 各 30min/200 企业 soak passed；同机双 worker，非多节点。
13. IMP01-B：最终候选 1927 冻结；Python 90 passed、浏览器 21 passed、typecheck/build/budget passed。

## 正在运行

- 无本任务长任务；本地 API 8000、preview 8082 可继续用于复验（job `bash-3777b62ed2d24e73`、`bash-8038e576584147c2`）。
- PG16 IMP09 已完成：job `bash-d4a7df9b1c8a4fe9`，报告 `tmp_test/imp/imp-20261106-a/imp09-capacity-pg16-soak/report.json`。

## IMP02 证据

- 验收：`docs/IMP02_ACCEPTANCE_20261106.md`
- 改前/改后：`tmp_test/imp/imp-20261106-a/shell-before/`、`shell-after/`
- 浏览器：`tmp_test/imp/imp-20261106-a/imp02-browser.json`、`imp02-results/`
- 单测：`npx vitest run src/workspace-home-focus-render.test.ts src/account-identity-render.test.ts`（13 passed）

## IMP03 证据

- 验收：`docs/IMP03_ACCEPTANCE_20261106.md`
- 单测：`npx vitest run src/workspace-home-focus-render.test.ts src/task-presentation.test.ts`（78 passed）
- 浏览器：`tmp_test/imp/imp-20261106-a/imp03-browser.json`（2 passed）；截图 `imp03-desktop.png`、`imp03-mobile.png`

## IMP04 证据

- 验收：`docs/IMP04_ACCEPTANCE_20261106.md`
- 单测：`npx vitest run src/intake-guidance.test.ts src/workspace-home-focus-render.test.ts src/task-presentation.test.ts src/account-identity-render.test.ts`（100 passed）
- 浏览器：`tmp_test/imp/imp-20261106-a/imp04-browser.json`（6 passed）；截图 `imp04-intake-before.png`、`imp04-intake-after.png`
- 既有员工流：`npx playwright test e2e/employee-flow.spec.ts --config=playwright.config.ts`（9 passed）
- 体积：initial 565,907B/gzip 175,004；home 593,430B/gzip 185,575；CSS 144,323B（passed）

## IMP07 证据

- 验收：`docs/IMP07_ACCEPTANCE_20261106.md`
- 语料：`nexus_backend/tests/fixtures/imp07_eval/corpus.json`（30 来源/120 变体，dev 80/holdout 40）
- 契约：`.venv/Scripts/python.exe -m pytest -q nexus_backend/tests/test_imp07_eval.py`（7 passed）
- 报告：`tmp_test/imp/imp-20261106-a/imp07/{good,regressed,unknown}/report.json|md`、`summary.json`
- 结论：good 120/120 `accepted_offline`；bad 119/120 `rejected`（2 回归）；unknown 0/120 `incomplete`（120 未知、16 安全失败）；真实模型/真人 pending

## IMP08 证据

- 验收：`docs/IMP08_ACCEPTANCE_20261106.md`
- 契约：`.venv/Scripts/python.exe -m pytest -q nexus_backend/tests/test_imp08_analysis.py`（8 passed/24.53s）
- UI：`npx playwright test --config=playwright.imp08.config.ts`（1 passed/11.4s）；截图 `imp08-analysis.png`
- 组合回归：`test_imp08_analysis.py + test_assistant_task_workflow.py + test_assistant_task_baseline.py`（35 passed/44.26s）
- 体积：initial 565,956B/gzip 175,047；home 593,479B/gzip 185,602；CSS 144,373B（passed）
- 结论：确定性金额/差额、覆盖/缺失、claim 事实校验、UI 无写入通过；PG16/17 各 1 smoke；正向票据差异仅 SQLite；真实模型 pending

## IMP09 证据（PG17/PG16）

- 验收：`docs/IMP09_ACCEPTANCE_20261106.md`
- PG17：`tmp_test/imp/imp-20261106-a/imp09-capacity-pg17-soak-r2/report.json`；200 企业/批、2 worker、8 并发、1800s；导出 13.554s/200、建包 34.132s/200；持续读 34392；p95 1.17/0.59/0.59s；0 写/回滚/连接失败/锁等待。
- PG16：`tmp_test/imp/imp-20261106-a/imp09-capacity-pg16-soak/report.json`；导出 23.720s/200、建包 33.706s/200；持续读 32736；p95 1.23/0.62/0.62s；0/0/0/0；结束 pool 3/4 可用，进程随后退出。
- 两库均未证明长期无泄漏；同机双 worker 不是多节点。

## 下一步（精确）

1. 1927 同候选三阶段恢复已通过（183/2/4；PG16/17 八项全 true）；保留 `tmp_test/imp/imp-20261106-a/imp01b-p01-1927/`。
2. 按最终模板汇总 11 包状态与剩余外部门槛；不提交/推送/部署。
3. 真人、真机、真实模型/通知仍 pending。

## 已知风险

- 员工首页未知计数 `—`/“数量暂未汇总”仍是真实问题（IMP03/06 未改该未知语义）。
- IMP04 的上传中断、草稿跨刷新、提交结果未知 UI 未取得浏览器证据，报告中已如实列为限制。
- IMP08 正向票据差异仅 SQLite；PG 仅 smoke。
- 全量 Accounting foundation 仍有未迁移夹具（另案），不能据此宣称全仓绿。
- 真人、真机、真实模型、真实通知、远端 CI 均 pending；本轮不提交/推送/部署。
