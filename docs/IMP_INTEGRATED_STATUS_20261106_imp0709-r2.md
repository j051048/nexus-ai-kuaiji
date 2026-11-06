# IMP07–IMP09 整合执行状态（imp0709-r2）

基线 HEAD：`d88b792c46c967be62a44301943b9a94248ec9c6`
运行目录：`tmp_test/imp-integrated/imp0709-20261106-r2/`

| 包 | 当前状态 | 本轮证据 | 剩余 |
|---|---|---|---|
| IMP07-A | 本地通过 | 15 passed；353 assistant replay；固定分母/成本/坏候选否决 | 真实模型 pending |
| IMP07-B | 本地通过（用户负担 pending） | 实际 request 捕获；提示词/权限/工具契约 2 项 | 负担量化、真实模型 |
| IMP08-A | 本地通过 | 13 passed；跨期/票据 coverage；结构化 handoff | — |
| IMP08-B | 本地部分通过 | observation 6 passed；无写入/幂等/撤权/预算/unknown | 真实订阅/持久游标；完整任务卡链路 |
| IMP08-C | 部分通过 | PG16/17 正向；桌面真实 API 2 passed + 手机真实 API 1 passed，0 写请求；撤权不缓存测试 | 同一调用内撤权/旧租约交错 |
| IMP09-A | 既有实现复验 | unified worker 18 passed（含慢后台不阻塞助手） | 交互优先容量两臂 |
| IMP09-B | 部分通过 | PG17/PG16 30min soak passed（1933）；HTTP 50 100/100 但延迟不达标（SQLite p50 13.07s；共享连接池对照 p50 10.92s）；前端预算 passed | 多节点/生产；HTTP 延迟目标 |

## 当前候选
- HEAD `d88b792c46c967be62a44301943b9a94248ec9c6`；source 1933 / `8565dfcd119c70f3afa825e20e7262482154ec4e29ffc01d4e976b3a0390a6fb`；migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`；P01 `p01-1933-r4` 在上一候选 passed（183/2/4）；新候选未重跑。

- source：1933 / `8565dfcd119c70f3afa825e20e7262482154ec4e29ffc01d4e976b3a0390a6fb`
- migration：338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`

## 本轮修改白名单

- `tools/imp07_eval/costing.py`、`tools/imp07_eval/__init__.py`
- `nexus_backend/tests/test_imp07_costing.py`、`test_imp07_contract.py`
- `nexus_backend/app/assistant/analysis.py`
- `nexus_backend/app/assistant/observation.py`
- `nexus_backend/tests/test_imp08_analysis.py`、`test_imp08_observation.py`
- `src/AnalysisEvidencePanel.tsx`
- `e2e/imp08-analysis.spec.ts`、`e2e/imp08-analysis-real.spec.ts`、`playwright.imp08.config.ts`
- `nexus_backend/tests/test_unified_worker.py`
- `docs/IMP07_INTEGRATED_ACCEPTANCE_20261106_imp0709-r2.md`、`docs/IMP08_INTEGRATED_ACCEPTANCE_20261106_imp0709-r2.md`、本文件

## 未验证 / 外部

- 真实模型、真实通知、真人、真机、远端 CI、生产：pending/not_authorized。
- PG 动态授权交错、三类正向 PG：未完成。
- PG16 同候选 30min soak 已通过；HTTP 50 仅 SQLite 功能验证、延迟不达标；多节点/生产未验证。
