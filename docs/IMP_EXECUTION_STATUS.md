# IMP 执行状态台账

- 基线日期：2026-11-06
- 执行文件：`docs/DEEPSEEK_IMP01_IMP10_EXECUTION_20261106.md`
- 基线 HEAD：`143d4aea7154b1905b96ab757ecd14865354b641`
- 基线工作树：仅 2 个本任务输入文档未跟踪；已跟踪文件无修改
- 基线候选：source 1905 / `b5959882e70a04beea56bfc3ed9a6efdab725cd4c1b90497b8193a4db227300f`；migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`
- 运行目录：`tmp_test/imp/imp-20261106-a/`
- 原则：每包单独更新；不以旧候选证据填充当前状态；外部门槛一律 pending，不写成通过

## 状态总表

| 包 | implementation | local | pg16 | pg17 | browser | real_device | real_user | real_model | remote_ci | production | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| IMP01-A 基线/门禁 | passed | passed | not_applicable | not_applicable | not_applicable | not_applicable | not_applicable | not_applicable | pending | not_applicable | 69 passed；候选/证据门禁复用 |
| IMP05-A 试用基线 | passed | passed | not_applicable | not_applicable | not_applicable | pending | pending | not_applicable | pending | not_applicable | 试用包+空记录契约 6 passed；真人 pending |
| IMP06-A 样式基线 | passed | passed | not_applicable | not_applicable | passed | pending | not_applicable | not_applicable | pending | not_applicable | 计算样式/截图已采集；真机 pending |
| IMP02 岗位外壳 | passed | passed | not_applicable | not_applicable | passed | pending | pending | not_applicable | pending | not_applicable | 13 单测 + 8 浏览器；四个视口 |
| IMP03 列表/文案 | passed | passed | not_applicable | not_applicable | passed | pending | pending | not_applicable | pending | not_applicable | 78 单测 + 2 浏览器；行折叠/标题兜底/首屏任务 |
| IMP04 手机录入/核验 | passed | passed | not_applicable | not_applicable | passed | pending | pending | not_applicable | pending | not_applicable | 100 单测 + 6 浏览器；375/390/1366；多企业必须选择；上传/提交未知 UI 未验 |
| IMP05-B 协同回归 | passed | passed | not_applicable | not_applicable | passed | pending | pending | not_applicable | pending | not_applicable | 36 浏览器 passed/85.16s；旧包/错版本/接手边界；真人 pending |
| IMP06-B 规范收口 | passed | passed | not_applicable | not_applicable | passed | pending | pending | not_applicable | pending | not_applicable | 4 浏览器 + axe；关键页范围，未全仓 |
| IMP07 评测补强 | passed | passed | not_applicable | not_applicable | not_applicable | not_applicable | not_applicable | pending | pending | not_applicable | 30 来源/120 变体；7 契约测试；good/bad/unknown 三报告；真实模型 pending |
| IMP08 只读分析 | passed | passed | passed | passed | passed | not_applicable | not_applicable | not_applicable | pending | not_applicable | 8 契约 + 1 浏览器；PG16/17 各 1 smoke；正向票据差异仅 SQLite；真实模型未调用 |
| IMP09 性能/容量 | passed | passed | passed | passed | not_applicable | not_applicable | not_applicable | not_applicable | pending | not_applicable | PG17/PG16 各 30min/200企业通过；同机双 worker，不是多节点；PG16 pool 3/4 退出前快照 |
| IMP10 DB 职责试点 | passed | passed | passed | passed | not_applicable | not_applicable | not_applicable | not_applicable | pending | not_applicable | 无代码/无迁移；10-T 表征；PG16/17 各 5 passed；历史 checksum 未变 |
| IMP01-B 最终组合 | passed | passed | passed | passed | passed | pending | pending | pending | pending | not_authorized | 最终候选 1927/`e6a94703…`；Python 90 + 浏览器 21 + typecheck/build/budget；P01 同候选 passed（183/2/4，PG16/17 八项全 true） |

## 当前候选

| 时点 | HEAD | source | migration | 说明 |
|---|---|---|---|---|
| 2026-11-06 基线 | `143d4aea` | 1905 / `b5959882…` | 338 / `2c96acd5…` | 含现有 205 PG + 133 SQLite 迁移 |
| 2026-11-06 IMP02 后 | `143d4aea` | 1909 / `7886e47a…` | 338 / `2c96acd5…` | 新增 IMP02 测试/基线脚本/试用脚本；迁移未变 |
| 2026-11-06 IMP03 后 | `143d4aea` | 1911 / `9c8cb0c5…` | 338 / `2c96acd5…` | 新增 IMP03 浏览器配置/spec；行折叠与财务首屏压缩 |
| 2026-11-06 IMP04 后 | `143d4aea` | 1914 / `eefe2271…` | 338 / `2c96acd5…` | 手机录入重排、显式企业选择、短视口焦点；6 浏览器 |
| 2026-11-06 IMP06-B 后 | `143d4aea` | 1916 / `e058e841…` | 338 / `2c96acd5…` | 关键页字号/金额列收口；IMP02–04 与 axe 回归通过 |
| 2026-11-06 IMP05-B 后 | `143d4aea` | 1916 / `b00de1db…` | 338 / `2c96acd5…` | 角色链夹具显式企业更新；36 浏览器 passed；真人 pending |
| 2026-11-06 IMP07 后 | `143d4aea` | 1921 / `9e85d77f…` | 338 / `2c96acd5…` | 合成语料 30/120、固定分母评分器、good/bad/unknown 报告 |
| 2026-11-06 IMP08 后 | `143d4aea` | 1927 / `e6a94703…` | 338 / `2c96acd5…` | 只读差异分析、HTTP 开关、UI 面板；8 契约测试 + 1 浏览器；PG16/17 各 1 smoke；正向票据差异仅 SQLite |
| 2026-11-06 IMP01-B 最终 | `ec63c24e` | **1927 / `e6a94703…`** | **338 / `2c96acd5…`** | 冻结于 `tmp_test/imp/imp-20261106-a/candidate-final.json`；P01 同候选 passed；已推送 `origin/main` |

## 已确认的现有能力（复用，不重建）

| 能力 | 当前位置 | 结论 |
|---|---|---|
| 候选指纹与离线证据校验 | `scripts/release_candidate.py`、`scripts/check_release_candidate.py` | 已有；负向测试覆盖候选漂移、假 summary、PG16/17 串证、JUnit 伪造 |
| 浏览器聚合入口 | `scripts/run_e2e.mjs`、`playwright*.config.ts` | 已有；按需使用 |
| P01 恢复门禁 | `scripts/p01_recovery_gate.py` | 已有；外部环境按需启动 |
| PG 隔离运行器 | `scripts/test_accounting_postgres_isolated.py` | 已有 |
| 前端预算 | `scripts/frontend_budget.mjs` | 已有；重建后 `initialGzip=175004`、`homeGzip=185575`，预算通过 |
| 角色导航/身份 | `src/workspace-presentation.ts`、`src/AccountIdentity.tsx` | 已有；IMP02 只做压缩与测试 |
| 员工短清单/草稿/退回 | `src/WorkspaceHome.tsx` 等 | 已有；IMP03/04 复用 |
| 客户服务端分页 | 客户/任务接口 | 已有；IMP09 不重建分页 |
| React.lazy | 路由 | 已有；IMP09 只测闭包与热点 |
| H01/AG05/RSI/assistant replay | `tools/` 与测试 | 已有；IMP07 增量 |
| 通知 unknown/attempt | 通知与月包模块 | 已有；IMP10 只读盘点后决定 |

## 外部门槛（本轮不冒充）

- 真人试用：pending，需 3–5 名真实参与者与记录表。
- 真机：pending，需 iOS/Android/微信内置浏览器实机。
- 真实模型/OCR：pending，本轮只用归档/FakeProvider。
- 真实通知/供应商：pending，本轮只用本地替身。
- 远端 CI：pending，本轮不推送。
- 生产发布：not authorized。

## 变更日志

| 日期 | 包 | 变更 |
|---|---|---|
| 2026-11-06 | IMP01-A | 建立基线、候选、影响矩阵、工具负向测试 |
| 2026-11-06 | IMP05-A | 试用包、空记录分析器、6 项契约测试 |
| 2026-11-06 | IMP06-A | 关键页面计算样式、截图、布局基线 |
| 2026-11-06 | IMP02 | 删除装饰英文/重复“当前工作台”；五身份四视口通过 |
| 2026-11-06 | IMP03 | 行内说明折叠、标题兜底、财务首屏压缩；78 单测 + 2 浏览器通过 |
| 2026-11-06 | IMP04 | 手机录入首屏/短视口/企业选择；100 单测 + 6 浏览器；employee-flow 9 passed |
| 2026-11-06 | IMP06-B | 12px 可操作文字、金额列扩宽、长/负金额不裁切；4 浏览器 + axe 通过 |
| 2026-11-06 | IMP05-B | 36 项真实/契约协同链 passed；旧包不静默升级、未知不重发；真人 pending |
| 2026-11-06 | IMP07 | 合成困难票 30 来源/120 变体；固定分母与关键回归否决；离线三报告；真实模型 pending |
| 2026-11-06 | IMP08 | 费用跨期/票据凭证差异只读分析；8 契约 + 1 UI 浏览器；真实模型未调用；PG16/17 smoke |
| 2026-11-06 | IMP09 | PG17/PG16 30min/200企业 soak；同机双 worker；非多节点 |
| 2026-11-06 | IMP10 | 无代码/无迁移；通知读侧 DB 职责表征；PG16/17 各 5 passed |
| 2026-11-06 | IMP01-B | 最终候选 1927 冻结；Python 90、浏览器 21、预算通过；P01 同候选 passed（183/2/4） |
| 2026-11-06 | IMP10 | 通知展示/DB 事件边界复核；不新增迁移；PG16/17 各 5 passed；历史 checksum 未变 |
| 2026-11-06 | IMP09 | PG17/PG16 各 30min/200企业 soak passed；导出200/13.554s与23.720s、建包200/34.132s与33.706s；持续读 p95 1.17/1.23s |
