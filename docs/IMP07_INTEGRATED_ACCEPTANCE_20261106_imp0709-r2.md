# IMP07 整合验收（2026-11-06 / imp0709-r2）

状态：本地工程与质量/成本契约通过；真实模型效果未验证。

## 已有能力复用

- `tools/imp07_eval/` 合成困难票语料（30 来源 / 120 变体，20 dev / 10 holdout）
- `scripts/imp07_build_corpus.py`、`scripts/imp07_run_eval.py`
- H01/AG05/RSI、`tools/assistant_replay/`、FakeProvider、现有候选/门禁
- 非另建评分器；成本契约作为薄模块接入现有 `score`

## 本轮增量

- 新增 `tools/imp07_eval/costing.py`：按 `attempt_id` 去重、同 ID 异内容冲突、缺失费用保持 unknown、显式零保留、失败 attempt 保留已知费用；`compare_cost` 在币种不一致、任一侧不完整或基线为零时拒绝比较。
- `tools/imp07_eval/__init__.py`：`score` 接收 `attempts`，输出 `cost_summary` 与明细 `attempt_summary`；Markdown 呈现费用完整性。
- 新增 `nexus_backend/tests/test_imp07_costing.py`、`test_imp07_contract.py`。
- 实际输入契约：用真实 `AssistantProvider` + `httpx.MockTransport` 捕获请求，校验无 tools/tool_choice、strict schema、system 只读/不授权、user 只含受控字段、不含 gold/candidate、指纹稳定。

## 证据

- `test_imp07_costing.py + test_imp07_contract.py + test_imp07_eval.py` → **15 passed / 0.63s**
- JUnit：`tmp_test/imp-integrated/imp0709-20261106-r2/imp07-tests.xml`
- 三份报告：`tmp_test/imp-integrated/imp0709-20261106-r2/imp07/`（good `accepted_offline`、regressed `rejected`、unknown `incomplete`）
- 全量助手回放：`assistant-replay/summary.json` **status=passed，353 passed**，answer 3/3，`real_model_evaluated=false`；助手候选 `44dbe7e7…`。

## 07H / 07S 对照（诚实状态）

- 07H-T03/T04/T05/T06/T07：成本与固定分母契约已有本地反例测试。
- 07H-T01/T02/T08：由既有 IMP07 测试/评分器覆盖，本轮复跑 15 passed；未逐条新增同名测试。
- 07H-T09/T10：全量助手回放尚未收结果；unknown 不重发由既有通知/attempt 回归覆盖。
- 07S-T01/T02/T03：实际 request 捕获与权限边界已覆盖。
- 07S-T04/T05/T06：覆盖/弃答/视觉能力声明由报告字段与既有语料约束；用户负担指标尚未形成独立量化报告，标 pending。
- 07S-T07：金标不进 provider、草稿未审核由实际 request 捕获与既有测试覆盖。

## 未完成 / 外部

- 真实模型、真实供应商成本与延迟：pending。
- 真人负担分钟数、真实 OCR 视觉推理：pending。
- 全量 assistant replay 已完成：353 passed；非真实模型。
