# IMP07–IMP09 整合补强基线（2026-11-06-r2）

- 分支：`main`
- HEAD：`d88b792c46c967be62a44301943b9a94248ec9c6`
- 工作树：干净
- 基线候选：source 1927 / `e6a947031fe9e3b4bd23b522b29ef46bbfddd89232a5c9d989b70a170f44baa5`
- 迁移：338 / `2c96acd52de191347a2365962e879340918fad013786faa260b4b13140646116`
- 运行目录：`tmp_test/imp-integrated/imp0709-20261106-r2/`

## 归属与已有能力

| 能力 | 当前位置 | 本轮处理 |
|---|---|---|
| IMP07 合成语料与评分 | `tools/imp07_eval/`、`scripts/imp07_*`、`test_imp07_eval.py` | 复用；补全成本/提示词输入契约 |
| IMP07 任务回放/RSI | `tools/assistant_replay/`、`tools/ai_inference_lab/rsi/` | 复用，不另造评分器 |
| IMP08 两类分析 | `nexus_backend/app/assistant/analysis.py`、`router.py` | 复用并修分页/总额/凭证口径 |
| IMP08 UI | `src/AnalysisEvidencePanel.tsx`、`BookWorkspaceShell.tsx` | 复用；补小卡片/接手投影 |
| 接手 | `nexus_backend/app/handoff/service.py`、`src/HandoffDialog.tsx` | 只读复用，不新增写路径 |
| 统一调度 | `nexus_backend/app/unified_worker.py` | 已有助手优先/单后台槽；补保护测试，不重写 |
| PG/浏览器门禁 | `scripts/test_accounting_postgres_isolated.py`、现有 Playwright 配置 | 复用 |

## 本轮明确缺口（先红测）

1. IMP07 没有从真实 `AssistantProvider`/ContextPack 组装处捕获实际请求，只有影子 prompt 检查。
2. IMP07 评分只接受逐样本 `cost_cny`，没有 attempt 去重、缺失/未知 usage、失败成本与完整/不完整总额语义。
3. IMP08 跨期总额与明细分页 coverage 未区分；上期明细超过一页时仍可能显示完整。
4. IMP08 票据差异只取借方、来源页金额求和，缺少全量来源总额和借贷平衡检查。
5. IMP08 没有结构化任务卡/接手投影；观察模式没有隔离的纯诊断契约。
6. IMP09 只有顺序 `run_once` 测试，没有 `run_tick` 的交互优先、预算/公平与释放保护测试。

## 修改白名单

- `tools/imp07_eval/`
- `nexus_backend/tests/test_imp07_eval.py`、新增 `test_imp07_contract.py`
- `nexus_backend/app/assistant/analysis.py`、`router.py`
- `nexus_backend/tests/test_imp08_analysis.py`、新增相关测试
- `src/AnalysisEvidencePanel.tsx`、`src/analysis-evidence.css`
- `e2e/imp08-analysis.spec.ts`、`playwright.imp08.config.ts`
- `nexus_backend/tests/test_unified_worker.py`、新增 IMP09 交互公平测试（仅在证明代码缺口时改 `unified_worker.py`）
- 本轮 `docs/IMP07_IMP09_*` 验收/检查点

不修改：历史迁移、支付/计费语义、provider 发送、权限政策、C01、IMP02–06/10 范围。

## 基线测试

- `test_imp07_eval.py + test_imp08_analysis.py`：15 passed / 20.50s（JUnit 见运行目录 `baseline.xml`）。
- 既有 `test_unified_worker.py` 6 项顺序/预算/停止测试通过（本轮将补并发）。
