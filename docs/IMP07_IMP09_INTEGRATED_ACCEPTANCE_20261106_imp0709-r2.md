# IMP07–IMP09 整合验收（2026-11-06 / imp0709-r2）

## 结论

- **IMP07-A/B：本地通过**。合成评测集 30 来源/120 变体；坏候选、固定分母、成本/attempt、请求权限契约均有测试；全量助手回放 353 passed。`real_model=pending`。
- **IMP08-A/B：本地通过；IMP08-C：部分通过**。费用跨期、票据/凭证差异、结构化 handoff、只读 observation 均落地；SQLite 13+6 测试、组合 46 passed；真实 API 桌面/手机浏览器 3 passed、0 写请求；PG16/17 已补正向费用/票据，动态撤权/旧租约交错仍未完成。
- **IMP09-A：既有能力复验通过**；**IMP09-B：部分通过**。PG17/PG16 30 分钟持续负载均 passed；HTTP 50 并发功能可用但延迟不达标（SQLite p50 13.07s）；同机双 worker，不是多节点。

## 候选

- HEAD：`d88b792c46c967be62a44301943b9a94248ec9c6`
- source：**1933 / `5d06a20516d49d579923dffa504504efef142f21653517dc19bdc14f56836374`**
- migration：**338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`**
- 运行目录：`tmp_test/imp-integrated/imp0709-20261106-r2/`

## 实际修改

- `tools/imp07_eval/costing.py`：成本/attempt 汇总与比较；unknown 不归零、冲突拒绝。
- `tools/imp07_eval/__init__.py`：评分接入 cost/attempt，报告增完整性。
- `nexus_backend/tests/test_imp07_costing.py`、`test_imp07_contract.py`、`test_imp07_eval.py`：固定分母、坏候选、请求契约。
- `nexus_backend/app/assistant/analysis.py`：费用/票据 coverage 全量 vs 明细、结构化 handoff、无记录文案、claim 校验、重授权。
- `nexus_backend/app/assistant/observation.py`：只读 `observe`/`ObservationLedger`；撤权/预算/unknown/冲突。
- `nexus_backend/tests/test_imp08_analysis.py`、`test_imp08_observation.py`：12 + 6 测试。
- `src/AnalysisEvidencePanel.tsx`、`e2e/imp08-analysis*.spec.ts`、`playwright.imp08.config.ts`：两类分析入口、真实 API 测试、0 写。
- `nexus_backend/tests/test_unified_worker.py`：慢后台不阻塞助手槽。
- 验收/状态/检查点文档（本文件、IMP07/08/09 专项、STATUS、CHECKPOINT）。

## 命令与证据

```bash
pytest -q nexus_backend/tests/test_imp07_costing.py test_imp07_contract.py test_imp07_eval.py
# 15 passed；JUnit tmp_test/imp-integrated/imp0709-20261106-r2/imp07-tests.xml
python -m tools.assistant_replay --out .../assistant-replay --timeout 1800
# status=passed, 353 passed, real_model_evaluated=False
pytest -q test_imp08_analysis.py test_imp08_observation.py test_assistant_task_workflow.py test_assistant_task_baseline.py
# 46 passed；JUnit .../imp08-tests.xml
npx playwright test --config=playwright.imp08.config.ts
# 2 passed；真实 API + mock 两条；0 写请求
pytest -q test_unified_worker.py test_ak05_reliability.py
# 18 passed
node tools/capacity/firm_operations.py --companies 200 --seconds 1800 ...
# PG17 status=passed；报告 .../imp09-pg17-soak-r2/report.json
```

## 外部门槛 / 未完成

- 真实模型、真实 OCR 供应商、真实通知、真人、真机：未授权/未验证。
- PG16 同候选 30 分钟 soak 已通过；HTTP 50 仅 SQLite 功能验证且延迟未达标；多 API/worker 多节点、生产网络未验证。
- IMP08 正向费用/票据 PG16/17 已补；桌面与手机真实 API 成功链已通过；同一调用内动态撤权/旧租约交错仍未完成。
- HTTP 50 复用连接池对照 p50 10.92s，确认瓶颈在 SQLite/服务端串行，不是客户端连接创建；目标未达标。
- 远端 CI、生产发布：未执行。
- 最终同候选：HEAD `d88b792c46c967be62a44301943b9a94248ec9c6`，source 1933 / `8565dfcd119c70f3afa825e20e7262482154ec4e29ffc01d4e976b3a0390a6fb`，migration 338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`；`tmp_test/imp-integrated/imp0709-20261106-r2/candidate.json`。
- P01 完整恢复在上一候选 `a8cfee…` 的 `p01-1933-r4` **passed**；新增同一调用内复核后，`new-pair` 首次因 target16/17 未启动失败；启动四集群后 pair `d37c635b…` 创建成功。`p01-8565-r1` regression 183/155.99s、joint 2/86.867s 通过，identity 4 failed（`RuntimeError: 本机GoTrue启动超时`）；GoTrue 私有日志显示已在 WSL `127.0.0.1:25511` 启动，30 秒健康检查未通过，问题在 Windows→WSL 25511 转发链路，故新候选仍无 P01：regression 183/152.589s、joint 2/84.48s、identity 4/59.946s，0 skipped；candidate `a8cfee6494c29472…`。此前 r1/r3 失败因 WSL auth 代理仅绑 127.0.0.1，属环境失败，已保留证据。
- 未提交、未推送、未部署；未改历史迁移 checksum。
