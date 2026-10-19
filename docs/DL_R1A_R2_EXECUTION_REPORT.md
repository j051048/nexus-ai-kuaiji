# DL-R1A-R2 执行报告

## 1. 结论

**DL-R1A-R2 离线工程验收通过，具备重新评估 DL-R1B 的离线前置条件。**

限定：本地 mock/冻结数据；真实模型、生产权限、跨进程防重发、生产计费未验证。

## 2. 基线

- HEAD：`59764a9b`
- 起始候选哈希：
  - contracts `E7EB9643...`
  - replay `9314F141...`
  - test_ai_inference_contracts `9A9DD8A7...`
  - test_ocr_replay_engine `C76A22C7...`
- 工作区包含 C01、DL-R1B/R1C 未提交修改；本轮未回退、未覆盖。
- 本轮未修改 C01、生产 provider、队列、数据库、权限、记账、复核、导出。

## 3. 最终哈希

```text
contracts.py
E6DC402D96E9CF08D2543106C41D7B8F0BA31131C81C60D84AC8F5E389339575

selective.py
14188FD17A28517CD2312D907B3E1F0A9F1E366AE9F0103504D86F25F2B1429E

replay.py
B7D5F6650907B1AD57C08FBF3DF852DC866757CB5655B705B55364345AA401A0

test_ai_inference_contracts.py
44C1F8814EE46B8118C08D5AB399EEEE8AF2DA3B8B6F9077BD2C08FEF879D428

test_ocr_replay_engine.py
E981C5A13E9A1597E5C62FC947112A2A2737B57AC4BABA6AC3BDABCD68341701
```

## 4. 修复清单

### A 可信执行上下文

- `Executor` 缺 trusted/limits 时发送前失败；
- trusted endpoint 决定 local/cloud、model、能力、数据类别；
- 请求 `provider_is_local` 不再授予权限；
- scope/endpoint/model 不匹配拒绝；
- selective 已传显式 trusted + 冻结 limits。

### B 来源绑定

- `semantic_fingerprint` 覆盖 scope/input/schema/policy/provider/model/endpoint/预算/能力；
- 结果保存 `source`：request fingerprint、input、schema SHA、policy SHA、endpoint/model/attempt；
- H01 归档先验 `request_fingerprint/input_hash/policy_prompt`，再记录 imported 元数据；
- H01 compare 校验 run.json 策略与结果行；
- MockTransport 将响应绑定到 sample+input，同 sample 不同输入不能复用；
- 两企业相同 sample 不能互取。

### C attempt

- 同 attempt 同语义请求复用；
- 不同语义请求 `AttemptConflict`；
- 缓存命中前重新检查当前 trusted/policy，撤权后不返回旧成功；
- 内部异常也写入状态，不保留 `result=None`；
- unknown 不重发。

### D 回执

- 协议归一化前保留 usage/cost；
- choices=[]/非法 JSON/截断/拒绝仍保留已知回执；
- 零、非零、未知分开；
- 同 attempt 不重复计费；
- `cost_summary` 显示未知费用调用数；
- 发送后取消保留 usage/cost。

### E 冻结预算/时限

- limits 缺失拒绝；
- 请求只能收紧，不能提高；
- call/output/deadline 分别校验；
- 可注入 clock；
- 发送后超过 deadline 返回 TIMEOUT，保留 structured/usage/cost；
- 并发 attempt 共享调用预算，不超领。

### F schema

- 发送前递归预检完整 schema；
- 可选属性、空数组 items 的未知约束也检查；
- enum 必须是数组且类型敏感（`true` 不匹配 `1`）；
- 非法 pattern 发送前拒绝；
- 已支持 nested object/array/enum 正常通过。

## 5. A–F 测试映射

| 编号 | 测试 |
|---|---|
| A01/A02 | `test_a01_a02_missing_trusted_or_limits_fails_before_send` |
| A03 | `test_a03_scope_and_input_digest_are_verified` |
| A04 | `test_a04_wrong_endpoint_model_or_scope_is_rejected` |
| A05 | `test_normal_structured_response_and_known_usage_cost` |
| A06 | `test_a02_local_only_uses_trusted_endpoint_not_self_report` + selective tests |
| B01/B02 | `test_b01_b02_request_prompt_input_and_source_fields_are_bound` |
| B03 | `test_b03_result_row_must_match_run_metadata` |
| B04/B05 | `test_b04_b05_response_is_bound_to_sample_and_input` |
| B06 | `test_a01_archive_import_preserves_original_source_metadata` |
| B07 | `test_compare_reports_regression_and_fixed_field` |
| B08 | `test_path_traversal_and_hash_change_are_rejected` |
| C01 | `test_a08_unknown_attempt_is_not_resent_and_parameter_change_is_rejected` |
| C02 | `test_a09_concurrent_same_attempt_sends_at_most_once` |
| C03 | `test_c03_semantic_field_change_is_attempt_conflict` |
| C04 | `test_c04_cached_result_requires_current_authorization` |
| C05 | `test_c05_schema_preflight_has_zero_sends` |
| C06 | `test_c06_post_send_internal_exception_is_recorded_once` |
| C07 | `test_c07_different_attempts_share_frozen_budget` |
| D01/D02 | `test_d01_d02_known_receipts_on_bad_envelopes` |
| D03/D04 | `test_d03_d04_unknown_vs_zero_and_no_double_charge` |
| D05 | `cost_summary()["unknown_cost_calls"]` in D03/D04 |
| D06 | `test_d06_post_send_cancel_preserves_usage_and_cost` |
| E01/E02/E03 | `test_a10_frozen_limits_cannot_be_raised`、`test_call_budget...` |
| E04/E05 | `test_e05_post_send_deadline_uses_injected_clock` |
| E06 | `test_e06_concurrent_attempts_cannot_exceed_shared_budget` |
| E07 | normal success tests |
| F01/F02/F03/F05 | `test_f01_f02_f03_f05_schema_preflight_rejects_before_send` |
| F04/F06 | `test_f04_f06_enum_type_and_nested_schema_success` |

## 6. 复现证据

- 起始 DL01 候选允许省略 trusted/limits，`provider_is_local` 可绕过 local-only；
- 起始 H01 归档路径先写 `manifest_sha256` 再校验；
- 起始 attempt 指纹漏 schema/allowed_data/endpoint_id/scenario/output budget；
- 起始 schema 只做实例浅校验，choices=[] 丢失 usage/cost；
- 起始 post-send 无时限检查。

修复前失败已通过上述测试在缺陷路径上复现；最终候选统一通过。  
说明：部分 A01/B01 的“修复前命令日志”未单独留存，报告中以起始代码哈希和回归断言定位；未回退当前代码制造历史失败。

## 7. 回归

```powershell
pytest test_ai_inference_contracts.py test_ai_selective_review.py `
       test_ai_execution_trace.py test_ocr_replay_engine.py `
       test_ocr_field_evaluation.py -q
# 132 passed
```

分项：

- DL01：41
- DL02：19
- DL03：13
- H01：42
- OCR：17

无 skip/xfail；未使用真实网络/数据库/模型。

## 8. 未验证

- 真实供应商/模型；
- 生产 RLS/权限；
- 跨进程/重启 attempt 持久化；
- 生产计费；
- 真实发送后网络强制中断；
- 生产浏览器/导航。

## 9. 门槛

满足重新评估 DL-R1B 的离线前置条件。  
不表示 C01 完成、生产可用、真实模型有效或可发布。
