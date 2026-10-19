# DL-R1A 执行报告：归档来源、可信身份与调用契约收口

## 1. 结论

**DL-R1A 离线来源与调用契约验收通过，可以进入 DL-R1B。**

限定：离线 mock/冻结 OpenAI-compatible 响应；真实供应商、真实网络、生产接入、重启后 attempt 持久保护未验证。

## 2. 基线

- 起始/最终 HEAD：`59764a9b`
- 工作区：H01/DL 未提交修改；C01 既有未提交内容保留
- 继承：H01-R1、DL01–DL03 未提交实现
- 本轮新增/修改：`tools/ocr_replay/replay.py`、`tools/ai_inference_lab/contracts.py`、`selective.py`、H01/DL01 测试、DL 文档
- 未修改 C01 服务/迁移/测试/报告、生产 provider、数据库、队列、权限、记账/复核/导出

## 3. 候选哈希

```text
replay.py
9314F1411C82E38BC7856C210F976468165EFCBA3EC4D550DA3941BCDFC43774

contracts.py
E7EB9643E23B4CA5A21085AD17AFB7762D8BE80BB52F381D31BCA1892646D0BA

selective.py
84B283EF741E32180F2D6B5C2888DB1864C667E4F9FF45C7CF9A984D1345AAE4

test_ocr_replay_engine.py
E00FC2BB3D5E402F3CEC0BFCB6EB233B1943CD08FEDCECA1C8D9B904F80B42E3
```

## 4. 复现与修复

### A01 H01 归档先覆盖身份

原实现先写入当前 manifest/mode，再校验来源，导致陌生归档可被重新盖章。  
修复：先 `validate_result_identity`、校验 `policy_version`，再单独记录 `imported_*`；保留原始 `manifest_sha256`/`mode`/`policy_version`。

测试：

- `test_a01_archived_manifest_and_policy_are_validated_before_import`
- `test_a01_archived_policy_mismatch_is_rejected`
- `test_a01_archive_import_preserves_original_source_metadata`

### A02 local-only 自报绕过

新增 `TrustedConfig`/`Endpoint`：local/cloud、provider、model、URL、能力、允许数据来自本地可信配置。  
Executor 不再信任请求 `provider_is_local`；请求 URL/provider/model/scope 不匹配直接拒绝，transport 调用为 0。

测试：`test_a02_local_only_uses_trusted_endpoint_not_self_report`。

### A03 错账套/期间/输入摘要

可信 scope 精确匹配 firm/company/ledger/book/period；输入必须提供实际 `input_text` 且 SHA-256 匹配；错误 attempt 不登记成功。

测试：`test_a03_scope_and_input_digest_are_verified`。

### A04 复用 sample/换输入/身份

attempt 绑定完整请求指纹（含 input_text、schema、scope、provider、model）。同 attempt 不同内容抛 `AttemptConflict`；归档结果先验来源，不复制当前身份。

测试：`test_a04_same_sample_with_different_input_cannot_reuse_attempt`、H01 `test_a01...`。

### A05 schema 浅校验

替换为递归最小 schema 校验：`type/required/properties/additionalProperties/items/enum/minimum/maximum/minLength/maxLength/pattern`；未知约束失败关闭。支持嵌套 required、additionalProperties、数组元素。

测试：`test_a05_nested_schema_required_additional_and_array_items`。

### A06 非有限数值

解析后递归 `_finite`，`1e9999`/inf/NaN 不进入成功结构化结果。

测试：`test_a06_overflow_numbers_are_never_success`。

### A07 失败时丢失 usage/cost

收到响应后先解析 usage/cost，再判断内容；拒绝/截断/schema 错误保留已知回执，缺失仍未知，显式零保持 known。

测试：`test_a07_failure_preserves_known_usage_and_cost`。

### A08/A09 attempt 与并发

- 同 attempt 同请求：返回原结果，unknown 不重发；
- 同 attempt 不同请求：`AttemptConflict`；
- Executor 内互斥锁覆盖检查、发送、登记；5 线程同 attempt 最多一次 `transport.send`。

测试：`test_a08_unknown_attempt_is_not_resent_and_parameter_change_is_rejected`、`test_a09_concurrent_same_attempt_sends_at_most_once`。

**限制：** 这是进程内登记，不是跨进程/重启持久保护；DL-R1B 负责持久化。

### A10 预算

`RunLimits` 在 Executor 创建时固定 call/deadline/output；请求调大被拒；输出上限非法、时间耗尽在发送前阻断。

测试：`test_a10_frozen_limits_cannot_be_raised`。

### A11 取消/超时

发送前取消、发送后取消、发送后超时/断连分别状态；响应已到时取消仍标记 cancelled 并保留 sent/received/usage。

测试：`test_a11_cancel_after_response_preserves_evidence`、`test_cancellation_before_and_after_send`、`test_timeout_unknown_does_not_retry`。

### A12 标准 OpenAI-compatible

新增 `normalize_protocol`：`choices[0].message.content/refusal/tool_calls`、`finish_reason`、顶层 `usage`、`error` 响应。冻结完整 OpenAI 响应可离线归一化。

测试：`test_a12_openai_compatible_frozen_response_is_normalized`。

Ollama 原生协议未实现，未宣称覆盖。

## 5. 测试

```powershell
pytest test_ai_inference_contracts.py test_ai_selective_review.py `
       test_ai_execution_trace.py test_ocr_replay_engine.py `
       test_ocr_field_evaluation.py -q
# 95 passed
```

分项：

- DL01 契约：26 passed
- DL02：7 passed
- DL03：5 passed
- H01 replay：40 passed
- 原 OCR 字段评分：17 passed

无 skip/xfail；无真实网络/数据库。

## 6. H01 归档前置门槛

重新通过：归档来源/策略/manifest 校验先于导入，原始来源保留。H01 40 项通过。

## 7. 协议范围

- 已覆盖：自定义 mock 格式、OpenAI-compatible `choices/message/usage/refusal/tool_calls/finish_reason/error`；
- 未覆盖：Ollama 原生、真实 HTTP/SSE、流式 tool call、真实代理、生产解析器入口。

## 8. 已知限制

- attempt 登记是进程内，不抗重启；DL-R1B 做持久保护；
- 费用预算只记录已知费用，无强制费用上限；
- 真实供应商能力/usage/价格未联调；
- mock 成功不等于真实模型效果；
- 未验证生产 RLS/权限/队列。

## 9. 门槛

允许进入 DL-R1B。  
不得据此宣称真实模型验证、生产权限验收、C01 通过或自动发布授权。
