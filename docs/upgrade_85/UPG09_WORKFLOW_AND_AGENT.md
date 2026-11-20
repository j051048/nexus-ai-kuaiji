# Batch 09：配置化可视化工作流中台与 PLT08 调查助手 60 样本闭环

- **先读**：`docs/upgrade_85/UPG00_MASTER.md`、`docs/pilot/PLT08_ASSISTANT.md`、`docs/rework/RWK06_PLT_REMAINING.md`（RWK06-E）、`nexus_backend/app/assistant/actions.py`、`analysis.py`、`routing.py`、`src/AnalysisEvidencePanel.tsx`
- **前置**：Batch 02 + Batch 05 完成
- **提分目标**：补齐企业级可视化工作流编排与版本发布能力，同时完成 PLT08-A~E 60+ 样本防幻觉闭环评测（将维度 10 从 6.5 拉升至 8.7+）

---

## 1. 实施任务

### 1.1 轻量级声明式工作流编排与版本管理中台
- 新增双库前向迁移（SQLite + PG + `FORCE RLS`）：
  - `workflow_definitions`（按 `firm_id / company_id / process_kind` 管理版本化 DAG 定义、状态 `draft / active / archived`、节点规则 JSON：自动直通点/人工确认点/金额分流阈值/超时升级策略、发布人、内容指纹）；
  - `workflow_instances` & `workflow_step_logs`（运行实例锁定的定义版本、当前节点、异常挂起原因、人工接管与安全回退留痕）；
- 实现 `nexus_backend/app/workflow/engine.py`：
  - 将四类核心财务流程（费用报销、资金付款审批、规费差异处置、月结预检发布）接入版本化声明式配置；
  - 支持**金额/风险条件分支**（低风险直通草稿、超额升级双人审批）、**超时自动升级主管**、**异常挂起与人工接管/安全回退**（已正式过账或已提交银行/税局的节点硬禁止回退，强制走冲销流）；
  - 严格版本隔离：**发版升级后，存量运行中实例继续按启动时版本走完，新发起实例启用新版本**；支持一键回滚至历史已批准版本；
- 新增按需 `lazy()` 加载的前端组件 `src/WorkflowStudioPanel.tsx`：采用轻量化原生 SVG 渲染可视化流程图（不引入大体积第三方图形库，严守首屏预算），支持查看节点规则、调整阈值/审批人、版本 Diff 对比、一键回滚及异常实例人工接管台。

### 1.2 完整交付 PLT08-A ~ E（调查助手防幻觉与 60+ 样本评测）
严格按 `docs/pilot/PLT08_ASSISTANT.md` 原文实现：
- **PLT08-A（六段固定结构 + 服务端防幻觉强改写）**：输出必须包含固定六段（结论、确定性事实、缺失材料、来源引用、建议动作卡、交接对象），全部金额与标识由服务端填入；**必测红绿场景：当模型桩擅自输出“这是银行手续费”但缺乏本期已核验依据时，服务端必须拦截并改写为“原因未确认/待核对”**；
- **PLT08-B/C（有界动作卡扩展）**：在 `actions.py` 既有的不可变预览 + 64 位指纹 + 显式确认 + 幂等键护栏下，新增面向财务的高频动作卡（如一键拉起高风险复核队列、生成缺件催办草稿）；
- **PLT08-D（三类受控记忆五条硬约束）**：将企业规则记忆、口径历史记忆、会话短期记忆严格分表隔离，逐条写测试验证：① 绝不跨法人泄漏；② 核算政策换版自动失效；③ 撤权后即时不可见；④ 严禁覆盖权威账务事实；⑤ 全量审计可删除；
- **PLT08-E（≥60 条独立留出评测集 + 独立候选对照协议）**：在 `nexus_backend/tests/evals/plt08_holdout/`（与开发样本完全分离）沉淀 ≥60 条封存样本（归因正确、缺件拒答、跨期拒用、跨法人防泄漏 4 类各 ≥15 条），评测脚本分别输出五项独立指标（不设虚假达标线、不合黑盒总分）；并且 A/B/C 对照必须支持**各组基于不同记忆配置独立生成候选输出后再由统一盲评计分器比较**（不再只用固定脚本硬编码结果），清晰区分离线替身对照与真实模型待测项。

---

## 2. 验收命令与通过标准

```text
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_workflow_engine_versioning.py nexus_backend/tests/test_plt08_full_suite.py -q
.venv/Scripts/python.exe -m pytest nexus_backend/tests/test_pg_sql_hygiene.py -q
npm run typecheck && npm run lint && npm test -- --run && npm run build && npm run test:frontend-budget
```

- 另须通过 `WorkflowStudioPanel` 的 Playwright E2E（1366/390：查看可视化流程、发布新版本、存量实例保旧版、异常节点人工接管）；
- 更新 `docs/pilot/PLT_STATUS.md` 与 `docs/rework/UPGRADE_85_STATUS.md`。
