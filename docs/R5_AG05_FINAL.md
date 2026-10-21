# R5 AG05：可信离线评分与检索验收

## 结论与边界

本轮 AG05 指定本地离线契约通过：119 项独立测试通过。
不代表真实模型质量、生产权限/RLS、生产接线、C01 或 AG01-R1 验收通过。

仅修改 `tools/ai_inference_lab/examples.py`、
`nexus_backend/tests/test_ag05_examples.py` 和本报告。
H01、AG02、AG03 源码只读复用。没有访问数据库、模型、网络或生产，
没有提交、推送、迁移。pytest 在独立临时目录复制已有虚构 H01
夹具并增加风险金标，未修改共享夹具。

## 红—绿证据

更改实现前增加 8 项失败用例：

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B -m pytest nexus_backend/tests/test_ag05_examples.py `
  --noconftest -p no:cacheprovider -q `
  -k 'test_r5_unbound_self_report or test_r5_retrieval_excludes or test_r5_retrieval_corpus or test_r5_reports_have' `
  --basetemp=tmp_test/r5_ag05_red
```

实际结果：`8 failed, 24 deselected`。均为业务断言：

- 两个公开评分入口接受无冻结身份的自报正确结果；
- test/validation/mystery/dev 四种非训练样例被检索；
- 派生自验收 ID 的训练样例未拒绝；
- 无可信规格的汇总可生成金融候选报告。

首轮修复与接口迁移后 `32 passed`。
扩大专项时，`test_r5_all_review_flags_do_not_mean_candidate_accepted`
实际失败：全部转人工仍返回 `accepted_offline`。
当次 `1 failed, 74 passed`。增加质量门槛后 `75 passed`，
再补 H01 正向、全弃答、空基线和候选冒用，最终 `79 passed`。
其余扩展用例是覆盖增强，不冒称全部都有旧实现红灯。

交叉审查后又先加入 24 项失败测试：

```powershell
.\.venv\Scripts\python.exe -B -m pytest nexus_backend/tests/test_ag05_examples.py `
  --noconftest -p no:cacheprovider -q `
  -k 'failure_is_not_expected or current_amount_cannot or truthy_permission or matching_empty' `
  --basetemp=tmp_test/r5_ag05_cross_red
```

实际 `24 failed, 79 deselected`，分别复现：执行失败冒充正确弃答 3 项、
当前金额被无证据常数计算替换 1 项、非布尔审核标志 4 项、
空白/错误类型范围身份 16 项。先收窄相应契约，再追加 12 项边界测试。
全回归发现旧“全部执行失败”用例仍预期 B1 正确弃答，按新明确契约
更正其断言为 B1 同样失败；没有删除该用例。

父审查最后补充 4 项红测：普通成功提取同时带弃答决定、
未知 kind、未知 reason 或显式 null decision，旧实现均忽略并通过。
实际 `4 failed, 115 deselected`。现在任何已声明 decision 都须满足完整
kind/reason/执行状态/空字段/空证据/转人工组合，否则记录
`domain_decision_invalid`、质量失败和绝对安全失败。
未声明 decision 的合法 H01 提取路径保留。

## 接口与可信输入

两个入口统一要求可信 `EvaluationSpec`：

```python
evaluate(baseline_rows, candidate_rows, spec=spec)
evaluate_samples(baseline_rows, candidate_rows, spec=spec)
```

不再保留可选 `required_ids/expected_scope` 的弱评分路径。
无 spec 返回 `evaluation_spec_required`。调用方检索只发现本测试，
无生产调用方；旧布尔测试改用完整 H01 夹具，保留有效验收语义。

`EvaluationSpec` 由可信 runner 在运行候选前确定：
H01 manifest 路径与固定 SHA-256；基线/候选分别指定
`CandidateConfig(candidate_id, policy_version, policy_prompt, context_version,
schema_version, mode)`。候选配置各有独立指纹。

每次评分必经 H01 `load_manifest` 与 `validate_result_identity`。
额外核验 `candidate_fingerprint/gold_sha256/source_hash`。
事务所、企业、账套、长期账套、期间、输入、请求指纹、策略提示词不可冒用。
SHA-256 检测变化，不证明作者身份。runner 的 spec 必须可信，不能接受候选
自报 spec。H01 `source_hash` 保持其既有冻结来源标识语义；
OCR/gold 有实际内容 SHA-256，不声称核验了未提供的原始图片/PDF。

## 金标、风险、分母

冻结 gold 增加：

```json
{"evaluation":{"risk_required":false,"abstention_expected":false}}
```

标签须为 bool；预期弃答须为风险样本；unsupported 样本须预期弃答。
缺风险标签明确拒绝，不由候选补标签。
金额/字段从输出与 gold 比较，证据从冻结 OCR 重算；
候选自报 `amount_correct/risk/risk_escalated/cross_company/evidence_complete`
均不作为分数依据。`review_required` 是被测路由动作，真值来自 gold。

统计 TP/TN/FP/FN、未观测、正确弃答。正常样本减少误报算修复；
漏报即使基线也有仍不通过。全部弃答/全部转人工不能蒙混质量门槛。

正确弃答必须是成功执行后的明确业务决定：
`status=succeeded`，`decision={"kind":"abstain","reason":"unsupported_case"}`，
字段和证据为空、转人工，并与冻结的预期弃答金标一致。
`failed`、超时/运输错误或带执行错误的矛盾 `succeeded` 均为执行失败，
保留在固定分母且不可比较；不能按“恰好为空”计作正确弃答。
本薄适配当前只支持这一种明确弃答原因，其他原因不会自动放行。

双侧 missing/unknown/执行失败均保持 manifest 分母，分别列 ID；
任一侧不完整为 `not_comparable`，空基线不能制造 improvement。
H01 继续拒绝 JSON `status=unknown`。可信 runner 的未知执行用独立
`UnknownAttempt` 信封，核验 attempt、样本、manifest/input/gold/source、
候选与请求指纹；不把未知 JSON 改成成功/失败绕过 H01。

输出分别含相对 `relative_result`、`absolute_safety_failed`、
`quality_failed` 和最终 `acceptance=incomplete/rejected/accepted_offline`。
`missing/unknown` 始终是 ID 列表，计数用 `*_count`，
保留并验证 E01 兼容报告修复。

## 检索与 ContextPack

只允许 train；test/holdout/validation/dev/未知集不召回。
核验企业/账套/供应商、审核状态、政策版本、生效范围和历史期间。
所有范围身份必须是非空文本；审核/证据完整标志必须是精确 bool，
`"false"`、`1` 不能按 Python 真值获得样例资格。
查询和样例同为空或错误类型不能构成范围匹配。
按当前/验收 ID、父来源 ID、来源摘要及祖先摘要排除同源泄漏，
改 ID 不能绕过。来源映射由可信 corpus 提供，哈希不会自动发现未登记祖先。

`compile_example_context` 使用真实 AG02 compiler：
当前事实保留当前 key/版本/证据；
历史金额使用 `history:<example_id>:gross_amount`、source=history、version=0。
`validate_context_suggestion` 仅把当前事实传给 AG03。
该薄适配进一步限定 claims 只能为 `source_fact`；
AG03 通用计算契约没有逐输入证据绑定，因此本适配拒绝任意常数计算、
推断及其他 claim kind，不将其视为当前事实替代方式。
这不改变 AG03 模块，也不声称实现了具有输入溯源的计算引擎。

实际组合：retrieve 历史 100 → 编译 ContextPack（当前仍为120）
→ 正确120引用通过 → 冒用100被特定 `claim_value_mismatch` 拒绝。
把历史100包装成 `50+50` 也由 `context_claim_kind_unsupported` 拒绝。
未修改 AG02/AG03，未产生业务凭证、审核或导出。

## 报告与最终验证

JSON 保留所有样本、双侧状态/原因、风险矩阵、范围/来源/input/gold摘要、
候选配置和最终验收。Markdown逐样本展示这些关键信息；
不再只有平均分或缺失数量，报告不触发发布。

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
.\.venv\Scripts\python.exe -B -m pytest nexus_backend/tests/test_ag05_examples.py `
  --noconftest -p no:cacheprovider -q --basetemp=tmp_test/r5_ag05_decision_final
```

实际结果：`119 passed in 3.84s`。
两个代码文件的 `git diff --check` 通过。

候选 SHA-256：

```text
examples.py
CEC8A5E8B58BA4F02540D1F1FA38C5FBDA1AA6884A4BD9AF7D6F172F446F1F3C

test_ag05_examples.py
6926C284731819B7F63392FC7538371280C0F608E6E281A672ED4D2CE93D78D5
```

未验证真实模型、真实企业、PG/RLS、生产接线和在线发布。
组合回归由父任务对最终共享候选执行，本报告不宣称远端 CI 已运行。
